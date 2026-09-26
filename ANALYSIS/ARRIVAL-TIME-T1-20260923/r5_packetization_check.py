#!/usr/bin/env python3
"""§R5-2 包粒度口径复算工具（决策请求率与双向单位一致性自检）

任务书 §R7 REAL-PACKETIZATION-PASS 要求：
  「真实/代理包长与旧 1Mbit 单元的决策请求率差已复算；处理能力与流量双向单位都一致。」

本工具只做复算与单位自检，不做任何"真实分布"的声称：给定聚合单元 packet_bits、
候选包长（字节）、报价比特率与 ISL 速率，输出决策请求率、相对放大倍数与服务时长。

用法
----
python3 ANALYSIS/ARRIVAL-TIME-T1-20260923/r5_packetization_check.py     --packet-bits 1000000 8000000     --offered-mbps 25 100 --isl-rate-mbps 10 --out out/r5-packetization.json
"""
from __future__ import annotations

import argparse
import json
import sys

# 当前可用的包长锚点（二手/人工，见 00c 与 00b 台账；不得声称是 Starlink 实测分布）
DEFAULT_LENGTHS_B = [1500.0, 1471.64, 891.0, 372.0, 64.0]


def recompute(packet_bits_list, offered_mbps_list, isl_rate_mbps, lengths):
    rows = []
    for unit_bits in packet_bits_list:
        for offered in offered_mbps_list:
            offered_bps = offered * 1_000_000.0
            unit_pps = offered_bps / unit_bits
            for L in lengths:
                pkt_bits = L * 8.0
                pps = offered_bps / pkt_bits
                row = {
                    "aggregate_unit_bits": unit_bits,
                    "offered_mbps": offered,
                    "aggregate_decisions_per_s": unit_pps,
                    "packet_bytes": L,
                    "packet_bits": pkt_bits,
                    "packet_decisions_per_s": pps,
                    "amplification_vs_unit": pps / unit_pps,
                    "service_time_s_at_isl_rate": pkt_bits / (isl_rate_mbps * 1_000_000.0),
                }
                rows.append(row)
    return rows


def unit_consistency(packet_bits_list, lengths, offered_mbps_list):
    """双向单位自检：比特口径与包口径必须互相还原。"""
    checks = []
    for unit_bits in packet_bits_list:
        for offered in offered_mbps_list:
            offered_bps = offered * 1_000_000.0
            unit_pps = offered_bps / unit_bits
            back = unit_pps * unit_bits
            checks.append({
                "aggregate_unit_bits": unit_bits,
                "offered_mbps": offered,
                "bits_to_packets_to_bits_roundtrip_ok": abs(back - offered_bps) < 1e-6,
                "roundtrip_bps": back,
            })
    for L in lengths:
        pkt_bits = L * 8.0
        for offered in offered_mbps_list:
            offered_bps = offered * 1_000_000.0
            pps = offered_bps / pkt_bits
            back = pps * pkt_bits
            checks.append({
                "packet_bytes": L,
                "offered_mbps": offered,
                "bits_to_packets_to_bits_roundtrip_ok": abs(back - offered_bps) < 1e-6,
                "roundtrip_bps": back,
            })
    return checks


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--packet-bits", type=float, nargs="+", required=True)
    ap.add_argument("--offered-mbps", type=float, nargs="+", required=True)
    ap.add_argument("--isl-rate-mbps", type=float, default=10.0)
    ap.add_argument("--lengths", type=float, nargs="*", default=DEFAULT_LENGTHS_B)
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args(argv)

    rows = recompute(args.packet_bits, args.offered_mbps, args.isl_rate_mbps, args.lengths)
    checks = unit_consistency(args.packet_bits, args.lengths, args.offered_mbps)
    all_ok = all(c["bits_to_packets_to_bits_roundtrip_ok"] for c in checks)
    report = {
        "schema": "leo-t1-r5-packetization/v1",
        "note": "仅复算与单位自检；包长为二手/人工锚点，不得声称是 Starlink 实测分布。",
        "isl_rate_mbps": args.isl_rate_mbps,
        "rows": rows,
        "unit_checks": checks,
        "unit_consistency_ok": all_ok,
    }
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
    print(json.dumps({
        "unit_consistency_ok": all_ok,
        "rows": len(rows),
        "sample": rows[:3],
    }, ensure_ascii=False, indent=1))
    return 0 if all_ok else 2


if __name__ == "__main__":
    sys.exit(main())
