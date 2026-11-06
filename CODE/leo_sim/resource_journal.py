"""Audit-only resource journal: per-link queue time series + loss attribution.

Two questions this answers that aggregate metrics cannot:
  * WHEN and WHICH directed ISL is queueing (per-link backlog over time);
  * WHERE a packet died (link / satellite / hop / fate), so a loss can be
    attributed instead of only counted.

Contract: opt-in, write-only, never read by routing/metrics; it does not
request/release resources or consume randomness.  Rows are sampled by a fixed
1-in-stride rule so the file stays bounded; loss rows are never sampled out.
"""
from __future__ import annotations

import json


SCHEMA = "leo-sim-resource-journal/v1"


class ResourceJournal:
    def __init__(self, path, *, stride=1, max_rows=200000):
        self.path = str(path)
        self.stride = max(1, int(stride))
        self.max_rows = max_rows
        self._seen = 0
        self.rows = 0
        self.dropped = 0
        self.losses = 0
        self._fh = open(self.path, "w", encoding="utf-8")

    def append_queue_state(self, row):
        """Record one post-transition queue state (sampled)."""
        if row.get("milestone") != "queue_state":
            return
        self._seen += 1
        if self.stride > 1 and (self._seen % self.stride):
            return
        if self.max_rows is not None and self.rows >= self.max_rows:
            self.dropped += 1
            return
        compact = {
            "kind": "queue_state",
            "t": row.get("at"),
            "resource_id": row.get("resource_id"),
            "sat": row.get("sat"),
            "peer": row.get("peer"),
            "direction": row.get("direction"),
            "generation": row.get("generation"),
            "queued_data_bits": row.get("queued_data_bits"),
            "queued_control_bits": row.get("queued_control_bits"),
            "queued_bits": row.get("queued_bits"),
            "in_service_bits": row.get("in_service_bits"),
            "in_service": row.get("in_service"),
            "event": row.get("event"),
            "outcome": row.get("outcome"),
        }
        self._fh.write(json.dumps(compact, sort_keys=True) + "\n")
        self.rows += 1

    def record_loss(self, *, pkt, fate, sat=None, peer=None, direction=None,
                    candidates=None, hop_index=None, decision_id=None,
                    bits=None, at=None):
        self.losses += 1
        row = {
            "kind": "loss",
            "t": at,
            "pid": getattr(pkt, "pid", None),
            "fate": str(fate),
            "sat": None if sat is None else int(sat),
            "peer": None if peer is None else int(peer),
            "direction": direction,
            "candidates": (None if candidates is None
                           else [str(c) for c in candidates]),
            "hop_index": hop_index,
            "decision_id": decision_id,
            "bits": (int(bits) if bits is not None
                     else getattr(pkt, "bits", None)),
        }
        self._fh.write(json.dumps(row, sort_keys=True) + "\n")

    def summary(self):
        return {
            "schema": SCHEMA,
            "path": self.path,
            "stride": self.stride,
            "seen_queue_states": self._seen,
            "rows": self.rows,
            "dropped_overflow": self.dropped,
            "loss_rows": self.losses,
            "audit_only": True,
            "feeds_online_policy": False,
        }

    def close(self):
        try:
            self._fh.close()
        except Exception:
            pass
        return self.summary()
