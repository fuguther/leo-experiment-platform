"""Immutable-demand adapter for the retained Gateway runtime.

This module deliberately contains no simulation process logic.  It validates
the exact V2 trace and projects fixed geographic cells onto the active legacy
Gateway set.  SimulationRL owns the actual Gateway -> satellite -> ISL ->
Gateway processes, so the comparison arm cannot bypass its real data path.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any, Sequence

try:  # SimulationRL executes from CODE; package CLI imports through CODE.*
    from leo_sim.grid import grid_center
    from leo_sim.trace import TraceError, load_trace
except ModuleNotFoundError:  # pragma: no cover - exercised by package-mode tests
    from CODE.leo_sim.grid import grid_center
    from CODE.leo_sim.trace import TraceError, load_trace


class LegacyTraceError(ValueError):
    """A trace cannot be represented honestly by the retained runtime."""


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2
    return 2.0 * radius_km * math.asin(math.sqrt(a))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _nearest_gateway(grid_id: str, gateways: Sequence[Any]) -> tuple[Any, float]:
    lat, lon = grid_center(grid_id)
    ranked = [
        (
            _haversine_km(lat, lon, float(gateway.latitude), float(gateway.longitude)),
            index,
            gateway,
        )
        for index, gateway in enumerate(gateways)
    ]
    distance_km, _index, gateway = min(ranked, key=lambda item: (item[0], item[1]))
    return gateway, distance_km


def load_and_project_trace(
    path: str | Path,
    gateways: Sequence[Any],
    *,
    horizon_s: float,
    expected_sha256: str,
    max_packets: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate and project one immutable V2 trace onto active Gateways.

    Projection is deterministic (nearest great-circle distance, then active
    Gateway order for exact ties).  A source/destination collision is rejected:
    treating it as an instant local delivery would give the legacy arm an
    unearned advantage.  Deadlines are also rejected because the retained
    Gateway transport has no equivalent deadline fate.
    """
    trace_path = Path(path).expanduser().resolve()
    if not trace_path.is_file():
        raise LegacyTraceError(f"traffic trace is not a regular file: {trace_path}")
    expected = str(expected_sha256).strip().lower()
    if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
        raise LegacyTraceError("expected trace SHA-256 must be 64 lowercase hex characters")
    actual = _sha256(trace_path)
    if actual != expected:
        raise LegacyTraceError(
            f"traffic trace SHA-256 mismatch: expected {expected}, actual {actual}")
    if len(gateways) < 2:
        raise LegacyTraceError("trace comparison requires at least two active Gateways")

    try:
        rows = load_trace(
            str(trace_path),
            horizon_s=float(horizon_s),
            max_packets=int(max_packets),
        )
    except (OSError, TraceError, ValueError) as exc:
        raise LegacyTraceError(f"invalid immutable traffic trace: {exc}") from exc

    cells = sorted({row["src_grid_id"] for row in rows} | {row["dst_grid_id"] for row in rows})
    projection: dict[str, dict[str, Any]] = {}
    mapped: dict[str, Any] = {}
    for cell in cells:
        gateway, distance_km = _nearest_gateway(cell, gateways)
        mapped[cell] = gateway
        projection[cell] = {
            "gateway": str(gateway.name),
            "gateway_active_index": int(getattr(gateway, "active_index", gateways.index(gateway))),
            "distance_km": round(float(distance_km), 9),
        }

    projected: list[dict[str, Any]] = []
    for row in rows:
        source = mapped[row["src_grid_id"]]
        destination = mapped[row["dst_grid_id"]]
        if source is destination:
            raise LegacyTraceError(
                f"trace packet {row['packet_id']} source/destination project to the same "
                f"active Gateway {source.name!r}; add active Gateways or use a compatible trace")
        if row["deadline_at_s"] is not None:
            raise LegacyTraceError(
                "retained Gateway runtime does not implement packet deadlines; "
                f"trace packet {row['packet_id']} has deadline_at_s={row['deadline_at_s']}")
        projected.append({
            **row,
            "source_gateway": source,
            "destination_gateway": destination,
        })

    manifest = {
        "schema": "leo-legacy-trace-projection/v1",
        "trace_path": str(trace_path),
        "trace_sha256": actual,
        "horizon_s": float(horizon_s),
        "offered_packets": len(projected),
        "offered_bits": sum(int(row["bits"]) for row in projected),
        "projection_policy": "nearest_active_gateway_haversine_then_active_order",
        "same_gateway_policy": "fail_closed",
        "deadline_policy": "unsupported_fail_closed",
        "projection": projection,
    }
    return projected, manifest
