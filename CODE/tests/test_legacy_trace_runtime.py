from __future__ import annotations

import csv
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE))

from legacy_trace_runtime import LegacyTraceError, load_and_project_trace


class _Gateway:
    def __init__(self, name: str, latitude: float, longitude: float):
        self.name = name
        self.latitude = latitude
        self.longitude = longitude


def _write_trace(path: Path, rows: list[dict]) -> str:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "packet_id",
                "emit_time_s",
                "src_grid_id",
                "dst_grid_id",
                "bits",
                "deadline_at_s",
            ],
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)
    return hashlib.sha256(path.read_bytes()).hexdigest()


class LegacyTraceRuntimeTests(unittest.TestCase):
    def test_projects_each_grid_to_nearest_active_gateway_and_preserves_demand(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            trace = Path(raw) / "trace.csv"
            digest = _write_trace(trace, [{
                "packet_id": 7,
                "emit_time_s": 1.25,
                "src_grid_id": "G1:90:180",  # (0.5, 0.5)
                "dst_grid_id": "G1:90:300",  # (0.5, 120.5)
                "bits": 12345,
                "deadline_at_s": "",
            }])
            gateways = [
                _Gateway("west", 0.0, 0.0),
                _Gateway("east", 0.0, 120.0),
            ]

            projected, manifest = load_and_project_trace(
                trace,
                gateways,
                horizon_s=5.0,
                expected_sha256=digest,
                max_packets=10,
            )

            self.assertEqual(len(projected), 1)
            self.assertIs(projected[0]["source_gateway"], gateways[0])
            self.assertIs(projected[0]["destination_gateway"], gateways[1])
            self.assertEqual(projected[0]["packet_id"], 7)
            self.assertEqual(projected[0]["bits"], 12345)
            self.assertEqual(manifest["trace_sha256"], digest)
            self.assertEqual(manifest["offered_packets"], 1)
            self.assertEqual(manifest["offered_bits"], 12345)
            self.assertEqual(manifest["projection"]["G1:90:180"]["gateway"], "west")

    def test_rejects_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            trace = Path(raw) / "trace.csv"
            _write_trace(trace, [{
                "packet_id": 1,
                "emit_time_s": 0,
                "src_grid_id": "G1:90:180",
                "dst_grid_id": "G1:90:300",
                "bits": 64800,
                "deadline_at_s": "",
            }])
            with self.assertRaisesRegex(LegacyTraceError, "SHA-256 mismatch"):
                load_and_project_trace(
                    trace,
                    [_Gateway("a", 0, 0), _Gateway("b", 0, 120)],
                    horizon_s=5,
                    expected_sha256="0" * 64,
                    max_packets=10,
                )

    def test_rejects_projection_collision_instead_of_counting_local_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            trace = Path(raw) / "trace.csv"
            digest = _write_trace(trace, [{
                "packet_id": 1,
                "emit_time_s": 0,
                "src_grid_id": "G1:90:180",
                "dst_grid_id": "G1:90:181",
                "bits": 64800,
                "deadline_at_s": "",
            }])
            with self.assertRaisesRegex(LegacyTraceError, "same active Gateway"):
                load_and_project_trace(
                    trace,
                    [_Gateway("only-nearby", 0, 0), _Gateway("far", 60, 120)],
                    horizon_s=5,
                    expected_sha256=digest,
                    max_packets=10,
                )

    def test_rejects_deadlines_unsupported_by_legacy_gateway_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            trace = Path(raw) / "trace.csv"
            digest = _write_trace(trace, [{
                "packet_id": 1,
                "emit_time_s": 0,
                "src_grid_id": "G1:90:180",
                "dst_grid_id": "G1:90:300",
                "bits": 64800,
                "deadline_at_s": 2,
            }])
            with self.assertRaisesRegex(LegacyTraceError, "does not implement packet deadlines"):
                load_and_project_trace(
                    trace,
                    [_Gateway("a", 0, 0), _Gateway("b", 0, 120)],
                    horizon_s=5,
                    expected_sha256=digest,
                    max_packets=10,
                )


if __name__ == "__main__":
    unittest.main()
