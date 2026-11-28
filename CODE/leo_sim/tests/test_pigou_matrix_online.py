from pathlib import Path
import json

from CODE.scripts import pigou_matrix


def test_online_native_seed_builds_seed_specific_validation_labels(tmp_path):
    matrix = object.__new__(pigou_matrix.Matrix)
    matrix.loads = {"A": 210.0}
    matrix.raw = []
    validation_labels = []

    def fake_cell(label, spec, *, model=None, resume=None):
        if spec["exp"] == "validation":
            validation_labels.append(label)
            return tmp_path / "validation", {"pdr": 0.5 + spec["seed"] / 1000.0}

        round_no = int(spec["epoch"])
        parent = tmp_path / f"train-r{round_no}"
        artifact_dir = parent / "cell" / "sim" / "pigou_online"
        artifact_dir.mkdir(parents=True)
        checkpoint = artifact_dir / "online.keras"
        checkpoint.write_bytes(b"checkpoint")
        return parent, {
            "run_dir": str(parent / "cell" / "sim"),
            "alg": "Ours",
            "scene": "A",
        }

    matrix.cell = fake_cell

    result = matrix._native_seed("A", "Ours", 11, "full")

    expected = [
        f"A-Ours-full-t11-r{round_no}-v{seed}"
        for round_no in (3, 5)
        for seed in pigou_matrix.VAL_SEEDS
    ]
    assert validation_labels == expected
    assert result[:4] == ("A", "Ours", 11, "full")
    assert Path(result[4]).is_file()


def test_gate2_probe_writes_report(tmp_path, monkeypatch):
    matrix = object.__new__(pigou_matrix.Matrix)
    matrix.prefix = "gate-test"
    matrix._heldout_tasks = lambda *args, **kwargs: []
    responses = iter([
        [
            (None, {"scene": "A", "alg": "Ours", "tx_goodput": 0.9}),
            *[(None, {"scene": "A", "alg": alg, "tx_goodput": 0.8})
              for alg in ("B1", "B2", "B4", "B5", "B6")],
        ],
        [
            (None, {"scene": "B", "alg": "Ours", "tx_goodput": 0.9}),
            *[(None, {"scene": "B", "alg": alg, "tx_goodput": 0.8})
              for alg in ("B1", "B2", "B4", "B5", "B6")],
        ],
        [(None, {"scene": "A", "alg": "Ours", "tx_goodput": 0.7})],
        [(None, {"scene": "B", "alg": "Ours", "tx_goodput": 0.7})],
    ])
    matrix.parallel = lambda tasks, workers: next(responses)
    matrix.raw = []
    monkeypatch.setattr(pigou_matrix, "ROOT", tmp_path)
    (tmp_path / "runs").mkdir()

    report = matrix._gate2_probe()

    path = tmp_path / "runs" / "gate-test-gate2.json"
    assert json.loads(path.read_text()) == report
    assert report["A"]["passed_tx_goodput"]
    assert report["B"]["passed_tx_goodput"]
