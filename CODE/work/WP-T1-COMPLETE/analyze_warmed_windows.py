"""Where exactly do the four arms diverge, window by window?"""
from __future__ import annotations
import gzip, json, sys
from pathlib import Path

ARMS = ("stale", "now", "common", "candidate")
BASE = Path("/data/论文/leo-t1-wt/runs")
WINDOWS = (("warmup", 0.0, 12.0), ("cohort", 12.0, 24.0), ("tail", 24.0, 28.0))


def rows(p):
    with gzip.open(p, "rt", encoding="utf-8") as s:
        for line in s:
            if line.strip():
                yield json.loads(line)


def one(root, pat):
    got = list(root.glob(pat))
    if len(got) != 1:
        raise ValueError(pat)
    return got[0]


def load_arm(root):
    dec = {}
    own = {name: 0 for name, _, _ in WINDOWS}
    for row in rows(one(root, "cal/*/arms/*/decisions.jsonl.gz")):
        if row.get("kind") != "forward":
            continue
        obs = row["observation_at_start"]
        ta = obs.get("time_alignment") or {}
        t = float(row["t_decision_start"])
        for name, lo, hi in WINDOWS:
            if lo <= t < hi:
                own[name] += 1
        dec[(row["sat"], row["t_decision_start"])] = {
            "legal": tuple(obs.get("legal_directions") or ()),
            "chosen": row.get("chosen"),
            "scores": ta.get("scores") or {},
        }
    return {"decisions": dec, "own": own}


spec = dict(a.split("=", 1) for a in sys.argv[1:])
arms = {a: load_arm(BASE / spec[a]) for a in ARMS}
print(json.dumps({"own_forward_decisions_per_window": {a: arms[a]["own"] for a in ARMS}},
                 ensure_ascii=False, indent=1))
for name, lo, hi in WINDOWS:
    keys = set(arms[ARMS[0]]["decisions"])
    for a in ARMS[1:]:
        keys &= set(arms[a]["decisions"])
    comparable, div, cvs, cvc, flips = 0, 0, 0, 0, []
    legal_hist = {}
    for k in sorted(keys):
        if not (lo <= float(k[1]) < hi):
            continue
        e = [arms[a]["decisions"][k] for a in ARMS]
        legal = e[0]["legal"]
        if len(legal) < 2 or any(x["legal"] != legal for x in e):
            continue
        if any(not set(legal).issubset(x["scores"]) for x in e):
            continue
        comparable += 1
        legal_hist[len(legal)] = legal_hist.get(len(legal), 0) + 1
        pick = {a: x["chosen"] for a, x in zip(ARMS, e)}
        if len(set(pick.values())) > 1:
            div += 1
            if len(flips) < 8:
                flips.append({"sat": k[0], "t": round(float(k[1]), 4), "chosen": pick})
        if pick["candidate"] != pick["now"]:
            cvs += 1
        if pick["candidate"] != pick["common"]:
            cvc += 1
    print(json.dumps({
        "window": name, "range_s": [lo, hi],
        "comparable_decisions": comparable,
        "legal_set_size_histogram": legal_hist,
        "four_arm_chosen_divergence": div,
        "candidate_vs_now": cvs, "candidate_vs_common": cvc,
        "samples": flips,
    }, ensure_ascii=False, indent=1))
