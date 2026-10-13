"""Read-only recheck of the named October 13 diagnostic batches.

Does not simulate. Receipt verification is performed separately by the
maintenance checker. Rejects missing, ambiguous or inconsistent packet data.
"""
import argparse
import collections
import gzip
import hashlib
import json
from pathlib import Path


def one(root, pattern):
    files = list(root.glob(pattern))
    if len(files) != 1:
        raise ValueError(f"expected one {pattern} under {root}, got {len(files)}")
    return files[0]


def rows(path):
    with gzip.open(path, "rt") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def outcome(root):
    result = json.loads(gzip.decompress(one(root, 'cal/*/arms/*/result.json.gz').read_bytes()))
    manifest = json.loads((root / 'run-manifest.json').read_text())
    receipt = json.loads((root / 'run-receipt.json').read_text())
    if manifest['status'] != 'completed' or manifest['exit_code'] != 0:
        raise ValueError(f"failed run: {root}")
    if result['error'] or result['interrupted'] or not result['natural_end']:
        raise ValueError(f"incomplete result: {root}")
    emitted = {}
    for event in result['packet_events']:
        if event['kind'] == 'packet_emitted':
            pid = int(event['pid'])
            if pid in emitted:
                raise ValueError('duplicate emission')
            emitted[pid] = event['at']
    trace_path = one(root, 'cal/*/trace.jsonl.gz')
    trace = list(rows(trace_path))
    expected = {int(r['packet_id']): r['emit_time_s'] for r in trace}
    if len(expected) != len(trace) or expected != emitted or not emitted:
        raise ValueError('trace and emission ledger differ or empty')
    stop = result['stop_time_s']
    if stop < max(emitted.values()) + 4:
        raise ValueError('D4 observation window not mature')
    deliveries = result['deliveries']
    if not set(map(int, deliveries)).issubset(emitted):
        raise ValueError('unknown delivered PID')
    delays = {}
    for pid, item in deliveries.items():
        at = item['delivered_at']
        if not emitted[int(pid)] <= at <= stop:
            raise ValueError('invalid delivered time')
        delays[int(pid)] = at - emitted[int(pid)]
    failed = sum(pid not in delays or delays[pid] > 4 for pid in emitted)
    fates = result['fates']
    if set(map(int, fates)) != set(emitted):
        raise ValueError('fate population differs')
    if {int(p) for p, f in fates.items() if f == 'DELIVERED'} != set(delays):
        raise ValueError('delivery and fate ledger differ')
    counts = collections.Counter(fates.values())
    if any(result['fate_counts'].get(k) != v for k, v in counts.items()):
        raise ValueError('fate count mismatch')
    canonical_packets = [[pid, emitted[pid], deliveries.get(str(pid)), fates[str(pid)]] for pid in sorted(emitted)]
    config = json.loads(one(root, 'cal/*/arms/*/config.json').read_text())['resolved_config']
    summary = json.loads(one(root, 'cal/*/summary.json').read_text())
    if summary['arms'][0]['d4']['deadline_not_delivered_count'] != failed:
        raise ValueError('driver D4 differs')
    return {
        'run_id': root.name, 'receipt_sha256': receipt['receipt_sha256'],
        'source_git_commit': manifest['source_git_commit'],
        'remote_backup': manifest.get('remote_backup', {}).get('status'),
        'trace_gzip_sha256': hashlib.sha256(trace_path.read_bytes()).hexdigest(),
        'offered': len(emitted), 'delivered': len(delays),
        'deadline_miss_count': failed, 'deadline_miss_ratio': failed / len(emitted),
        'delivered_only_mean_delay_s': sum(delays.values()) / len(delays) if delays else None,
        'capped_delay_D4': sum(min(delays.get(p, 4), 4) / 4 for p in emitted) / len(emitted),
        'packet_outcome_sha256': hashlib.sha256(json.dumps(canonical_packets, sort_keys=True).encode()).hexdigest(),
        'fates': dict(counts), 'queue_area_bits_s': result['queue_area_bits_s'],
        'packet_event_kind_counts': dict(collections.Counter(e['kind'] for e in result['packet_events'])),
        'holding_queue_entries': sum(e['kind'] == 'queue_enter' and e.get('queue') == 'holding' for e in result['packet_events']),
        'policy': config['routing']['policy'], 'time_alignment_enabled': config['time_alignment']['enabled'],
        'stop_time_s': stop, 'last_emission_s': max(emitted.values()),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--evidence-root', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--inspect-holds', action='append', default=[])
    args = p.parse_args()
    prefixes = ('t1-load-cal-', 't1-core-', 't1-age-', 't1-stress-', 't1-oracle-')
    runs = sorted(d for d in args.evidence_root.iterdir() if d.is_dir()
                  and d.name.startswith(prefixes) and '-20261013-' in d.name)
    result = {'schema': 'independent-batch-audit/v1', 'claimable': False,
              'scope': 'packet outcomes, maturity, trace, fates; not causal attribution or receipt hashing',
              'run_count': len(runs), 'runs': [outcome(d) for d in runs]}
    result['hold_audits'] = {}
    for name in args.inspect_holds:
        root = args.evidence_root / name
        if root not in runs:
            raise ValueError('hold audit must refer to an included run')
        statuses, reasons, generated = collections.Counter(), collections.Counter(), collections.Counter()
        no_room = 0
        max_queue = 0
        first, last = None, None
        for r in rows(one(root, 'cal/*/arms/*/timeline.jsonl.gz')):
            if r.get('milestone') == 'ctrl_advert_generated':
                generated[(r['at'], bool(r.get('serve_cells')))] += 1
            if r.get('milestone') != 'frozen_inferred_hold':
                continue
            obs = r['observation_at_start']
            audit = obs['four_direction_audit']
            statuses[audit['route_status']] += 1
            for c in audit['candidate_details'].values():
                reasons.update(c.get('filter_reasons', []))
                no_room += c.get('queue_room') is False
                max_queue = max(max_queue, c.get('queue_bits_before', 0))
            at = audit['t_observed']
            first = at if first is None else min(first, at)
            last = at if last is None else max(last, at)
        result['hold_audits'][name] = {
            'hold_route_status_counts': dict(statuses), 'candidate_filter_reason_counts': dict(reasons),
            'candidate_queue_room_false_count': no_room, 'max_candidate_queued_bits': max_queue,
            'first_hold_observation': first, 'last_hold_observation': last,
            'generated_advertisements': [{'at': at, 'nonempty_serve_cells': nonempty, 'count': n}
                                         for (at, nonempty), n in sorted(generated.items())],
        }
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    for r in result['runs']:
        print(r['run_id'], r['offered'], r['delivered'], r['deadline_miss_count'], round(r['delivered_only_mean_delay_s'] or 0, 6))


if __name__ == '__main__':
    main()
