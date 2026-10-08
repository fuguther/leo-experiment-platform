import hashlib

import pytest

from CODE.experiment_platform import artifact_identity, t1_tasks
from CODE.leo_sim import trace as trace_mod


VALID_SHA256 = "7a" * 32


def _install_design_fixtures(monkeypatch, *, input_sha256=VALID_SHA256,
                             population_sha256=VALID_SHA256):
    logical_path = "CODE/data/population.tif"
    resolved = {
        "config": {
            "demand": {
                "mode": "population_gravity",
                "population_path": logical_path,
            },
            "execution": {"max_packets": 4},
        },
    }
    manifest = {
        "emission_end_s": 10.0,
        "trace_sha256": "trace-digest",
        "input_sha256": input_sha256,
        "population": {"source_sha256": population_sha256},
    }
    rows = [
        {"packet_id": 1, "src_grid_id": "grid-a", "dst_grid_id": "grid-b"},
    ]
    monkeypatch.setattr(t1_tasks.config_mod, "load_config_file",
                        lambda _path: resolved)
    monkeypatch.setattr(trace_mod, "compile_trace",
                        lambda _resolved, _out: manifest)
    monkeypatch.setattr(trace_mod, "load_trace",
                        lambda *_args, **_kwargs: rows)
    return logical_path


def test_design_uses_compiled_snapshot_hash_when_logical_source_is_absent(
        monkeypatch, tmp_path):
    logical_path = _install_design_fixtures(monkeypatch)
    monkeypatch.setattr(artifact_identity, "REPO_ROOT", tmp_path)

    _resolved, _rows, _geometry, source = t1_tasks.design(
        config_path="snapshot-config.yaml")

    assert not (tmp_path / logical_path).exists()
    assert source["native_population"]["population_sha256"] == VALID_SHA256
    assert source["native_population"]["population_source"] == logical_path


def test_design_does_not_use_a_different_file_at_logical_source_path(
        monkeypatch, tmp_path):
    logical_path = _install_design_fixtures(monkeypatch)
    monkeypatch.setattr(artifact_identity, "REPO_ROOT", tmp_path)
    decoy = tmp_path / logical_path
    decoy.parent.mkdir(parents=True)
    decoy.write_bytes(b"different repository-side file")

    _resolved, _rows, _geometry, source = t1_tasks.design(
        config_path="snapshot-config.yaml")

    assert hashlib.sha256(decoy.read_bytes()).hexdigest() != VALID_SHA256
    assert source["native_population"]["population_sha256"] == VALID_SHA256


def test_design_rejects_disagreeing_population_input_hashes(
        monkeypatch, tmp_path):
    _install_design_fixtures(
        monkeypatch, input_sha256=VALID_SHA256,
        population_sha256="8b" * 32)
    monkeypatch.setattr(artifact_identity, "REPO_ROOT", tmp_path)

    with pytest.raises(t1_tasks.TaskError, match="population.*hash"):
        t1_tasks.design(config_path="snapshot-config.yaml")


@pytest.mark.parametrize("input_sha256,population_sha256", [
    (None, VALID_SHA256),
    (VALID_SHA256, None),
    ("not-a-sha256", "not-a-sha256"),
    ("A" * 64, "A" * 64),
])
def test_design_rejects_missing_or_noncanonical_population_hashes(
        monkeypatch, tmp_path, input_sha256, population_sha256):
    _install_design_fixtures(
        monkeypatch, input_sha256=input_sha256,
        population_sha256=population_sha256)
    monkeypatch.setattr(artifact_identity, "REPO_ROOT", tmp_path)

    with pytest.raises(t1_tasks.TaskError, match="population.*hash"):
        t1_tasks.design(config_path="snapshot-config.yaml")
