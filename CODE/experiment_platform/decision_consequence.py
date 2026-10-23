#!/usr/bin/env python3
"""decision_consequence.py — 预测误差 → 决策翻转 → 业务后果，一条可复算的链。

用途
----
把一次双臂 run 里的三份证据接起来：

1. 原始 run（`t1-lossless-json-graph`，用 `experiment_platform.replay_codec.read_document`
   解码）：两臂 `routing_audit_log.decision_records` 给出每个决策的 `chosen`，
   `network_outcome.packet_outcomes` 给出每个包的送达/时延/deadline 损失。
2. 同 run 的真值探针（`*-target-probe-{arm}.jsonl`，`leo-sim-target-probe-decision/v1`）：
   给出某个 (决策, 方向) 的 `predicted_bits` 与同一资源同一 `eval_target` 的
   `truth.work_ahead_bits`，因此 `|error|` 是同一 run 内的真误差，不是代理。
3. （可选）f58 的 summary：只用于把该 run 的宏观业务指标并排列出，做跨 run 方向校核。

口径（刻意写死，避免"看起来对"）
--------------------------------
* 跨臂对齐键：`(pid, hop_index)`。`decision_id` 是本 run 自增计数器，跨臂不可比。
* 翻转（flip）：同一 `(pid, hop_index)` 在 A / B 两臂的 `chosen` 不同。
  审计记录是被 `blake2b_16_of_(pid,hop_index)` 稳定抽样的（两臂抽同一批 key），
  所以配对样本对"两臂都出现的决策"是无偏的；只在单臂出现的决策**无法对齐**，
  单独计数并写进 `unalignable`，不冒充翻转。
* 误差：`|predicted_bits - truth.work_ahead_bits|`（bit）。
  一个决策的探针可能只覆盖一个方向；脚本记录 `direction_relevant`
  （该方向是否恰好是两臂 chosen 之一）与误差来源臂，不隐藏覆盖偏差。
* 业务差：`common − stale`（B − A）。
  - `delay_delta_s`：仅在两臂都 `DELIVERED` 时定义（否则为 survivors 偏差，不外推）；
  - `pp_delta`：`deadline_loss.value` 之差（连续损失，0..1 的 min(delay,D)/D 口径）；
  - `miss_delta`：deadline miss 指示变量之差（有 D 用 delay<=D，无 D 退回 fate==DELIVERED）。
* 桶：`|error|=0` 单独一桶（本数据里占 39%，若按秩四分位会把大量 0 拆进不同桶，
  是假分桶）；其余按**正值误差**的 p50/p75 切三档，等价于"零桶 + 正值四分位"。
  同时输出按秩等分的四分位桶 `rank_quartiles` 供对照。

用法
----
    python3 decision_consequence.py \
        --probe-a stale.jsonl --probe-b common.jsonl \
        --raw run.json --code-root CODE --out out.json

    # 两臂各一个 run 文件时（f58 sprint 的存法）：
    python3 decision_consequence.py --raw-a F58_s7_stale.json --raw-b F58_s7_common.json ...

    # 已抽出 compact 缓存（不想每次解码 223MB 时）：
    python3 decision_consequence.py --audit-extract extract.json ...

    # 只跑合成自测：
    python3 decision_consequence.py --self-test

`--out -` 时 JSON 走 stdout、可读表走 stderr，便于 `ssh vm 'python3 - ...'` 管道使用。
纯标准库；不写 VM、不改输入文件。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
import sys

SCHEMA = "decision-consequence/v1"
DIRECTIONS = ("N", "E", "S", "W")


# --------------------------------------------------------------------------- io
def _load_replay_document(path, code_root):
    """Decode a lossless replay document through the platform codec."""
    if code_root:
        code_root = os.path.abspath(code_root)
        if code_root not in sys.path:
            sys.path.insert(0, code_root)
    try:
        from experiment_platform.replay_codec import read_document  # noqa: WPS433
    except ImportError:
        read_document = None
    if read_document is not None:
        doc = read_document(path)
        if isinstance(doc, dict) and "document" in doc and "arms" in doc["document"]:
            return doc["document"]
        if isinstance(doc, dict) and "arms" in doc:
            return doc
        return doc
    with open(path, "r", encoding="utf-8") as fh:  # plain JSON fallback
        doc = json.load(fh)
    return doc.get("document", doc)


def _iter_arms(document):
    arms = document.get("arms")
    if arms is None:  # single-arm document
        arms = [document]
    out = {}
    for arm in arms:
        name = arm.get("arm") or arm.get("resolved_arm")
        if name:
            out[name] = arm
    return out


def _extract_arm(arm):
    """Compact, JSON-able view of one arm's decisions and packet outcomes."""
    decisions = []
    for rec in (arm.get("routing_audit_log") or {}).get("decision_records") or []:
        details = ((rec.get("four_direction_audit") or {})
                   .get("candidate_details") or {})
        pred = {}
        for direction, detail in details.items():
            score = (detail or {}).get("candidate_score") or {}
            pred[direction] = {
                "predicted_bits": score.get("predicted_bits"),
                "total_s": score.get("total_s"),
                "fallback": score.get("fallback"),
                "resource_id": (detail or {}).get("resource_id"),
            }
        decisions.append({
            "pid": rec.get("pid"),
            "hop_index": rec.get("hop_index"),
            "decision_id": rec.get("decision_id"),
            "chosen": rec.get("chosen"),
            "kind": rec.get("kind"),
            "t_commit_s": rec.get("t_decision_commit"),
            "candidates": rec.get("candidates"),
            "pred": pred,
        })
    packets = []
    for row in ((arm.get("network_outcome") or {}).get("packet_outcomes") or []):
        loss = row.get("deadline_loss") or {}
        packets.append({
            "pid": row.get("pid"),
            "fate": row.get("fate"),
            "emit_time_s": row.get("emit_time_s"),
            "delivery_time_s": row.get("delivery_time_s"),
            "observed_delay_s": loss.get("observed_delay_s"),
            "deadline_loss": loss.get("value"),
            "deadline_s": loss.get("deadline_s"),
            "terminal_reason": row.get("terminal_reason"),
            "censor_reason": row.get("censor_reason"),
        })
    audit = {k: v for k, v in (arm.get("routing_audit_log") or {}).items()
             if k not in ("attempt_records", "decision_records")}
    return {
        "arm": arm.get("arm"),
        "seed": arm.get("seed"),
        "config_sha256": arm.get("config_sha256"),
        "scope": arm.get("scope"),
        "probe_summary": ((arm.get("target_probe") or {}).get("summary") or {}),
        "outcome": arm.get("outcome") or {},
        "deadline_primary_loss": (arm.get("outcome") or {}).get("deadline_primary_loss") or {},
        "delivery_ratio": (arm.get("outcome") or {}).get("delivery_ratio") or {},
        "audit_meta": audit,
        "decisions": decisions,
        "packets": packets,
    }


def _normalize_extract(blob):
    """Accept either this script's extract or a leaner cache (hop/pb/ts/obs/dl/D)."""
    arms = blob["arms"] if "arms" in blob else blob
    for name, arm in arms.items():
        arm.setdefault("arm", name)
        fixed = []
        for dec in arm.get("decisions") or []:
            dec = dict(dec)
            dec["hop_index"] = dec.get("hop_index", dec.get("hop"))
            pred = {}
            for direction, value in (dec.get("pred") or {}).items():
                value = dict(value or {})
                value["predicted_bits"] = value.get("predicted_bits", value.get("pb"))
                value["total_s"] = value.get("total_s", value.get("ts"))
                pred[direction] = value
            dec["pred"] = pred
            fixed.append(dec)
        arm["decisions"] = fixed
        packets = []
        for row in arm.get("packets") or []:
            row = dict(row)
            row["observed_delay_s"] = row.get("observed_delay_s", row.get("obs"))
            row["deadline_loss"] = row.get("deadline_loss", row.get("dl"))
            row["deadline_s"] = row.get("deadline_s", row.get("D"))
            packets.append(row)
        arm["packets"] = packets
        arm.setdefault("outcome", {})
        arm.setdefault("deadline_primary_loss", {})
        arm.setdefault("delivery_ratio", {})
        arm.setdefault("audit_meta", {})
    return arms


def load_audit(args):
    """Return {arm_name: extracted arm} from --raw / --raw-a/--raw-b / --audit-extract."""
    if args.audit_extract:
        with open(args.audit_extract, "r", encoding="utf-8") as fh:
            blob = json.load(fh)
        return _normalize_extract(blob)
    arms = {}
    if args.raw:
        doc = _load_replay_document(args.raw, args.code_root)
        arms.update({name: _extract_arm(a) for name, a in _iter_arms(doc).items()})
    if args.raw_a and args.raw_b:
        for name, path in ((args.arm_a, args.raw_a), (args.arm_b, args.raw_b)):
            doc = _load_replay_document(path, args.code_root)
            found = _iter_arms(doc)
            if not found and isinstance(doc, dict):
                found = {name: doc}
            if name in found:
                arms[name] = _extract_arm(found[name])
            else:
                only = list(found.values())[0]
                arms[name] = _extract_arm(only)
                arms[name]["arm"] = name
    if not arms:
        raise SystemExit("no raw input: give --raw or --raw-a/--raw-b")
    return arms


def load_probe(path):
    rows = {}
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            rows[(row.get("pid"), row.get("hop_index"))] = row
    return rows


# ------------------------------------------------------------------ statistics
def wilson(k, n, z=1.96):
    if n <= 0:
        return (None, None)
    p = k / n
    denom = 1.0 + z * z / n
    centre = p + z * z / (2.0 * n)
    half = z * math.sqrt(max(0.0, p * (1.0 - p) / n + z * z / (4.0 * n * n)))
    return (max(0.0, (centre - half) / denom), min(1.0, (centre + half) / denom))


def bootstrap_mean_ci(values, seed=20261024, draws=4000, alpha=0.05):
    if len(values) < 3:
        return (None, None)
    rnd = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(draws):
        means.append(statistics.mean(values[rnd.randrange(n)] for _ in range(n)))
    means.sort()
    lo = means[int((alpha / 2.0) * draws)]
    hi = means[int((1.0 - alpha / 2.0) * draws) - 1]
    return (lo, hi)


def quartiles(values):
    if not values:
        return []
    data = sorted(values)
    qs = []
    for frac in (0.25, 0.50, 0.75):
        idx = min(len(data) - 1, max(0, int(round(frac * (len(data) - 1)))))
        qs.append(data[idx])
    return qs


# -------------------------------------------------------------------- analysis
def build_decisions(audit, arm_a, arm_b):
    da = {(d["pid"], d["hop_index"]): d for d in audit[arm_a]["decisions"]}
    db = {(d["pid"], d["hop_index"]): d for d in audit[arm_b]["decisions"]}
    return da, db


def build_packets(audit):
    return {name: {p["pid"]: p for p in arm["packets"]}
            for name, arm in audit.items()}


def packet_business(pid, packets, arm_a, arm_b):
    """common−stale deltas for one packet; None where undefined."""
    pa = packets[arm_a].get(pid)
    pb = packets[arm_b].get(pid)
    out = {"delay_delta_s": None, "pp_delta": None, "miss_delta": None,
           "fate_a": None if pa is None else pa["fate"],
           "fate_b": None if pb is None else pb["fate"]}
    if pa is None or pb is None:
        return out
    if pa["fate"] == "DELIVERED" and pb["fate"] == "DELIVERED":
        da, db_ = pa["observed_delay_s"], pb["observed_delay_s"]
        if da is None and pa["delivery_time_s"] is not None and pa["emit_time_s"] is not None:
            da = pa["delivery_time_s"] - pa["emit_time_s"]
        if db_ is None and pb["delivery_time_s"] is not None and pb["emit_time_s"] is not None:
            db_ = pb["delivery_time_s"] - pb["emit_time_s"]
        if da is not None and db_ is not None:
            out["delay_delta_s"] = db_ - da
    if pa["deadline_loss"] is not None and pb["deadline_loss"] is not None:
        out["pp_delta"] = pb["deadline_loss"] - pa["deadline_loss"]
    ma = packet_miss(pa)
    mb = packet_miss(pb)
    if ma is not None and mb is not None:
        out["miss_delta"] = mb - ma
    return out


def packet_miss(packet):
    if packet is None:
        return None
    if packet["fate"] != "DELIVERED":
        return 1
    delay = packet["observed_delay_s"]
    if delay is None and packet["delivery_time_s"] is not None and packet["emit_time_s"] is not None:
        delay = packet["delivery_time_s"] - packet["emit_time_s"]
    deadline = packet["deadline_s"]
    if delay is not None and deadline is not None:
        return 0 if delay <= deadline else 1
    return 0


def probe_error(row):
    """(abs error bits, signed error, source) or (None, None, reason)."""
    if row is None:
        return None, None, "no_probe_row"
    truth = row.get("truth") or {}
    pred = row.get("predicted_bits")
    if pred is None:
        return None, None, row.get("missing_reason") or "no_prediction"
    if truth.get("work_ahead_bits") is None:
        return None, None, row.get("missing_reason") or "no_truth"
    signed = float(pred) - float(truth["work_ahead_bits"])
    return abs(signed), signed, "ok"


def summarize_values(values):
    if not values:
        return {"n": 0, "mean": None, "median": None, "mean_ci": [None, None]}
    lo, hi = bootstrap_mean_ci(values)
    return {"n": len(values), "mean": statistics.mean(values),
            "median": statistics.median(values), "mean_ci": [lo, hi]}


def aggregate_business(decision_keys, flip_of, packets, arm_a, arm_b):
    delay, pp, miss = [], [], []
    unpaired = 0
    for key in decision_keys:
        biz = packet_business(key[0], packets, arm_a, arm_b)
        if biz["delay_delta_s"] is not None:
            delay.append(biz["delay_delta_s"])
        else:
            unpaired += 1
        if biz["pp_delta"] is not None:
            pp.append(biz["pp_delta"])
        if biz["miss_delta"] is not None:
            miss.append(biz["miss_delta"])
    return {
        "n_decisions": len(decision_keys),
        "n_delay_pairs": len(delay),
        "n_not_both_delivered": unpaired,
        "delay_delta_s": summarize_values(delay),
        "pp_delta": summarize_values(pp),
        "miss_delta": summarize_values(miss),
    }


def bucket_rows(joint, flip_of, error_of, packets, arm_a, arm_b, zero_bucket=True):
    """|error| 桶 → 翻转率 + 业务差。零桶单独，正值按 p50/p75 切。"""
    errors = [error_of(k)[0] for k in joint]
    positive = sorted(e for e in errors if e is not None and e > 0)
    cuts = quartiles(positive)
    p50 = cuts[1] if len(cuts) == 3 else (positive[len(positive) // 2] if positive else 0.0)
    p75 = cuts[2] if len(cuts) == 3 else (positive[-1] if positive else 0.0)

    def bucket_of(value):
        if value is None:
            return None
        if value == 0 and zero_bucket:
            return 0
        if value <= p50:
            return 1
        if value <= p75:
            return 2
        return 3

    labels = {0: "|e|=0", 1: "(0,p50_pos]", 2: "(p50_pos,p75_pos]", 3: "(p75_pos,inf)"}
    if not zero_bucket:
        labels = {1: "Q1 (0,p50]", 2: "Q2 (p50,p75]", 3: "Q3 (p75,inf)"}
    grouped = {}
    for key in joint:
        value = error_of(key)[0]
        bucket = bucket_of(value)
        if bucket is None:
            continue
        grouped.setdefault(bucket, []).append(key)
    rows = []
    for bucket in sorted(grouped):
        keys = grouped[bucket]
        n = len(keys)
        n_flip = sum(1 for k in keys if flip_of(k))
        lo, hi = wilson(n_flip, n)
        biz = aggregate_business(keys, flip_of, packets, arm_a, arm_b)
        rows.append({
            "bucket": labels[bucket],
            "bucket_id": bucket,
            "error_min_bits": (0.0 if bucket == 0 else
                               (min(error_of(k)[0] for k in keys))),
            "error_max_bits": max(error_of(k)[0] for k in keys),
            "n_decisions": n,
            "n_flipped": n_flip,
            "flip_rate": (n_flip / n) if n else None,
            "flip_rate_ci95": [lo, hi],
            "business": biz,
        })
    return rows, {"p50_positive_bits": p50, "p75_positive_bits": p75,
                  "n_positive": len(positive), "n_zero": sum(1 for e in errors if e == 0),
                  "n_total": len(errors), "zero_bucket": zero_bucket}


def rank_quartile_rows(joint, flip_of, error_of, packets, arm_a, arm_b):
    """纯按秩等分四分位（对 0 过多时会把 0 拆开，仅作对照）。"""
    ordered = sorted(joint, key=lambda k: error_of(k)[0])
    n = len(ordered)
    rows = []
    for idx in range(4):
        lo = idx * n // 4
        hi = (idx + 1) * n // 4
        keys = ordered[lo:hi]
        if not keys:
            continue
        n_flip = sum(1 for k in keys if flip_of(k))
        wlo, whi = wilson(n_flip, len(keys))
        rows.append({
            "bucket": f"rank Q{idx + 1} (p{idx * 25}-{(idx + 1) * 25}%)",
            "n_decisions": len(keys),
            "n_flipped": n_flip,
            "flip_rate": n_flip / len(keys),
            "flip_rate_ci95": [wlo, whi],
            "error_min_bits": error_of(keys[0])[0],
            "error_max_bits": error_of(keys[-1])[0],
            "business": aggregate_business(keys, flip_of, packets, arm_a, arm_b),
        })
    return rows


def score_margin(decision):
    """Relative gap between the two cheapest candidate scores (margin)."""
    scores = [v["total_s"] for v in (decision.get("pred") or {}).values()
              if v.get("total_s") is not None]
    if len(scores) < 2:
        return None
    scores.sort()
    if scores[1] <= 0:
        return None
    return (scores[1] - scores[0]) / scores[1]


def margin_rows(da, db, shared, flips):
    margins = {}
    for key in shared:
        ma, mb = score_margin(da[key]), score_margin(db[key])
        if ma is not None and mb is not None:
            margins[key] = min(ma, mb)
    rows = []
    ms = sorted(margins.values())
    if not ms:
        return rows, margins
    cuts = quartiles(ms)
    edges = [0.0, cuts[0], cuts[1], cuts[2], float("inf")]
    for idx in range(4):
        low, high = edges[idx], edges[idx + 1]
        keys = [k for k, m in margins.items()
                if (low <= m <= high if idx == 3 else low <= m < high)]
        if not keys:
            continue
        n_flip = sum(1 for k in keys if k in flips)
        lo, hi = wilson(n_flip, len(keys))
        rows.append({
            "bucket": f"margin Q{idx + 1}",
            "margin_low": low, "margin_high": (None if high == float("inf") else high),
            "n_decisions": len(keys), "n_flipped": n_flip,
            "flip_rate": n_flip / len(keys), "flip_rate_ci95": [lo, hi],
        })
    return rows, margins


def macro_block(audit, arm_a, arm_b):
    def pick(arm):
        outcome = audit[arm].get("outcome") or {}
        dl = audit[arm].get("deadline_primary_loss") or {}
        miss = (dl.get("miss_pp") or {})
        ratio = audit[arm].get("delivery_ratio") or {}
        return {
            "offered": outcome.get("offered"),
            "delivered": outcome.get("delivered"),
            "e2e_mean_s": outcome.get("e2e_mean_s"),
            "e2e_p95_s": outcome.get("e2e_p95_s"),
            "deadline_loss_mean": dl.get("exact_mean", dl.get("lower_mean")),
            "miss_pp": miss.get("value"),
            "miss_packets": miss.get("misses"),
            "delivery_ratio": (ratio.get("by_packets") if isinstance(ratio, dict) else ratio),
        }
    left, right = pick(arm_a), pick(arm_b)
    delta = {}
    for field in ("e2e_mean_s", "e2e_p95_s", "miss_pp", "delivery_ratio", "delivered"):
        a, b = left.get(field), right.get(field)
        delta[field] = (b - a) if isinstance(a, (int, float)) and isinstance(b, (int, float)) else None
    return {"arm_a": left, "arm_b": right, "delta_b_minus_a": delta}


def analyse(audit, arm_a, arm_b, probe_a=None, probe_b=None, args=None):
    if arm_a not in audit or arm_b not in audit:
        raise SystemExit(f"arms not found: {sorted(audit)}")
    da, db = build_decisions(audit, arm_a, arm_b)
    packets = build_packets(audit)
    shared = set(da) & set(db)
    only_a = set(da) - set(db)
    only_b = set(db) - set(da)
    flips = {key for key in shared if da[key]["chosen"] != db[key]["chosen"]}
    n_shared = len(shared)
    lo, hi = wilson(len(flips), n_shared)
    flip_kind = {}
    for key in flips:
        kind = da[key].get("kind") or "unknown"
        flip_kind[kind] = flip_kind.get(kind, 0) + 1

    audit_block = {
        "n_decisions_a": len(da), "n_decisions_b": len(db),
        "n_paired": n_shared, "n_unalignable_a_only": len(only_a),
        "n_unalignable_b_only": len(only_b),
        "n_flipped": len(flips),
        "flip_rate": (len(flips) / n_shared) if n_shared else None,
        "flip_rate_ci95": [lo, hi],
        "flip_kind_counts": flip_kind,
        "coverage": {arm: {k: (audit[arm].get("audit_meta") or {}).get(k)
                           for k in ("decision_record_count", "decision_records_retained",
                                     "decision_records_cap", "decision_coverage_fraction",
                                     "cross_arm_key", "decision_sampling_rule")}
                     for arm in (arm_a, arm_b)},
    }

    result = {"schema": SCHEMA, "audit": audit_block,
              "inputs": {"arm_a": arm_a, "arm_b": arm_b}}

    if not (probe_a and probe_b):
        result["probe"] = {"available": False,
                           "note": "no probe given: flip and business layers only"}
        result["margin"] = dict(zip(("rows", "margins"), margin_rows(da, db, shared, flips)))
        result["margin"] = {"rows": result["margin"]["rows"]}
        result["macro"] = macro_block(audit, arm_a, arm_b)
        result["flip_business"] = {
            "flipped": aggregate_business(sorted(flips), None, packets, arm_a, arm_b),
            "not_flipped": aggregate_business(sorted(shared - flips), None, packets, arm_a, arm_b),
        }
        return result

    def err_for(key):
        if key in probe_a and key in probe_b:
            e1, s1, _ = probe_error(probe_a[key])
            e2, s2, _ = probe_error(probe_b[key])
            if e1 is not None and e2 is not None:
                return (e1 + e2) / 2.0, "both_arms_mean", None, None
            if e1 is not None:
                return e1, arm_a, probe_a[key].get("direction"), s1
            if e2 is not None:
                return e2, arm_b, probe_b[key].get("direction"), s2
            return None, None, None, None
        for arm, probe in ((arm_a, probe_a), (arm_b, probe_b)):
            if key in probe:
                value, signed, _ = probe_error(probe[key])
                return value, arm, probe[key].get("direction"), signed
        return None, None, None, None

    def chosen_pair(key):
        if key in shared:
            return {da[key]["chosen"], db[key]["chosen"]}
        if key in probe_a and key in probe_b:
            return {probe_a[key]["chosen"], probe_b[key]["chosen"]}
        return set()

    def flip_of(key):
        if key in shared:
            return key in flips
        return (key in probe_a and key in probe_b
                and probe_a[key]["chosen"] != probe_b[key]["chosen"])

    # 联合总体：既有翻转状态、又有探针误差
    joint, excluded = [], {"no_probe": 0, "no_error": 0}
    for key in sorted(set(shared) | (set(probe_a) & set(probe_b))):
        has_status = (key in shared) or (key in probe_a and key in probe_b)
        if not has_status:
            continue
        value, source_arm, direction, signed = err_for(key)
        if value is None:
            excluded["no_error"] += 1
            continue
        joint.append(key)
    relevance = {"relevant": 0, "irrelevant": 0, "unknown": 0}
    sources = {}
    for key in joint:
        value, source_arm, direction, signed = err_for(key)
        sources[source_arm] = sources.get(source_arm, 0) + 1
        chosen = chosen_pair(key)
        if direction is None or not chosen:
            relevance["unknown"] += 1
        elif direction in chosen:
            relevance["relevant"] += 1
        else:
            relevance["irrelevant"] += 1
    joint_flips = sum(1 for k in joint if flip_of(k))
    jlo, jhi = wilson(joint_flips, len(joint))

    error_values = sorted(err_for(k)[0] for k in joint)
    bucket_primary, bucket_meta = bucket_rows(joint, flip_of, err_for,
                                              packets, arm_a, arm_b)
    rank_rows = rank_quartile_rows(joint, flip_of, err_for, packets, arm_a, arm_b)

    per_arm_error = {}
    for arm, probe in ((arm_a, probe_a), (arm_b, probe_b)):
        values, signed_values = [], []
        for row in probe.values():
            value, signed, _ = probe_error(row)
            if value is not None:
                values.append(value)
                signed_values.append(signed)
        per_arm_error[arm] = {
            "n": len(values),
            "mae_bits": statistics.mean(values) if values else None,
            "bias_bits": statistics.mean(signed_values) if signed_values else None,
            "median_bits": statistics.median(values) if values else None,
            "zero_share": (sum(1 for v in values if v == 0) / len(values)) if values else None,
        }

    result["probe"] = {
        "available": True,
        "per_arm_error": per_arm_error,
        "n_rows_a": len(probe_a), "n_rows_b": len(probe_b),
        "n_decisions_probe_a": len(probe_a), "n_decisions_probe_b": len(probe_b),
        "n_paired_probe": len(set(probe_a) & set(probe_b)),
        "n_probe_pairs_flipped": sum(
            1 for k in (set(probe_a) & set(probe_b))
            if probe_a[k]["chosen"] != probe_b[k]["chosen"]),
        "probe_pair_overlap_with_audit": len((set(probe_a) & set(probe_b)) & shared),
        "error_sources": sources,
        "direction_relevance": relevance,
        "excluded": excluded,
        "error_summary": {
            "n": len(error_values),
            "mae_bits": statistics.mean(error_values) if error_values else None,
            "median_bits": statistics.median(error_values) if error_values else None,
            "p25_bits": quartiles(error_values)[0] if error_values else None,
            "p75_bits": quartiles(error_values)[2] if error_values else None,
            "zero_share": (sum(1 for v in error_values if v == 0) / len(error_values)
                           if error_values else None),
        },
    }
    result["joint"] = {
        "n_decisions": len(joint), "n_flipped": joint_flips,
        "flip_rate": (joint_flips / len(joint)) if joint else None,
        "flip_rate_ci95": [jlo, jhi],
        "note": ("联合总体 = 有翻转状态（审计配对或探针配对）且有探针误差的决策；"
                 "两个抽样器几乎不相交，故它远小于审计配对总体"),
    }
    result["error_buckets"] = {"definition": bucket_meta, "rows": bucket_primary,
                               "rank_quartiles": rank_rows}
    result["flip_business"] = {
        "flipped": aggregate_business(sorted(k for k in joint if flip_of(k)),
                                      flip_of, packets, arm_a, arm_b),
        "not_flipped": aggregate_business(sorted(k for k in joint if not flip_of(k)),
                                          flip_of, packets, arm_a, arm_b),
    }
    # 全探针（不要求翻转状态）：误差桶 → 该包在两臂的业务差，样本更大
    all_probe = []
    for arm, probe in ((arm_a, probe_a), (arm_b, probe_b)):
        for key, row in probe.items():
            value, signed, _ = probe_error(row)
            if value is None:
                continue
            all_probe.append((value, key[0], arm))
    all_probe.sort()
    all_errors = [v for v, _, _ in all_probe]
    pos = sorted(v for v in all_errors if v > 0)
    cuts = quartiles(pos)
    p50 = cuts[1] if len(cuts) == 3 else (pos[len(pos) // 2] if pos else 0.0)
    p75 = cuts[2] if len(cuts) == 3 else (pos[-1] if pos else 0.0)
    all_rows = []
    groups = {0: [], 1: [], 2: [], 3: []}
    for value, pid, arm in all_probe:
        if value == 0:
            groups[0].append((pid, arm))
        elif value <= p50:
            groups[1].append((pid, arm))
        elif value <= p75:
            groups[2].append((pid, arm))
        else:
            groups[3].append((pid, arm))
    # 每个包只取一次（误差桶按该包所见最大误差分配，避免同包多次计权）
    pid_best = {}
    for value, pid, arm in all_probe:
        prev = pid_best.get(pid)
        if prev is None or value > prev[0]:
            pid_best[pid] = (value, arm)
    labels = {0: "|e|=0", 1: "(0,p50_pos]", 2: "(p50_pos,p75_pos]", 3: "(p75_pos,inf)"}
    for bucket in sorted(groups):
        pids = [pid for pid, (value, arm) in pid_best.items()
                if (value == 0 and bucket == 0)
                or (bucket == 1 and 0 < value <= p50)
                or (bucket == 2 and p50 < value <= p75)
                or (bucket == 3 and value > p75)]
        biz = aggregate_business([(pid, 0) for pid in pids], None, packets, arm_a, arm_b)
        all_rows.append({"bucket": labels[bucket], "n_packets": len(pids),
                         "business": biz})
    result["all_probe_business"] = {"definition": {"p50_positive_bits": p50,
                                                   "p75_positive_bits": p75,
                                                   "dedupe": "each packet once, by max observed |error|"},
                                    "rows": all_rows}
    margin, _margins = margin_rows(da, db, shared, flips)
    result["margin"] = {
        "n_decisions": len(_margins),
        "definition": "margin = (2nd cheapest total_s - cheapest total_s)/2nd cheapest total_s; min of both arms",
        "rows": margin,
    }
    result["macro"] = macro_block(audit, arm_a, arm_b)
    return result


# ----------------------------------------------------------------- printing
def _fmt(value, digits=3):
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _fmt_int(value):
    if value is None:
        return "-"
    return f"{int(value):,}"


def print_table(result):
    lines = []
    audit = result["audit"]
    lines.append("== 1. 跨臂决策对齐（键 = (pid, hop_index)） ==")
    lines.append(f"  配对决策 n={_fmt_int(audit['n_paired'])}（A {_fmt_int(audit['n_decisions_a'])} / "
                 f"B {_fmt_int(audit['n_decisions_b'])}，单臂不可对齐 "
                 f"{_fmt_int(audit['n_unalignable_a_only'] + audit['n_unalignable_b_only'])}）")
    lines.append(f"  翻转 n={_fmt_int(audit['n_flipped'])}  flip_rate={_fmt(audit['flip_rate'], 4)} "
                 f"95%CI=[{_fmt(audit['flip_rate_ci95'][0], 3)}, {_fmt(audit['flip_rate_ci95'][1], 3)}]")
    lines.append(f"  翻转让按 kind: {audit['flip_kind_counts']}")
    probe = result.get("probe") or {}
    if not probe.get("available"):
        lines.append("")
        lines.append("== 2/3. 探针未提供：跳过误差桶与误差→业务表 ==")
    else:
        lines.append("")
        lines.append("== 2. 探针：误差总体与联合可用性 ==")
        err = probe["error_summary"]
        lines.append(f"  探针行 A={probe['n_rows_a']} B={probe['n_rows_b']}，配对决策 "
                     f"{probe['n_paired_probe']}（其中翻转 {probe['n_probe_pairs_flipped']}）")
        lines.append(f"  |error| n={err['n']} MAE={_fmt(err['mae_bits'], 0)} bit "
                     f"median={_fmt(err['median_bits'], 0)} p75={_fmt(err['p75_bits'], 0)} "
                     f"零误差占比={_fmt(err['zero_share'], 3)}")
        joint = result["joint"]
        lines.append(f"  联合总体（有翻转状态且有误差）n={joint['n_decisions']} "
                     f"flips={joint['n_flipped']} rate={_fmt(joint['flip_rate'], 4)}")
        lines.append(f"  误差来源臂: {probe['error_sources']}；方向相关性: {probe['direction_relevance']}")
        lines.append("")
        lines.append("== 3. 误差桶 → 翻转率 → 业务差（common - stale） ==")
        header = ("  桶".ljust(22) + "n_dec".rjust(7) + "n_flip".rjust(7) + "flip_rate".rjust(10)
                  + "  CI95".ljust(18) + "n_delay".rjust(8) + "delay_ms".rjust(10)
                  + "pp_delta".rjust(10) + "miss_delta".rjust(11))
        lines.append(header)
        for row in result["error_buckets"]["rows"]:
            biz = row["business"]
            delay = biz["delay_delta_s"]
            lines.append(
                "  " + row["bucket"].ljust(20)
                + str(row["n_decisions"]).rjust(7)
                + str(row["n_flipped"]).rjust(7)
                + _fmt(row["flip_rate"], 3).rjust(10)
                + f"  [{_fmt(row['flip_rate_ci95'][0], 2)},{_fmt(row['flip_rate_ci95'][1], 2)}]".ljust(18)
                + str(delay["n"]).rjust(8)
                + (_fmt(1000 * delay["mean"], 3) if delay["mean"] is not None else "-").rjust(10)
                + (_fmt(biz["pp_delta"]["mean"], 5) if biz["pp_delta"]["mean"] is not None else "-").rjust(10)
                + (_fmt(biz["miss_delta"]["mean"], 4) if biz["miss_delta"]["mean"] is not None else "-").rjust(11))
        fb = result["flip_business"]
        lines.append(f"  翻转决策组: n={fb['flipped']['n_decisions']} "
                     f"delay_ms={_fmt(1000 * fb['flipped']['delay_delta_s']['mean'], 3)} "
                     f"(95%CI {_fmt(1000 * fb['flipped']['delay_delta_s']['mean_ci'][0], 2)}.."
                     f"{_fmt(1000 * fb['flipped']['delay_delta_s']['mean_ci'][1], 2)})")
        lines.append(f"  未翻转组：n={fb['not_flipped']['n_decisions']} "
                     f"delay_ms={_fmt(1000 * fb['not_flipped']['delay_delta_s']['mean'], 3)} "
                     f"(95%CI {_fmt(1000 * fb['not_flipped']['delay_delta_s']['mean_ci'][0], 2)}.."
                     f"{_fmt(1000 * fb['not_flipped']['delay_delta_s']['mean_ci'][1], 2)})")
        lines.append("")
        lines.append("== 4. 机制旁证：候选分差 margin → 翻转率（不需要探针） ==")
        for row in result["margin"]["rows"]:
            lines.append(f"  {row['bucket']} n={row['n_decisions']:4d} "
                         f"flips={row['n_flipped']:3d} rate={_fmt(row['flip_rate'], 3)}")
    macro = result.get("macro") or {}
    if macro:
        left, right, delta = macro["arm_a"], macro["arm_b"], macro["delta_b_minus_a"]
        lines.append("")
        lines.append("== 5. 宏观业务（该 run 的 outcome；括号内为 B-A） ==")
        lines.append(f"  delivered: {left['delivered']} vs {right['delivered']} "
                     f"({_fmt(delta['delivered'], 0)})")
        lines.append(f"  e2e_mean_ms: {_fmt(1000 * left['e2e_mean_s'], 2) if left['e2e_mean_s'] else '-'} vs "
                     f"{_fmt(1000 * right['e2e_mean_s'], 2) if right['e2e_mean_s'] else '-'} "
                     f"({_fmt(1000 * delta['e2e_mean_s'], 2) if delta['e2e_mean_s'] is not None else '-'})")
        lines.append(f"  e2e_p95_ms: {_fmt(1000 * left['e2e_p95_s'], 2) if left['e2e_p95_s'] else '-'} vs "
                     f"{_fmt(1000 * right['e2e_p95_s'], 2) if right['e2e_p95_s'] else '-'} "
                     f"({_fmt(1000 * delta['e2e_p95_s'], 2) if delta['e2e_p95_s'] is not None else '-'})")
        lines.append(f"  miss_pp: {_fmt(left['miss_pp'], 3)} vs {_fmt(right['miss_pp'], 3)} "
                     f"({_fmt(delta['miss_pp'], 3)})")
    return "\n".join(lines)


# ------------------------------------------------------------------ self-test
def _synthetic_case():
    """Two-arm synthetic run with known flips, known |error| and known delay deltas."""
    arms = {}
    deltas = {8: 0.010, 9: 0.010}          # only flipped packets pay +10 ms in arm B
    flips = {8, 9}                        # same decisions flip N -> S
    for arm in ("stale", "common"):
        decisions, packets = [], []
        for pid in range(10):
            chosen = ("S" if (pid in flips and arm == "common") else "N")
            decisions.append({
                "pid": pid, "hop_index": 0, "chosen": chosen, "kind": "forward",
                "t_commit_s": 0.1 + pid / 100.0,
                "pred": {"N": {"total_s": (1.0 if pid != 9 else 1.05)},
                         "S": {"total_s": 2.0}},
            })
            delta = deltas.get(pid, 0.0) if arm == "common" else 0.0
            packets.append({
                "pid": pid, "fate": "DELIVERED", "emit_time_s": 0.2,
                "delivery_time_s": 0.3 + delta, "observed_delay_s": 0.100 + delta,
                "deadline_loss": 0.5 + 10 * delta, "deadline_s": 0.2,
                "terminal_reason": None, "censor_reason": None,
            })
        arms[arm] = {
            "arm": arm, "seed": 1, "decisions": decisions, "packets": packets,
            "outcome": {"offered": 10, "delivered": 10,
                        "e2e_mean_s": 0.1 + (0.001 if arm == "common" else 0.0),
                        "e2e_p95_s": 0.3},
            "deadline_primary_loss": {"miss_pp": {"value": 0.0}},
            "delivery_ratio": {}, "audit_meta": {}, "probe_summary": {}, "scope": {},
            "config_sha256": None,
        }
    return arms


def _synthetic_probe():
    """Probe label per decision: zero error except 100k/200k/1M/2M for pids 6..9."""
    errors = {6: 100000.0, 7: 200000.0, 8: 1000000.0, 9: 2000000.0}
    truth = 100000.0
    probe_a, probe_b = {}, {}
    for pid in range(10):
        error = errors.get(pid, 0.0)
        row = {"pid": pid, "hop_index": 0, "chosen": "N", "direction": "N",
               "predicted_bits": truth + error,
               "truth": {"work_ahead_bits": truth},
               "eval_target_s": 0.5, "state_measured_at_s": 0.4,
               "state_used_at_s": 0.45, "selected": True,
               "fallback": False, "predictor": "ou_shrink"}
        probe_a[(pid, 0)] = dict(row)
        row_b = dict(row)
        row_b["chosen"] = "S" if pid in (8, 9) else "N"
        probe_b[(pid, 0)] = row_b
    return probe_a, probe_b


def self_test():
    failures = []
    audit = _synthetic_case()
    probe_a, probe_b = _synthetic_probe()
    result = analyse(audit, "stale", "common", probe_a, probe_b, None)

    # --- test 1: 对齐与翻转计数（键 = (pid, hop_index)） ---
    got = result["audit"]
    if got["n_paired"] != 10 or got["n_flipped"] != 2:
        failures.append(f"test1 pairing: paired={got['n_paired']} flips={got['n_flipped']} (expect 10/2)")
    joint = result["joint"]
    if joint["n_decisions"] != 10 or joint["n_flipped"] != 2:
        failures.append(f"test1 joint: n={joint['n_decisions']} flips={joint['n_flipped']} (expect 10/2)")

    # --- test 2: 误差桶方向 + 业务差符号 ---
    buckets = {row["bucket"]: row for row in result["error_buckets"]["rows"]}
    zero = buckets.get("|e|=0")
    high = [row for name, row in buckets.items() if name.startswith("(p75")]
    if zero is None or not high:
        failures.append(f"test2 buckets missing: got {sorted(buckets)}")
    else:
        if zero["flip_rate"] != 0.0:
            failures.append(f"test2: zero-error bucket flip_rate={zero['flip_rate']} (expect 0)")
        if high[0]["flip_rate"] != 1.0:
            failures.append(f"test2: high-error bucket flip_rate={high[0]['flip_rate']} (expect 1)")
    flipped = result["flip_business"]["flipped"]["delay_delta_s"]["mean"]
    if flipped is None or abs(flipped - 0.010) > 1e-9:
        failures.append(f"test2: flipped delay delta={flipped} (expect 0.01)")
    not_flipped = result["flip_business"]["not_flipped"]["delay_delta_s"]["mean"]
    if not_flipped is None or abs(not_flipped) > 1e-12:
        failures.append(f"test2: non-flipped delay delta={not_flipped} (expect 0.0)")

    if failures:
        for item in failures:
            print("SELF-TEST FAIL:", item)
        return 1
    print("SELF-TEST OK: 2/2 合成用例通过 "
          "(配对/翻转计数=10/2；零误差桶翻转率 0%；高误差桶翻转率 100%；翻转包时延差 +10 ms)")
    return 0


# ----------------------------------------------------------------------- main
def build_parser():
    parser = argparse.ArgumentParser(
        description="prediction error -> decision flip -> business consequence")
    parser.add_argument("--probe-a", help="target-probe jsonl for arm A")
    parser.add_argument("--probe-b", help="target-probe jsonl for arm B")
    parser.add_argument("--raw", help="lossless replay run json holding both arms")
    parser.add_argument("--raw-a", help="per-arm replay run json for arm A")
    parser.add_argument("--raw-b", help="per-arm replay run json for arm B")
    parser.add_argument("--audit-extract", help="pre-extracted compact audit json (skips --raw)")
    parser.add_argument("--code-root", default=os.environ.get("LEO_CODE_ROOT", ""),
                        help="directory containing experiment_platform/replay_codec.py")
    parser.add_argument("--arm-a", default="stale")
    parser.add_argument("--arm-b", default="common")
    parser.add_argument("--out", default="-",
                        help="JSON output path; '-' = stdout (table then goes to stderr)")
    parser.add_argument("--self-test", action="store_true",
                        help="run the two synthetic self-tests and exit")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.self_test:
        return self_test()
    audit = load_audit(args)
    probe_a = load_probe(args.probe_a) if args.probe_a else None
    probe_b = load_probe(args.probe_b) if args.probe_b else None
    if (probe_a is None) != (probe_b is None):
        raise SystemExit("give both --probe-a and --probe-b, or neither")
    result = analyse(audit, args.arm_a, args.arm_b, probe_a, probe_b, args)
    table = print_table(result)
    if args.out == "-":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        print(table, file=sys.stderr)
    else:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        print(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
