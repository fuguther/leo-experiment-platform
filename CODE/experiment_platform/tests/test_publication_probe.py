"""Small deterministic tests for the pure publication probe."""
import hashlib
import json
from pathlib import Path

import pytest

from CODE.experiment_platform import publication_probe
from CODE.experiment_platform import t1_tasks


def _reference_row():
    shared_array = [[index, index * 2, "节点😀"] for index in range(36)]
    shared_observation = {
        "卫星": "星河-α-🚀",
        "邻居": [{"id": f"LEO-{index:02d}", "可见": index % 2 == 0}
                 for index in range(6)],
        "队列快照": shared_array,
        "嵌套": {"状态": "可转发", "轨迹": shared_array},
    }
    return {
        "决策": "最短预计时延",
        "观测": shared_observation,
        "候选": [
            {"下一跳": f"LEO-{index:02d}", "观测": shared_observation,
             "预测数组": shared_array}
            for index in range(4)
        ],
        "数组": {"历史": shared_array, "有效": [True, False, True]},
    }


@pytest.mark.parametrize("rows", [1, 2, 3])
def test_rows_publish_exact_standard_json_bytes_and_report(tmp_path, monkeypatch,
                                                           rows):
    ticks = iter([10.0, 10.25])
    monkeypatch.setattr(publication_probe.time, "perf_counter", lambda: next(ticks))
    monkeypatch.setattr(publication_probe, "_peak_rss", lambda: {
        "value": 1234, "unit": "KiB", "platform": "Linux"})
    monkeypatch.setattr(t1_tasks.kernel, "run_simulation",
                        lambda *args, **kwargs: pytest.fail("simulation called"))
    original_publish = t1_tasks.publish
    publish_calls = []

    def publish_spy(document, path):
        publish_calls.append((document, path))
        return original_publish(document, path)

    monkeypatch.setattr(t1_tasks, "publish", publish_spy)
    out_dir = tmp_path / f"new-{rows}"

    report = publication_probe.run_probe(out_dir, rows=rows)

    assert len(publish_calls) == 1
    published_document, published_path = publish_calls[0]
    assert published_path == out_dir / "fixture.json"
    assert len(published_document["rows"]) == rows
    assert all(item is published_document["rows"][0]
               for item in published_document["rows"])

    prefix = b'{"rows":['
    row = json.dumps(_reference_row(), ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    suffix = b'],"schema":"engineering-publication-probe/v1"}\n'
    expected = prefix + b",".join([row] * rows) + suffix
    output = (out_dir / "fixture.json").read_bytes()
    assert output == expected
    assert report["schema"] == "engineering-publication-probe/v1"
    assert report["not_scientific"] is True
    assert report["simulator_calls"] == 0
    assert report["rows"] == rows
    assert report["outputbytes"] == len(expected)
    assert report["sha_expected"] == hashlib.sha256(expected).hexdigest()
    assert report["sha_actual"] == report["sha_expected"]
    assert report["equal"] is True
    assert report["walltime_seconds"] == 0.25
    assert report["peak_rss"] == {
        "value": 1234, "unit": "KiB", "platform": "Linux"}
    assert report["source_driver_sha256"] == hashlib.sha256(
        Path(t1_tasks.__file__).read_bytes()).hexdigest()
    assert report["probe_sha256"] == hashlib.sha256(
        Path(publication_probe.__file__).read_bytes()).hexdigest()
    assert json.loads((out_dir / "report.json").read_text()) == report


@pytest.mark.parametrize("rows", [0, -1, 8193, 1.5, True])
def test_row_count_outside_fixed_bounds_is_rejected_before_output(tmp_path, rows):
    out_dir = tmp_path / "new"
    with pytest.raises(publication_probe.ProbeError):
        publication_probe.run_probe(out_dir, rows=rows)
    assert not out_dir.exists()


def test_row_count_bounds_are_inclusive_without_allocating_output():
    assert publication_probe._validate_rows(1) == 1
    assert publication_probe._validate_rows(8192) == 8192


def test_existing_output_path_and_symlinked_parent_are_rejected(tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    with pytest.raises(publication_probe.ProbeError, match="exists"):
        publication_probe.run_probe(old, rows=1)

    target = tmp_path / "target"
    target.mkdir()
    out_link = tmp_path / "out-link"
    out_link.symlink_to(target, target_is_directory=True)
    with pytest.raises(publication_probe.ProbeError, match="exists"):
        publication_probe.run_probe(out_link, rows=1)

    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(publication_probe.ProbeError, match="parent"):
        publication_probe.run_probe(link / "new", rows=1)


def test_hash_mismatch_is_reported_and_fails_without_removing_fixture(
        tmp_path, monkeypatch):
    monkeypatch.setattr(publication_probe, "_hash_file", lambda path: "0" * 64)
    out_dir = tmp_path / "new"

    assert publication_probe.main([
        "--out-dir", str(out_dir), "--rows", "1"]
    ) == 2

    report = json.loads((out_dir / "report.json").read_text())
    assert report["equal"] is False
    assert report["sha_actual"] == "0" * 64
    assert (out_dir / "fixture.json").is_file()


def test_wrong_production_writer_artifact_is_rejected(tmp_path, monkeypatch):
    def wrong_publish(document, path):
        Path(path).write_bytes(b'{"wrong":true}\n')

    monkeypatch.setattr(t1_tasks, "publish", wrong_publish)
    out_dir = tmp_path / "wrong-writer"

    with pytest.raises(publication_probe.ProbeError, match="hash mismatch"):
        publication_probe.run_probe(out_dir, rows=1)

    report = json.loads((out_dir / "report.json").read_text())
    assert report["equal"] is False
    assert (out_dir / "fixture.json").read_bytes() == b'{"wrong":true}\n'


def test_output_limit_uses_exact_utf8_json_byte_lengths():
    prefix, row, suffix = b"{\"x\":[", '"星😀"'.encode("utf-8"), b"]}\n"
    expected = len(prefix) + 3 * len(row) + 2 + len(suffix)
    assert publication_probe._output_size(prefix, row, suffix, 3) == expected
    assert publication_probe._output_size(prefix, row, suffix, 0) == len(prefix) + len(suffix)


def test_fixed_fixture_default_is_substantial_and_all_rows_fit_hard_cap():
    prefix = b'{"rows":['
    row = json.dumps(publication_probe._fixture_row(), ensure_ascii=False,
                     sort_keys=True, separators=(",", ":"),
                     allow_nan=False).encode("utf-8")
    suffix = b'],"schema":"engineering-publication-probe/v1"}\n'
    default_size = publication_probe._output_size(
        prefix, row, suffix, publication_probe.DEFAULT_ROWS)
    max_size = publication_probe._output_size(
        prefix, row, suffix, publication_probe.MAX_ROWS)
    assert 50_000_000 <= default_size <= 200_000_000
    assert max_size <= publication_probe.MAX_OUTPUT_BYTES
