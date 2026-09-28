"""Third-round independent review (S3): a hit must not become a free full score.

The defect: decide_deferred judged a per_flow cache hit at the REQUEST instant
(no compute_request, no compute interval), then _decide re-checked the TTL at
env.now AFTER the query service wait.  If the entry lapsed during that wait,
that same decision fell through to a full scoring run nobody paid for.

Frozen contract (plan P2): "freeze at request, never refresh while queued".

The check is PER DECISION INSTANCE, not per packet id: a packet can be decided
again later at another satellite and legitimately pay compute then, which would
mask a per-pid multiset comparison.
"""
from __future__ import annotations

import pytest

from CODE.experiment_platform import scripted_scenarios
from CODE.leo_sim import config as config_mod, kernel


def _run(monkeypatch, *, ttl, query_delay, compute_delay):
    resolved, rows, geometry, _meta = scripted_scenarios.build("same_flow")
    raw = resolved["config"]
    cfg = config_mod.resolve_config(dict(
        raw,
        time_alignment=dict(raw["time_alignment"], enabled=True,
                            execution_mode="per_flow",
                            per_flow_ttl_s=ttl, query_delay_s=query_delay),
        execution=dict(raw["execution"], compute_delay_s=compute_delay)))

    scored = []
    computed = []
    lapsed = []
    violations = []

    original_order = kernel.Kernel._score_order

    def spy_order(self, pkt, sat, now, cands, own_q):
        free = getattr(self, "_s3_free", {}).get(id(pkt))
        if free:
            violations.append((pkt.pid, round(float(now), 6)))
        scored.append(pkt.pid)
        return original_order(self, pkt, sat, now, cands, own_q)

    monkeypatch.setattr(kernel.Kernel, "_score_order", spy_order)

    original_milestone = kernel.Kernel._compute_milestone

    def spy_milestone(self, milestone, pkt, sat, job_id, **extra):
        if milestone == "compute_request":
            computed.append(pkt.pid)
        return original_milestone(self, milestone, pkt, sat, job_id, **extra)

    monkeypatch.setattr(kernel.Kernel, "_compute_milestone", spy_milestone)

    original_ta = kernel.Kernel._time_aligned_order

    def spy_ta(self, pkt, sat, now, cands, own_q, frozen_hit=None):
        if frozen_hit is not None and frozen_hit["expires_at"] <= now:
            lapsed.append(pkt.pid)
        return original_ta(self, pkt, sat, now, cands, own_q,
                           frozen_hit=frozen_hit)

    monkeypatch.setattr(kernel.Kernel, "_time_aligned_order", spy_ta)

    original_deferred = kernel.Kernel.decide_deferred

    def spy_deferred(self, pkt, sat):
        if not hasattr(self, "_s3_free"):
            self._s3_free = {}
        key = id(pkt)
        self._s3_free[key] = not self._packet_compute_required(
            pkt, sat, float(self.env.now))
        try:
            return (yield from original_deferred(self, pkt, sat))
        finally:
            self._s3_free.pop(key, None)

    monkeypatch.setattr(kernel.Kernel, "decide_deferred", spy_deferred)

    kernel.run_simulation(cfg, rows, geometry=geometry, decision_sink=[],
                          timeline_sink=[])
    return scored, computed, lapsed, violations


def test_a_per_flow_hit_that_lapses_during_the_query_wait_is_still_a_hit(monkeypatch):
    scored, computed, lapsed, violations = _run(
        monkeypatch, ttl=0.2, query_delay=0.1, compute_delay=0.1)
    assert lapsed, ("this fixture must exercise a hit whose TTL lapses during "
                    "the query wait, otherwise it proves nothing")
    assert not violations, (
        "a decision classified as free of computation ran the full scorer: "
        f"{violations}")


@pytest.mark.parametrize("ttl", [0.05, 0.2, 0.5])
def test_no_decision_scores_without_paying(monkeypatch, ttl):
    _scored, _computed, _lapsed, violations = _run(
        monkeypatch, ttl=ttl, query_delay=0.1, compute_delay=0.1)
    assert not violations, f"ttl={ttl}: unpaid scoring at {violations}"
