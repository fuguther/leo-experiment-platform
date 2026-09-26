set -u
W="/data/论文/leo-direct-sim"
echo "--- tmux ---"; which tmux && tmux -V || echo NO_TMUX
echo "--- deployed leo_sim CLI reachable ---"
cd "$W" && source /opt/anaconda3/bin/activate /data/liguang13/conda-envs/leo-i39 >/dev/null 2>&1
python3 -c "import sys; print(sys.version.split()[0])"
python3 -m CODE.leo_sim --help 2>&1 | head -3
echo "--- pulled-back R06 result dir contents (a real formal run) ---"
ls "$W/CODE/Results/EXP-20260923-T1-STEP5-MICRO-R06-control-s7" 2>&1 | head -20
echo "--- its receipt schema ---"
python3 -c "
import json
p='$W/CODE/Results/EXP-20260923-T1-STEP5-MICRO-R06-control-s7/receipt.json'
try:
    d=json.load(open(p)); print(d.get('schema'), 'keys', len(d))
except Exception as e: print('ERR', e)
"
echo "--- governance receipt present? ---"
test -f "$W/CODE/Results/EXP-20260923-T1-STEP5-MICRO-R06-control-s7/governance_receipt.json" && echo YES || echo NO
echo "--- external witness dir ---"
ls "$W/CODE/Results/_external_launch_witness" 2>&1 | head -5
