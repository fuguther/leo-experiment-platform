import json, subprocess
from pathlib import Path
B = Path("/Users/lge/Desktop/topic/leo-experiment-platform")
d = json.loads((B/"CODE/work/WP-LEO-V2-T1-TRUST-CHAIN-V6/criteria.json").read_text())
head = subprocess.run(["git","rev-parse","HEAD"], cwd=B, capture_output=True, text=True).stdout.strip()
print("  work_id      :", d["work_id"])
print("  frozen_at_sha:", d["frozen_at_sha"][:16], "| 当前 HEAD:", head[:16],
      "| 一致" if d["frozen_at_sha"] == head else "| 不一致")
print("  判据条数     :", len(d["criteria"]), "(要求 8-20)")
missing = [c["id"] for c in d["criteria"] if not c.get("check")]
print("  缺 check 的  :", missing or "无")
print()
for c in d["criteria"]:
    print("    %-4s %s" % (c["id"], c["statement"][:60]))
