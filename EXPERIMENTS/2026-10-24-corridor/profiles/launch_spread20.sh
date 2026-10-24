#!/bin/bash
set -u
D=/data/论文/leo-t1-wt/scratch/corridor-20261024
cd "$D" || exit 1
PY=/data/论文/leo-t1-wt/envs/t1-linux-aarch64-py311-pkgset-v1/bin/python
for i in $(seq 1 120); do
  n=$(ps -eo args | grep -c "[t]1_tasks")
  [ "$n" -eq 0 ] && break
  sleep 20
done
echo "spread20 start $(date +%H:%M)" >> logs/chain.log
{
  echo "spread20_s7.yaml stale spread20_s7_stale.json"
  echo "spread20_orc_s7.yaml now spread20_orc_s7_now.json"
  echo "spread20_s8.yaml stale spread20_s8_stale.json"
  echo "spread20_orc_s8.yaml now spread20_orc_s8_now.json"
} | while read prof arm out; do
  (T1_AUDIT_MAX_DECISIONS=2000 T1_OU_TAU_S=0.1 nohup $PY -B -m CODE.experiment_platform.t1_tasks --task network_alignment \
     --config "profiles/$prof" --arms "$arm" --deadline-s 0.4 \
     --out "runs/$out" > "logs/${out%.json}.log" 2>&1 &)
done
echo "spread20 launched $(date +%H:%M)" >> logs/chain.log
