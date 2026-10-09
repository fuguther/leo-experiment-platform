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
