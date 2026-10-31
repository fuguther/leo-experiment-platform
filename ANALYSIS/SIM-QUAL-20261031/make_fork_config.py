#!/usr/bin/env python3
"""Extract the resolved config of one evidence cell into a standalone config
file that the counterfactual driver can load (pinned to the same release)."""
import argparse, json
from pathlib import Path

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("cell_dir")   # <run>/<cell>
    ap.add_argument("--out", required=True)
    args=ap.parse_args()
    cfg=json.loads((Path(args.cell_dir)/"arms"/"now"/"config.json").read_text())
    resolved=cfg["resolved_config"]
    Path(args.out).write_text(json.dumps(resolved, indent=1, sort_keys=True)+"\n",
                              encoding="utf-8")
    print(json.dumps({"out": args.out, "sha256": cfg["sha256"],
                      "demand_csv": resolved.get("demand",{}).get("csv_path"),
                      "policy": resolved["time_alignment"]["state_policy"],
                      "arm": resolved["time_alignment"]["arm"]}, indent=1))

if __name__=="__main__":
    main()
