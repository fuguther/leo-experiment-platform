"""Production writer/reader fixtures, without invoking the simulator."""
import json
import hashlib
import pytest
from CODE.experiment_platform import replay_codec, t1_suite, t1_tasks


def test_network_result_round_trips_all_events_and_hashes_encoded_file(tmp_path):
    observation = {'队列': [1, 3, 2], 'generation': 4}
    row = {'pid': 1, 'observation': observation, 'at': 0.5}
    result = {'schema': t1_tasks.SCHEMA_TASK, 'task': 'network_alignment',
              'document': {'arms': [{'arm': arm, 'replay': {'captured': True,
                  'timeline_rows': [row] * 30, 'decision_rows': [row]}}
                  for arm in t1_tasks.NETWORK_ARMS]}}
    path = tmp_path / 'result.json'
    t1_tasks.publish(result, path)
    assert json.loads(path.read_text())['schema'] == replay_codec.SCHEMA
    probe = t1_suite._inspect_result(path)
    assert probe['payload'] == json.loads(json.dumps(result))
    assert probe['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    arms = probe['payload']['document']['arms']
    assert arms[0]['replay']['timeline_rows'][0] is arms[3]['replay']['decision_rows'][0]


def test_corrupted_graph_fails_closed_in_production_reader(tmp_path):
    path = tmp_path / 'result.json'
    path.write_text(json.dumps({'schema': replay_codec.SCHEMA,
                               'root': {'$t1ref': 10}, 'nodes': []}))
    probe = t1_suite._inspect_result(path)
    assert probe['payload'] is None and probe['parse_error']


def test_failed_graph_write_leaves_no_primary_and_preserves_other_file(tmp_path, monkeypatch):
    existing = tmp_path / 'old.json'
    existing.write_text('original')
    def fail(value, stream):
        stream.write('partial')
        raise OSError('injected graph failure')
    monkeypatch.setattr(replay_codec, 'dump_graph', fail)
    with pytest.raises(OSError, match='injected'):
        t1_tasks.publish({'schema': t1_tasks.SCHEMA_TASK, 'task': 'network_alignment'}, tmp_path / 'result.json')
    assert not (tmp_path / 'result.json').exists()
    assert existing.read_text() == 'original'


def test_graph_probe_uses_production_roundtrip_on_a_tiny_unit_fixture(tmp_path):
    from CODE.experiment_platform.publication_probe import run_graph_probe
    report = run_graph_probe(tmp_path / 'probe', references=3)
    assert report['equal'] and report['simulator_calls'] == 0
    assert report['expanded_standard_json_bytes'] > report['outputbytes']


def test_network_capture_preserves_complete_history_and_resource_evidence(monkeypatch):
    """The production aggregation path must never discard capture evidence."""
    import copy
    history = [{'generated_at': i, 'received_at': i + .1,
                'advertised_processing': {'queue': i},
                'advertised_downlink_resources': {'ground': {'queue_bits': i}}}
               for i in range(40)]
    replay = {'captured': True, 'decision_rows': [
        {'pid': 1, 't': 42., 'truth_at_commit': {'queue': 4},
         'observation_at_start': {'neighbours': {'2': {'advertised_history': history}}}}],
        'timeline_rows': [{'milestone': 'queue_state', 'at': 42}],
        'link_service_windows': [{'start': 42, 'end': 43}],
        'link_available_windows': [{'start': 0, 'end': 44}],
        'topology_trace': [{'at': 0}], 'handover_events': [{'at': 1}],
        'queue_state_events': [{'milestone': 'queue_state', 'at': 42}],
        'packet_events': [], 'fates': {}, 'deliveries': {}}
    original = copy.deepcopy(replay)
    monkeypatch.setattr(t1_tasks, '_arm_row', lambda *a, **k:
                        {'arm': a[3], 'replay': copy.deepcopy(replay)})
    monkeypatch.setattr(t1_tasks.artifact_identity, 'build_identity', lambda **k: {})
    result = t1_tasks.network_alignment(
        {'config': {'scenario': {'seed': 7}, 'time_alignment': {'predictor': 'hold_last'}},
         'sha256': 'fixture'}, [], None, {}, capture_replay=True)
    assert result['status'] == 'ok'
    for arm in result['arms']:
        for key, value in original.items():
            assert arm['replay'][key] == value, key
