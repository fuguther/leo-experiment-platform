#!/usr/bin/env bash
set -u
W="/data/论文/leo-direct-sim"
echo "--- .deployment_commit ---"; cat "$W/.deployment_commit" 2>&1
echo "--- deployment.json identity ---"
python3 - <<'PY'
import json
d = json.load(open("/data/论文/leo-direct-sim/.remote_runtime/deployment.json"))
for k in ("schema_version","source_git_commit","source_git_branch","source_git_dirty",
          "source_tree_sha256","remote_workspace_dir","remote_code_dir","deployed_at"):
    print(f"  {k} = {d.get(k)}")
names = sorted({r["path"].split("/")[0] for r in d.get("deployed_files", [])})
print("  deployed top-level entries:", names)
print("  deployed file count:", len(d.get("deployed_files", [])))
PY
echo "--- top-level entries present on the VM but absent locally ---"
for n in LITERATURE PAPER NOTES.md DECISIONS.md AGENTS.md LICENSE; do
  if [ -e "$W/$n" ]; then echo "  PRESENT  $n"; else echo "  MISSING  $n"; fi
done
echo "--- does the VM CODE match this repo's CODE at any of our commits? ---"
echo "  VM CODE/leo_sim/*.py count: $(ls "$W/CODE/leo_sim"/*.py 2>/dev/null | wc -l)"
echo "  VM has pullback.py: $(test -f "$W/CODE/leo_sim/pullback.py" && echo yes || echo no)"
echo "  VM has recompute.py: $(test -f "$W/CODE/leo_sim/recompute.py" && echo yes || echo no)"
echo "  VM has lametrics_independent.py: $(test -f "$W/CODE/leo_sim/metrics_independent.py" && echo yes || echo no)"
echo "--- VM EXPERIMENTS count ---"; ls "$W/EXPERIMENTS" | wc -l
echo "--- VM backends/tools needed by run-remote ---"
which rsync; which tmux; df -h /data | tail -1
