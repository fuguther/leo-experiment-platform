"""Publication regression fixtures; no simulator or scientific timing."""
import json
from pathlib import Path

import pytest
from CODE.experiment_platform import t1_tasks


def test_large_nested_unicode_document_is_encoded_in_bounded_pieces(tmp_path, monkeypatch):
    record = {"observation": {"name": "卫星😀", "history": list(range(20))},
              "events": [{"at": i / 10, "pid": i} for i in range(8)]}
    document = {"arms": [{"replay": {"timeline": [record] * 200}} for _ in range(4)],
                "empty": {}, "escaped": '"\\\n', "null": None}
    reference = json.dumps(document, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")) + "\n"
    encode = json.dumps
    calls = []

    def bounded_encode(value, **kwargs):
        encoded = encode(value, **kwargs)
        assert len(encoded) < 100_000, "encoder materialized an entire large subtree"
        calls.append(len(encoded))
        return encoded

    monkeypatch.setattr(t1_tasks.json, "dumps", bounded_encode)
    output = tmp_path / "result.json"
    t1_tasks.publish(document, output)
    assert output.read_text() == reference
    assert len(calls) > 1


def test_failed_piece_never_publishes_primary(tmp_path, monkeypatch):
    original = json.dumps
    calls = 0

    def fail_later(value, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 5:
            raise OSError("injected encoding interruption")
        return original(value, **kwargs)

    monkeypatch.setattr(t1_tasks.json, "dumps", fail_later)
    with pytest.raises(OSError, match="interruption"):
        t1_tasks.publish({"rows": [{"data": "测" * 400} for _ in range(100)]},
                         tmp_path / "result.json")
    assert not (tmp_path / "result.json").exists()


@pytest.mark.parametrize("document", [
    {"rows": [None, True, False, -2, 1.2, "星😀"] * 500},
    {str(i): {"x": [i] * 20} for i in range(300)},
    {"large_scalar": "星😀" * 50000},
])
def test_chunking_preserves_exact_standard_json_bytes(tmp_path, document):
    output = tmp_path / "result.json"
    t1_tasks.publish(document, output)
    assert output.read_text() == json.dumps(document, ensure_ascii=False,
        sort_keys=True, separators=(",", ":")) + "\n"


def test_cycle_fails_without_publishing(tmp_path):
    value = []
    value.append(value)
    with pytest.raises(ValueError, match="Circular"):
        t1_tasks.publish(value, tmp_path / "result.json")
    assert not (tmp_path / "result.json").exists()


def test_result_inspection_does_not_keep_whole_byte_buffer(tmp_path, monkeypatch):
    import hashlib
    from CODE.experiment_platform import t1_suite
    path = tmp_path / 'result.json'
    raw = b'{"schema":"fixture","records":[1,2,3]}'
    path.write_bytes(raw)
    def no_read_bytes(_path):
        raise AssertionError('whole byte buffer retained during JSON parsing')
    monkeypatch.setattr(Path, 'read_bytes', no_read_bytes)
    probe = t1_suite._inspect_result(path)
    assert probe['sha256'] == hashlib.sha256(raw).hexdigest()
    assert probe['payload']['records'] == [1, 2, 3]
    assert probe['parse_error'] is None


def test_result_changed_between_hash_and_parse_is_rejected(tmp_path, monkeypatch):
    from CODE.experiment_platform import t1_suite
    path = tmp_path / 'result.json'
    path.write_text('{"schema":"fixture","value":1}')
    original = json.load
    def change_then_load(stream):
        path.write_text('{"schema":"fixture","value":200}')
        return original(stream)
    monkeypatch.setattr(t1_suite.json, 'load', change_then_load)
    probe = t1_suite._inspect_result(path)
    assert probe['payload'] is None
    assert 'changed' in probe['parse_error']
