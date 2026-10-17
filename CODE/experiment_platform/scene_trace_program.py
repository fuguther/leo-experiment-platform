#!/usr/bin/env python3
"""Declared stochastic demand programs for the T1 state-time study.

The simulator's built-in demand modes can express a single GLOBAL burst window
(demand.mode=burst) or a per-longitude 24 h cosine, but not "each OD pair has
its own on/off process" and not "a few ODs share a hotspot while the rest stay
stationary".  Both are required by the study design, and both are exactly what
demand.mode=csv can carry.  This module turns a small, declared program into
that CSV.

WHAT IS DECLARED, AND WHAT IS ASSUMED
-------------------------------------
* The two-state envelope (a, b, p) with p*a + (1-p)*b == 1 is a MODEL.  It is
  the study's burstiness knob, not a measurement.  The module refuses a triple
  that violates the identity or that needs b < 0.
* Dwell times are set FROM p (high_dwell = p * cycle, low_dwell = (1-p) *
  cycle) so that "how often it bursts" and "how long it bursts" cannot drift
  apart; only the cycle length is free.
* Every model preserves the LONG-RUN MEAN: sum over ODs of w_k * R * (mean
  multiplier) == R.  A burst cell is therefore not secretly a higher-load cell.
* The generator is a pure function of (program, seed).  Two runs with the same
  program and seed produce byte-identical CSVs, and the CSV hash is recorded
  by the trace compiler as the input identity.

MODELS
------
S1 stationary            homogeneous Poisson per OD at w_k * R / L.
S2 independent_burst     each OD runs its OWN two-state process (independent
                         onsets), all with the same (a, b, p).
S3 correlated_hotspot    a declared SUBSET of ODs shares ONE two-state
                         process (simultaneous onsets); the rest stay
                         stationary.  Optionally the hot subset migrates to
                         the next group every migration_period_s.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import random
import zlib
from pathlib import Path

SCHEMA = "scene-trace-program/v1"
MODELS = ("s1_stationary", "s2_independent_burst", "s3_correlated_hotspot")


class TraceProgramError(RuntimeError):
    pass


def check_two_state(a: float, b: float, p: float, tol: float = 1e-6) -> None:
    """The declared identity, enforced rather than assumed.

    The tolerance covers contracts that declare a, b and p rounded to six
    decimals; it is far tighter than any effect a 1e-6 mean drift could have.
    """
    if not (0.0 <= p <= 1.0):
        raise TraceProgramError(f"p must be in [0,1], got {p!r}")
    if a <= 1.0:
        raise TraceProgramError(
            f"high-state multiplier a must exceed 1 to be a burst, got {a!r}")
    if b < 0.0:
        raise TraceProgramError(f"low-state multiplier b must be >= 0, got {b!r}")
    if abs(p * a + (1.0 - p) * b - 1.0) > tol:
        raise TraceProgramError(
            "two-state envelope must preserve the mean: "
            f"p*a+(1-p)*b = {p * a + (1.0 - p) * b!r}, expected 1")
    if p * a > 1.0 + tol:
        raise TraceProgramError(
            f"p*a = {p * a!r} > 1: the high state alone already exceeds the "
            "long-run mean, so b would have to be negative")


def _load(program: dict) -> dict:
    p = dict(program)
    if p.get("schema", SCHEMA) != SCHEMA:
        raise TraceProgramError(f"unsupported trace program schema {p.get('schema')!r}")
    model = p.get("model")
    if model not in MODELS:
        raise TraceProgramError(f"model must be one of {MODELS}, got {model!r}")
    for key in ("total_mbps", "packet_bits", "emission_start_s",
                "emission_end_s", "seed", "cells", "od_weights"):
        if key not in p:
            raise TraceProgramError(f"trace program is missing {key!r}")
    if float(p["emission_end_s"]) <= float(p["emission_start_s"]):
        raise TraceProgramError("emission window must be non-empty")
    weights = {k: float(v) for k, v in p["od_weights"].items()}
    if not weights or abs(sum(weights.values()) - 1.0) > 1e-6:
        raise TraceProgramError(
            f"od_weights must sum to 1, got {sum(weights.values())!r}")
    p["od_weights"] = weights
    if model != "s1_stationary":
        # The burst is declared by its DURATION and its CYCLE, not by p
        # directly: p is then DERIVED as burst_duration / cycle, so "how long
        # it bursts" and "how often it bursts" cannot drift apart.  An earlier
        # version accepted p and a cycle longer than the observation window,
        # and the realised high-state fraction then collapsed (0.86 of the
        # intended mean) because most ODs never burst inside the window at
        # all.  That failure is now a hard refusal.
        for key in ("a", "b", "burst_duration_s", "cycle_s"):
            if key not in p:
                raise TraceProgramError(f"{model} requires {key!r}")
        window = float(p["emission_end_s"]) - float(p["emission_start_s"])
        cycle = float(p["cycle_s"])
        tau = float(p["burst_duration_s"])
        if cycle <= 0 or tau <= 0:
            raise TraceProgramError("cycle_s and burst_duration_s must be positive")
        if tau >= cycle:
            raise TraceProgramError(
                f"burst_duration_s {tau} must be shorter than cycle_s {cycle}")
        if cycle > window / 3.0:
            raise TraceProgramError(
                f"cycle_s {cycle}s is too long for a {window}s window: at most "
                f"{window / 3.0:.3f}s keeps at least three burst cycles, and "
                "fewer makes the declared p unobservable (measured, not assumed)")
        p["p"] = tau / cycle
        check_two_state(float(p["a"]), float(p["b"]), float(p["p"]))
        p["p_source"] = "derived: burst_duration_s / cycle_s"
    if model == "s3_correlated_hotspot":
        hot = p.get("hot_ods")
        if not isinstance(hot, list) or not hot:
            raise TraceProgramError("s3 requires a non-empty hot_ods list")
        unknown = [k for k in hot if k not in weights]
        if unknown:
            raise TraceProgramError(f"s3 hot_ods not in od_weights: {unknown}")
        for group in p.get("migration_groups") or []:
            unknown = [k for k in group if k not in weights]
            if unknown:
                raise TraceProgramError(
                    f"s3 migration group not in od_weights: {unknown}")
    return p


def _two_state_intervals(gen: random.Random, start: float, end: float,
                         p: float, cycle_s: float):
    """Yield (t0, t1, is_high) for one independent two-state process."""
    high_dwell = p * cycle_s
    low_dwell = (1.0 - p) * cycle_s
    if high_dwell <= 0.0:            # never high
        yield (start, end, False)
        return
    if low_dwell <= 0.0:             # always high
        yield (start, end, True)
        return
    t = start
    high = gen.random() < p
    while t < end:
        dwell = high_dwell if high else low_dwell
        stop = min(end, t + dwell)
        yield (t, stop, high)
        t = stop
        high = not high


def generate(program: dict):
    """Return the CSV rows for one program. Pure function of (program, seed)."""
    p = _load(program)
    model = p["model"]
    L = int(p["packet_bits"])
    R = float(p["total_mbps"]) * 1e6
    t0, t1 = float(p["emission_start_s"]), float(p["emission_end_s"])
    seed = int(p["seed"])
    cells = p["cells"]
    weights = p["od_weights"]
    ods = sorted(weights)
    if model == "s1_stationary":
        mult = {od: 1.0 for od in ods}
    elif model == "s2_independent_burst":
        mult = {od: (float(p["a"]), float(p["b"]), float(p["p"])) for od in ods}
    else:
        hot = set(p["hot_ods"])
        groups = p.get("migration_groups") or []
        mult = {}
        for od in ods:
            mult[od] = ((float(p["a"]), float(p["b"]), float(p["p"]))
                        if od in hot else 1.0)
        if groups:
            raise TraceProgramError(
                "migration_groups is declared but the migration schedule is not "
                "implemented; refusing rather than silently ignoring it")

    rows = []
    pid = 0
    for od in ods:
        src_id, dst_id = od.split(",")
        s_lat, s_lon = cells[src_id]
        d_lat, d_lon = cells[dst_id]
        w = weights[od]
        base = w * R / L                       # packets/s at multiplier 1
        # STABLE per-OD seed.  tuple.__hash__ is randomised per process
        # (PYTHONHASHSEED), so an earlier version produced a DIFFERENT trace
        # for the same declared program on every run -- the file was cached
        # by program digest, so the first process won and nobody noticed.
        gen = random.Random(zlib.crc32(od.encode("utf-8")) ^ (seed * 0x9E3779B1))
        spec = mult[od]
        if spec == 1.0:
            intervals = [(t0, t1, False, 1.0)]
            peak = 1.0
        else:
            a, b, pp = spec
            intervals = [(s, e, h, a if h else b)
                         for (s, e, h) in _two_state_intervals(gen, t0, t1, pp,
                                                               float(p["cycle_s"]))]
            peak = a
        # thinning on a constant majorant keeps the process a true
        # non-homogeneous Poisson with the declared instantaneous rate
        t = t0
        majorant = base * peak
        if majorant > 0:
            while True:
                t += gen.expovariate(majorant)
                if t >= t1:
                    break
                rate = base
                for (s, e, _h, m) in intervals:
                    if s <= t < e:
                        rate = base * m
                        break
                if gen.random() < rate / majorant:
                    pid += 1
                    rows.append(dict(packet_id=pid, emit_time_s=t,
                                     src_lat=s_lat, src_lon=s_lon,
                                     dst_lat=d_lat, dst_lon=d_lon, bits=L))
    rows.sort(key=lambda r: (r["emit_time_s"], r["packet_id"]))
    for i, r in enumerate(rows, start=1):
        r["packet_id"] = i
    if len({r["packet_id"] for r in rows}) != len(rows):
        raise TraceProgramError("generated duplicate packet ids")
    return rows


FIELDS = ["packet_id", "emit_time_s", "src_lat", "src_lon", "dst_lat",
          "dst_lon", "bits"]


def write_csv(rows, path) -> dict:
    """Write atomically.

    An earlier version opened the destination directly.  When the batch
    launcher started several cells at once, two writers on the same
    content-addressed path interleaved their rows and produced lines with 12
    fields instead of 7 (observed: 'csv row 320: invalid endpoint
    coordinates').  Writing to a private temporary file and renaming makes the
    visible file either absent or complete, so a concurrent reader can never
    see a half-written trace.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", newline="", encoding="utf-8") as fh:
            wr = csv.DictWriter(fh, fieldnames=FIELDS)
            wr.writeheader()
            for r in rows:
                wr.writerow(r)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"path": str(path), "rows": len(rows), "sha256": digest}


def is_complete(path) -> bool:
    """A trace file is usable only if it starts with the declared header and
    ends on a row boundary."""
    path = Path(path)
    try:
        with path.open("rb") as fh:
            head = fh.readline().decode("utf-8").strip().split(",")
            if head != FIELDS:
                return False
            fh.seek(-1, os.SEEK_END)
            return fh.read(1) == b"\\n"
    except (OSError, UnicodeError):
        return False


def program_digest(program: dict) -> str:
    return hashlib.sha256(
        json.dumps(program, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
