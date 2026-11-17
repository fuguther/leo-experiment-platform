"""Same-run prediction -> truth probe at a FIXED evaluation target instant.

WHY THIS EXISTS
---------------
The offline label builder (work/WP-T1-SCENE-CAL/build_prediction_labels.py)
reconstructs the resource backlog from the run event log.  That answers "what
will the backlog be", but it cannot prove that the predicted bits recorded in
the decision log and the truth used as the label describe the SAME run, the
SAME resource identity, the SAME generation and the SAME instant.  This module
samples the truth INSIDE the running simulation, at the evaluation target
instant, from the live resource object.

THE CONTRACT (deliberately narrow)
----------------------------------
1. The ONLINE side is unchanged.  Nothing here is read by routing, learning,
   the access layer, the deadline objective or any metric.  enqueue() is
   called from Kernel._record_decision AFTER the action is committed and only
   stores rows.
2. NO business randomness is consumed.  The sampler never calls a
   distribution; env.timeout schedules on the SimPy event heap, which is not a
   random source.  The kernel RNG stream is therefore bit-identical.
3. NO business resource is touched.  The sampler does not request, release or
   hold a link, a compute slot, a query server or a pending queue entry.
4. The truth is read from the SAME run through the SAME accessor the platform
   already uses to describe a directed ISL resource
   (Kernel._isl_resource_state_advertisement), so the definition is
   queued_data_and_control_bits + in-service_remaining_bits and it cannot
   drift from the in-run meaning.  It is defined ONCE here and labelled:
     queued_bits             link.data_bits + link.ctrl_bits
     in_service_remaining    head packet bits x remaining fraction, using the
                             rate sampled when THAT transmission began
     work_ahead_bits         the sum above; None when the residual is unknown
   The three components are all recorded, so a consumer can re-derive the sum
   and can never double count the active head: it appears ONLY inside
   in_service_remaining, never inside queued_bits.

SAME-TIMESTAMP ORDERING
-----------------------
One long-lived sampler process walks a monotonically sorted pending list; it
yields timeout(max(0, target - now)) and reads the resource when SimPy resumes
it at exactly target.  SimPy processes events at the same instant in
scheduling order, so the read observes every event that was already scheduled
before the sampler woke and none that is scheduled after it.  Because the
sampler only reads, it inserts no business event and removes none, so the
baseline run is bit-identical.  The truth is therefore the post-transition
state at the target instant, and that convention is recorded in the summary
as same_instant_convention.

FAILURE REASONS ARE RECORDED, NEVER SUBSTITUTED
------------------------------------------------
target_in_past, resource_missing, generation_changed (with both generations),
link_retired, no_prediction, no_resource_identity, prediction_generation_unknown,
beyond_horizon, overflow.  A record with a reason carries NO truth value.
"""
from __future__ import annotations

import bisect
import json
import math


SCHEMA = "leo-sim-target-probe/v2"


class TargetProbe:
    """Audit-only recorder. One instance per kernel run (or None)."""

    def __init__(self, kernel, path, *, max_samples=None, note=None,
                 stride=1):
        self.kernel = kernel
        self.path = str(path)
        self.note = note
        # Deterministic 1-in-stride sampling is performed over decision groups,
        # not individual candidate rows. This preserves the complete candidate
        # set required for a ranking replay.
        self.stride = max(1, int(stride))
        self._decision_seq = 0
        # max_samples remains a candidate-record cap, but groups are admitted or
        # dropped atomically so the cap can never leave a partial candidate set.
        self.max_samples = None if max_samples is None else int(max_samples)
        self.pending = []
        # Targets in ascending order, parallel to pending.  The sampler can only
        # read a target at its own instant if the queue head is always the
        # EARLIEST target; appending unsorted made it sleep past earlier
        # targets and sample them late (measured on the local smoke run:
        # 2508/6313 rows were read 8 us to 10.9 ms late).
        self._targets = []
        self.records = []
        self.counters = {
            "enqueued": 0, "sampled": 0, "dropped_overflow": 0,
            "skipped_by_stride": 0,
            "decision_groups_seen": 0,
            "decision_groups_without_valid_targets": 0,
            "decision_groups_enqueued": 0,
            "decision_groups_skipped_by_stride": 0,
            "decision_groups_dropped_overflow": 0,
        }
        self.reasons = {}
        self.started = False
        self.finished = False
        self.started_at = None
        self.end_at = None
        self._cursor = 0
        self._sorted = True
        self._wake = None
        self._exited = False
        self._parked = False

    # ------------------------------------------------------------------ input
    def enqueue(self, rows):
        """Queue candidate rows by decision, sampling/dropping only whole groups."""
        if not rows:
            return
        groups = {}
        for row in rows:
            if row.get("eval_target_s") is None:
                # Preserve the missing-target audit even though this row cannot
                # be scheduled. A selected decision with a missing candidate
                # remains incomplete and will fail closed in the replay.
                self._reason("no_eval_target")
            pid = row.get("pid")
            decision_id = row.get("decision_id")
            if pid is None or decision_id is None:
                self._reason("no_decision_identity")
                continue
            groups.setdefault((pid, decision_id), []).append(row)

        for group_rows in groups.values():
            self.counters["decision_groups_seen"] += 1
            self._decision_seq += 1
            valid_rows = [row for row in group_rows
                          if row.get("eval_target_s") is not None]
            if not valid_rows:
                self.counters["decision_groups_without_valid_targets"] += 1

            # Advance stride over every keyed decision group, including groups
            # whose target times are unavailable. Otherwise sample inclusion
            # would depend on whether the prediction produced a usable target.
            if self.stride > 1 and self._decision_seq % self.stride:
                self.counters["skipped_by_stride"] += len(valid_rows)
                self.counters["decision_groups_skipped_by_stride"] += 1
                continue
            if not valid_rows:
                continue
            if (self.max_samples is not None
                    and self.counters["enqueued"] + len(valid_rows) > self.max_samples):
                self.counters["dropped_overflow"] += len(valid_rows)
                self.counters["decision_groups_dropped_overflow"] += 1
                for _ in valid_rows:
                    self._reason("overflow")
                continue

            self.counters["decision_groups_enqueued"] += 1
            for row in valid_rows:
                self.counters["enqueued"] += 1
                target = float(row["eval_target_s"])
                position = bisect.bisect_right(self._targets, target)
                self._targets.insert(position, target)
                self.pending.insert(position, row)
                if self.started and self._wake is not None \
                        and not self._wake.triggered:
                    # A newer target may be EARLIER than the one the sampler is
                    # sleeping to. Wake it so every target is read at its own
                    # instant, never late.
                    self._wake.succeed()

    # ---------------------------------------------------------------- process
    def start(self):
        if self.started:
            return
        self.started = True
        self.started_at = float(self.kernel.env.now)
        self.kernel.env.process(self._sampler())

    def _sampler(self):
        """Read one target per wake-up, at exactly its own instant.

        Idle waits are event-driven, never a periodic poll.  A newly enqueued
        EARLIER target pokes the wake event so it is not read late.
        """
        env = self.kernel.env
        try:
            while True:
                if self._cursor >= len(self.pending):
                    self._wake = env.event()
                    self._parked = True
                    yield self._wake
                    self._parked = False
                    continue
                target = float(self.pending[self._cursor]["eval_target_s"])
                now = float(env.now)
                if target > now:
                    if self._wake is None or self._wake.triggered:
                        self._wake = env.event()
                    wake = self._wake
                    yield env.timeout(target - now) | wake
                    if wake.triggered and self._wake is wake:
                        self._wake = env.event()
                    continue
                self._sample_one(self.pending[self._cursor], now)
                self._cursor += 1
        finally:
            self._exited = True
            self.finished = True
            self.end_at = float(env.now)

    # ------------------------------------------------------------------ truth
    def _sample_one(self, row, now):
        record = dict(row)
        record["sampled_at_s"] = now
        record["truth"] = None
        reason = None
        link = None
        if row.get("predicted_bits") is None:
            reason = "no_prediction"
        elif row.get("resource_id") is None:
            reason = "no_resource_identity"
        elif now > float(self.kernel.horizon) + 1e-12:
            reason = "beyond_horizon"
        if reason is None:
            link = self._find_link(row, record)
            if link is None:
                reason = record.get("_lookup_reason") or "resource_missing"
        if reason is None:
            state = self.kernel._isl_resource_state_advertisement(link, now)
            work = state["work_ahead_bits_proxy"]
            record["truth"] = {
                "resource_id": record.get("sampled_resource_id"),
                "generation": state["generation"],
                "rate_bps": state["rate_bps"],
                "queued_bits": int(state["value"]),
                "work_ahead_bits": work,
                "in_service_remaining_bits": (
                    None if work is None
                    else float(work) - float(state["value"])),
                "definition": "queued_data_and_control_bits + in_service_remaining",
                "source": "kernel_live_resource_at_target",
            }
            record["truth_definition"] = (
                "work_ahead_bits = queued_bits + in_service_remaining_bits")
        if reason is not None:
            record["missing_reason"] = reason
            self._reason(reason)
        else:
            self.counters["sampled"] += 1
        record.pop("_lookup_reason", None)
        self.records.append(record)

    def _find_link(self, row, record):
        """Resolve the DIRECTED ISL resource, refusing a substitute.

        Identity is (sat, peer, generation).  A resource that has been
        replaced is a DIFFERENT resource, and reading its successor would
        silently answer a different question.
        """
        resource_id = row.get("resource_id")
        # THE RESOURCE IS THE PREDICTED ONE, NOT THE LOCAL CANDIDATE LINK.
        # The time-alignment prediction is about the PEER EGRESS the candidate
        # hands the packet to, i.e. resource_id "isl:<peer>:<peer_egress>".
        # Looking up isls[this_sat][candidate_direction] would silently read a
        # DIFFERENT link (measured: 5979/6313 rows resolved to isl:74:82 while
        # the prediction was about isl:82:90).
        parts = str(resource_id or "").split(":")
        if len(parts) != 3 or parts[0] != "isl":
            record["_lookup_reason"] = "unparseable_resource_id"
            return None
        host, peer = int(parts[1]), int(parts[2])
        links = (self.kernel.isls[host]
                 if 0 <= host < len(self.kernel.isls) else {})
        link = None
        for candidate in links.values():
            if int(candidate.peer) == peer:
                link = candidate
                break
        if link is None:
            # Retired generations live outside the live map; distinguish
            # "the resource is gone" from "the identity was replaced".
            for retired in getattr(self.kernel, "_retired_isls", ()) or ():
                if (int(retired.sat) == host and int(retired.peer) == peer
                        and int(retired.gen) == int(row.get("generation") or -1)):
                    record["_lookup_reason"] = "link_retired"
                    record["sampled_resource_id"] = "isl:%d:%d" % (host, peer)
                    record["sampled_generation"] = int(retired.gen)
                    return None
            record["_lookup_reason"] = "resource_missing"
            return None
        record["sampled_resource_id"] = "isl:%d:%d" % (int(link.sat), int(link.peer))
        record["sampled_generation"] = int(link.gen)
        predicted_generation = row.get("generation")
        if predicted_generation is None:
            record["_lookup_reason"] = "prediction_generation_unknown"
            return None
        if int(link.gen) != int(predicted_generation):
            record["generation_at_target"] = int(link.gen)
            record["_lookup_reason"] = "generation_changed"
            return None
        if bool(getattr(link, "retired", False)):
            record["_lookup_reason"] = "link_retired"
            return None
        return link

    def _reason(self, reason):
        self.reasons[reason] = self.reasons.get(reason, 0) + 1

    def note_reason(self, reason):
        """Public counter for a record that never reached the queue."""
        self._reason(reason)

    # ----------------------------------------------------------------- output
    def summary(self):
        targets = [r["eval_target_s"] for r in self.records]
        return {
            "schema": SCHEMA,
            "path": self.path,
            "note": self.note,
            "started_at_s": self.started_at,
            "closed_at_s": self.end_at,
            # finished means "every queued record has been resolved"; the
            # sampler process itself is parked (not exited) at a natural stop
            # because no further event wakes it.  Both facts are reported.
            "finished": bool(self._cursor >= len(self.pending)),
            "sampler_exited": bool(self._exited),
            "sampler_parked_at_stop": bool(self._parked),
            "enqueued": self.counters["enqueued"],
            "stride": int(self.stride),
            "sampling_unit": "decision_group",
            "decision_groups_seen": self.counters["decision_groups_seen"],
            "decision_groups_without_valid_targets": self.counters["decision_groups_without_valid_targets"],
            "decision_groups_enqueued": self.counters["decision_groups_enqueued"],
            "decision_groups_skipped_by_stride": self.counters["decision_groups_skipped_by_stride"],
            "decision_groups_dropped_overflow": self.counters["decision_groups_dropped_overflow"],
            "skipped_by_stride": self.counters["skipped_by_stride"],
            "sampling_rule": ("deterministic 1-in-stride over all keyed (pid, decision_id) "
                              "groups, including groups with no valid target; every "
                              "valid-target candidate in a selected group is kept; "
                              "overflow drops whole groups"),
            "sampled": self.counters["sampled"],
            "dropped_overflow": self.counters["dropped_overflow"],
            "reason_counts": dict(sorted(self.reasons.items())),
            "pending_unresolved": int(len(self.pending) - self._cursor),
            "eval_target_min_s": (min(targets) if targets else None),
            "eval_target_max_s": (max(targets) if targets else None),
            "same_instant_convention": (
                "truth read by a single sampler process resumed at exactly "
                "eval_target_s; it observes the state after every event already "
                "scheduled at that instant before the sampler woke, and before "
                "any event scheduled after it"),
            "truth_definition": (
                "work_ahead_bits = queued_bits(data+control) + "
                "in_service_remaining_bits(head packet x remaining fraction)"),
            "audit_only": True,
            "feeds_online_policy": False,
            "consumes_business_randomness": False,
            "occupies_business_resources": False,
        }

    def write(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            for record in self.records:
                fh.write(json.dumps(record, sort_keys=True) + "\n")
        return {"path": self.path, "rows": len(self.records),
                "summary": self.summary()}


def _finite(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(float(value)))
