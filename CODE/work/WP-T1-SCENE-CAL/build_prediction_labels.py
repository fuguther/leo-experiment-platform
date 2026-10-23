"""Build prediction labels by reconstructing the resource's work-ahead at the
EVALUATION target instant, offline, from the run's own event log.

Why offline: the brief requires that truth sampling must not affect online
routing nor consume business link resources.  Reconstructing from the recorded
service windows satisfies both by construction.

Two instants are kept strictly separate:
  algo_query_target  the instant the method actually queried (query_targets[d]);
                     for hold-last this is the last source measurement, i.e. PAST
  eval_target        the single instant every method is scored at (= eta_targets[d],
                     the candidate's estimated arrival); errors are only comparable
                     on a common instant.

work_ahead(link, t) = SUM of remaining bits, over packets that had ALREADY
ENTERRED this queue at t and had NOT finished service at t.

A first version summed every service window with start > t, which counts all
future traffic on the link (17.9 Mbit against a 0 bit prediction) instead of the
backlog.  Arrival times come from queue_enter events; the service interval comes
from link_service_windows, both keyed by (link_id, pid) -- the ISL link_id text
is identical across queue_enter, service_start and the windows.
"""
import gzip, json, os, sys, bisect, collections

def load_windows(result_path):
    """Per ISL link: service intervals joined with the moment each packet entered
    the queue, so a backlog at any instant can be reconstructed."""
    with gzip.open(result_path) as fh:
        res = json.load(fh)
    entered = {}
    for e in res.get("packet_events") or []:
        if e.get("kind") == "queue_enter" and e.get("queue") == "isl":
            entered[(e.get("link_id"), e.get("pid"))] = float(e.get("at"))
    per = collections.defaultdict(list)
    for w in res.get("link_service_windows") or []:
        if w.get("stage") != "isl" or w.get("outcome") != "ok":
            continue
        key = (w.get("link_id"), w.get("pid"))
        arr = entered.get(key)
        if arr is None:
            continue
        per[w["link_id"]].append((arr, float(w["start"]), float(w["end"]), float(w["bits"])))
    for k in per:
        per[k].sort()
    return per

def work_ahead(win, t):
    """Bits queued on this link at instant t: arrived by t, not yet served by t.

    A packet counts when enter <= t.  If service already started it contributes
    only its remaining bits; if it has not started it contributes all of them.
    """
    total = 0.0
    for arr, s, e, b in win or ():
        if arr > t:
            continue
        if e <= t:
            continue
        if s <= t < e:
            total += b * ((e - t) / (e - s)) if e > s else 0.0
        else:
            total += b
    return total

def label_rows(cell_dir, arm, limit=None):
    dec_path = os.path.join(cell_dir, "arms", arm, "decisions.jsonl.gz")
    win = load_windows(os.path.join(cell_dir, "arms", arm, "result.json.gz"))
    rows = []
    n = 0
    with gzip.open(dec_path, "rt") as fh:
        for line in fh:
            d = json.loads(line); n += 1
            ta = d["observation_at_start"]["time_alignment"]
            us = ta.get("used_state") or {}
            qt = ta.get("query_targets") or {}
            et = ta.get("eta_targets") or {}
            cd = d["observation_at_start"]["four_direction_audit"]["candidate_details"]
            for direction, rec in cd.items():
                u = us.get(direction) or {}
                rid = u.get("resource_id")
                gen = u.get("generation")
                algo_t = qt.get(direction)
                eval_t = et.get(direction)
                terms = (rec.get("candidate_score") or {}).get("terms") or {}
                ws = terms.get("resource_work_s")
                rate = rec.get("rate_bps")
                missing = []
                if rid is None: missing.append("no_resource_id")
                if algo_t is None: missing.append("no_query_target")
                if eval_t is None: missing.append("no_eval_target")
                if ws is None or not rate: missing.append("no_predicted_work")
                pred_bits = None if (ws is None or not rate) else float(ws) * float(rate)
                lab = None
                if not missing and rid in win:
                    lab = work_ahead(win[rid], float(eval_t))
                elif not missing:
                    missing.append("link_absent_from_service_windows")
                rows.append(dict(pid=d.get("pid"), sat=d.get("sat"), t=d.get("t"),
                                 direction=direction, resource_id=rid, generation=gen,
                                 algo_query_target=algo_t, eval_target=eval_t,
                                 age_s=(None if (algo_t is None or d.get("t") is None)
                                        else float(d["t"]) - float(algo_t)),
                                 horizon_s=(None if (eval_t is None or d.get("t") is None)
                                            else float(eval_t) - float(d["t"])),
                                 predicted_bits=pred_bits, label_bits=lab,
                                 error_bits=(None if (pred_bits is None or lab is None)
                                             else pred_bits - lab),
                                 missing=";".join(missing)))
            if limit and len(rows) >= limit:
                break
    return rows

if __name__ == "__main__":
    cell, arm = sys.argv[1], sys.argv[2]
    lim = int(sys.argv[3]) if len(sys.argv) > 3 else None
    rows = label_rows(cell, arm, lim)
    ok = [r for r in rows if r["label_bits"] is not None]
    print(f"rows={len(rows)}  labelled={len(ok)}  coverage={100*len(ok)/max(1,len(rows)):.2f}%")
    mc = collections.Counter()
    for r in rows:
        for m in (r["missing"] or "").split(";"):
            if m: mc[m] += 1
    print("missing reasons:", dict(mc))
    if ok:
        import statistics
        errs = [abs(r["error_bits"]) for r in ok]
        print(f"|err| mean={statistics.fmean(errs):.1f} bits  n={len(ok)}")
        print("sample:", json.dumps(ok[0])[:400])
