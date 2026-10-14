"""Audit the predeclared [6,8) emission cohort, without simulating."""
import argparse
import collections
import gzip
import json
from pathlib import Path

from audit_result_batch import one, outcome, rows


def audit(root):
    whole = outcome(root)
    trace = list(rows(one(root, 'cal/*/trace.jsonl.gz')))
    cohort = {r['packet_id']: r['emit_time_s'] for r in trace if 6 <= r['emit_time_s'] < 8}
    if not cohort:
        raise ValueError('predeclared emission cohort is empty')
    result = json.loads(gzip.decompress(one(root, 'cal/*/arms/*/result.json.gz').read_bytes()))
    delays = {p: result['deliveries'][str(p)]['delivered_at'] - at for p, at in cohort.items()
              if str(p) in result['deliveries']}
    counts = collections.Counter()
    forward_pids, complete_pids, informative_pids = set(), set(), set()
    histories = collections.Counter()
    for r in rows(one(root, 'cal/*/arms/*/decisions.jsonl.gz')):
        if r['pid'] not in cohort or r['kind'] != 'forward':
            continue
        counts['forward_decisions'] += 1
        forward_pids.add(r['pid'])
        ta = r['observation_at_start']['time_alignment']
        legal = r['observation_at_start']['legal_directions']
        complete = [d for d, s in ta['scores'].items() if d in legal and s['missing'] == [] and s['total_s'] is not None]
        if len(complete) >= 2:
            counts['two_complete_candidate_decisions'] += 1
            complete_pids.add(r['pid'])
        if ta.get('fallback_directions'):
            counts['decisions_with_fallback'] += 1
        with_history = 0
        with_nonzero = 0
        for d in complete:
            n = ta.get('history_samples', {}).get(d, 0)
            histories[n] += 1
            if n >= 2:
                with_history += 1
            if ta['scores'][d]['terms']['resource_work_s'] > 0:
                with_nonzero += 1
        if len(complete) >= 2 and with_history >= 2 and with_nonzero:
            counts['two_complete_with_history_and_nonzero_work'] += 1
            informative_pids.add(r['pid'])
    hold_status = collections.Counter()
    hold_pids = collections.defaultdict(set)
    for r in rows(one(root, 'cal/*/arms/*/timeline.jsonl.gz')):
        if r.get('milestone') != 'frozen_inferred_hold' or r.get('pid') not in cohort:
            continue
        status = r['observation_at_start']['four_direction_audit']['route_status']
        hold_status[status] += 1
        hold_pids[status].add(r['pid'])
    return {'schema': 'warmup-cohort-audit/v1', 'whole_run': whole,
            'selection': 'all packets emitted at 6 <= t < 8; fixed before run; earlier traffic consumes real resources',
            'cohort_offered': len(cohort), 'cohort_delivered': len(delays),
            'cohort_deadline_miss_count': sum(p not in delays or delays[p] > 4 for p in cohort),
            'cohort_delivered_only_mean_delay_s': sum(delays.values()) / len(delays) if delays else None,
            'cohort_capped_delay_D4': sum(min(delays.get(p, 4), 4) / 4 for p in cohort) / len(cohort),
            'forward_pids': len(forward_pids), 'two_complete_pids': len(complete_pids),
            'informative_pids': len(informative_pids), 'decision_counts': dict(counts),
            'complete_candidate_history_sample_counts': dict(histories),
            'hold_status_counts': dict(hold_status), 'hold_status_pids': {k: len(v) for k, v in hold_pids.items()},
            'boundary': 'nonzero estimate and history count are exposure checks, not proof of predictive accuracy or benefit',
            'kernel_calls': 0, 'claimable': False}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--run-dir', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    result = audit(args.run_dir)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'whole_run'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
