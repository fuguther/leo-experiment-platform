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


def test_history_probe_uses_kernel_objects_and_completes_production_round_trip(
        tmp_path, monkeypatch):
    from CODE.leo_sim import kernel as kernel_module

    monkeypatch.setattr(kernel_module.Kernel, "__init__",
                        lambda *args, **kwargs: pytest.fail(
                            "history fixture must use Kernel.__new__"))
    monkeypatch.setattr(kernel_module, "run_simulation",
                        lambda *args, **kwargs: pytest.fail(
                            "history fixture must not run a simulation"))
    monkeypatch.setattr(publication_probe, "_peak_rss", lambda: {
        "value": 9876, "unit": "KiB", "platform": "Linux"})
    out_dir = tmp_path / "history"

    report = publication_probe.run_history_probe(
        out_dir, receivers=2, origins=2, history=3, downlink=2, queries=4)

    assert report["not_scientific"] is True
    assert report["scope"] == "engineering fixture; not a 96-satellite simulation"
    assert report["simulator_calls"] == 0
    assert report["arms"] == 4
    assert report["dimensions"] == {
        "receivers_per_arm": 2,
        "origins_per_receiver": 2,
        "initial_history_per_origin": 3,
        "downlink_resources_per_entry": 2,
        "record_queries_per_arm": 4,
        "arrival_events_per_arm": 2,
        "topology_rematches_per_arm": 1,
    }
    assert report["invalidation_checks"] == {
        "arrival_1_changed_digest": True,
        "arrival_2_changed_digest": True,
        "topology_rematch_changed_digest": True,
    }
    assert report["history_count"] == 16
    assert report["query_record_count"] == 16
    assert report["query_reference_digest_count"] == 16
    assert report["reference_digest_count"] == 32
    assert report["record_queries"] == 32
    assert report["benchmark_record_queries"] == 16
    assert report["materialization_record_queries"] == 16
    assert report["peak_rss_after_readback"] == report["peak_rss"]
    assert "current_rss_after_source_release_kib" in report
    assert report["equal"] is True
    assert report["encoded_file_sha256"] == hashlib.sha256(
        (out_dir / "fixture.json").read_bytes()).hexdigest()
    assert report["peak_rss"] == {
        "value": 9876, "unit": "KiB", "platform": "Linux"}

    from CODE.experiment_platform import t1_suite
    readback = t1_suite._inspect_result(out_dir / "fixture.json")
    assert readback["parse_error"] is None
    assert readback["sha256"] == report["encoded_file_sha256"]
    payload = readback["payload"]
    assert payload["schema"] == t1_tasks.SCHEMA_TASK
    assert payload["task"] == "network_alignment"
    arms = payload["document"]["arms"]
    assert len(arms) == 4
    assert all(len(arm["queries"]) == 4 for arm in arms)
    assert all(len(arm["final_histories"]) == 4 for arm in arms)
    assert all([row["query_index"] for row in arm["queries"]] == [0, 1, 2, 3]
               for arm in arms)
    assert all([len(row["samples"]) for row in arm["queries"]]
               == [3, 4, 5, 5] for arm in arms)
    assert all(len(history["samples"])
               == (5 if history["receiver"] == 0 and history["origin"] == 0
                   else 3)
               for arm in arms for history in arm["final_histories"])
    assert all(history["reference_sha256"]
               for arm in arms for history in arm["queries"])
    assert all(history["reference_sha256"]
               for arm in arms for history in arm["final_histories"])


def test_history_default_shape_and_mode_exclusion(tmp_path):
    assert publication_probe.DEFAULT_HISTORY_RECEIVERS == 8
    assert publication_probe.DEFAULT_HISTORY_ORIGINS == 12
    assert publication_probe.DEFAULT_HISTORY_LENGTH == 64
    assert publication_probe.DEFAULT_HISTORY_DOWNLINK == 99
    assert publication_probe.DEFAULT_HISTORY_QUERIES == 20_000

    with pytest.raises(SystemExit) as exc_info:
        publication_probe.main([
            "--out-dir", str(tmp_path / "excluded"), "--graph", "--history"])
    assert exc_info.value.code == 2
    assert not (tmp_path / "excluded").exists()


def test_history_shape_rejects_incomplete_invalidation_queries_before_output(
        tmp_path):
    out_dir = tmp_path / "too-small"
    with pytest.raises(publication_probe.ProbeError, match="queries"):
        publication_probe.run_history_probe(
            out_dir, receivers=2, origins=2, history=3, downlink=2, queries=3)
    assert not out_dir.exists()
