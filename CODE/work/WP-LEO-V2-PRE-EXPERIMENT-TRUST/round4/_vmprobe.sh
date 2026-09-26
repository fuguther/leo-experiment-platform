#!/usr/bin/env bash
# read-only reconnaissance of the canonical VM workspace
set -u
W="/data/论文/leo-direct-sim"
echo "--- host ---"; hostname; uname -sr
echo "--- workspace ---"; ls -la "$W" 2>&1 | head -20
echo "--- CODE ---"; ls "$W/CODE" 2>&1 | head -20
echo "--- .remote_runtime ---"; ls -la "$W/.remote_runtime" 2>&1 | head -20
echo "--- deployment receipt ---"
if [ -f "$W/.remote_runtime/deployment.json" ]; then
  python3 - <<'PY'
import json
p = "/data/论文/leo-direct-sim/.remote_runtime/deployment.json"
d = json.load(open(p))
print(json.dumps({k: d.get(k) for k in
  ("schema","source_git_commit","source_tree_sha256","deployed_at","deployed_by")
  if k in d}, ensure_ascii=False, indent=2))
print("keys:", sorted(d))
PY
else
  echo "NO_DEPLOYMENT_RECEIPT"
fi
echo "--- launches ---"; ls "$W/.remote_runtime/launches" 2>&1 | head -5
echo "--- current_status ---"; ls -la "$W/.remote_runtime/current_status.json" 2>&1
echo "--- Results ---"; ls "$W/CODE/Results" 2>&1 | head -15
echo "--- conda env ---"; test -d /data/liguang13/conda-envs/leo-i39 && echo ENV_PRESENT || echo ENV_MISSING
/data/liguang13/conda-envs/leo-i39/bin/python3 -V 2>&1 | head -1
echo "--- deployed leo_sim identity ---"
if [ -f "$W/CODE/leo_sim/receipt.py" ]; then
  cd "$W" && /data/liguang13/conda-envs/leo-i39/bin/python3 -c "
import sys; sys.path.insert(0,'.')
try:
    from CODE.leo_sim.receipt import code_sha256
    print('deployed code_sha256:', code_sha256())
except Exception as exc:
    print('cannot compute:', exc)
"
else
  echo "NO_CODE"
fi
echo "--- git in workspace ---"; cd "$W" && git rev-parse HEAD 2>&1 | head -2
echo "--- disk ---"; df -h "$W" 2>&1 | tail -2
