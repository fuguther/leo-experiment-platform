"""T1-COMPLETE P7: background schedule updates and cross-version forwarding."""
from __future__ import annotations

import pytest

from CODE.leo_sim import async_routing as ar, time_alignment as ta

R1 = ta.ResourceKey(1, "E", "isl")
R2 = ta.ResourceKey(2, "S", "isl")


def _snapshot(snapshot_at, q1, q2=None, arm="candidate"):
    q2 = q1 if q2 is None else q2
    history = (
        ta.StateSample(R1, snapshot_at - 1.0, snapshot_at - 0.9, q1, 1e6),
        ta.StateSample(R2, snapshot_at - 1.0, snapshot_at - 0.9, q2, 1e6),
    )
    return ta.make_snapshot(
        satellite=0, snapshot_at=snapshot_at, history=history,
        legal_directions=("E", "W"),
        resources={"E": R1, "W": R2},
        egress_queue_bits={"E": 0.0, "W": 0.0},
        link_rate_bps={"E": 1e6, "W": 1e6},
        link_propagation_s={"E": 0.01, "W": 0.01},
        peer_process_s={"E": 0.0, "W": 0.0},
        remaining_prop_s={"E": 0.02, "W": 0.02},
        pkt_bits=1000.0, arm=arm,
        provenance=("scope:|0|B|default",))


def _manager(service=2.0, delay=0.5, window=1.0, bins=4):
    return ar.AsyncScheduleManager(install_delay_s=delay, valid_window_s=window,
                                   window_bins=bins, service_s=service)


def _activate(manager, scope, now, q1=0.0, q2=0.0):
    """Drive one update to an installed table and return it."""
    scope_key = scope
    res = manager.request_update(scope_key, _snapshot(now, q1, q2), now)
    assert res["accepted"]
    ticket = res["ticket"]
    manager.finish_compute(ticket, now + manager.service_s)
    verdict = manager.install(ticket, ticket.install_at)
    assert verdict["installed"], verdict
    return ticket


def test_the_state_machine_walks_uninitialized_to_active():
    m = _manager()
    scope = (0, "B", "default")
    assert m.state_of(scope) == ar.STATE_UNINITIALIZED
    res = m.request_update(scope, _snapshot(0.0, 0.0), 0.0)
    assert m.state_of(scope) == ar.STATE_COMPUTING
    ticket = m.finish_compute(res["ticket"], 2.0)
    assert m.state_of(scope) == ar.STATE_INSTALL_PENDING
    m.install(ticket, 2.5)
    assert m.state_of(scope) == ar.STATE_ACTIVE


def test_the_task_book_cross_version_timing_example():
    m = _manager(service=2.0, delay=0.5, window=5.0, bins=1)
    scope = (0, "B", "default")
    v1 = _activate(m, scope, 0.0)
    assert v1.version == 1
    # a packet at 0.5 uses v1
    assert m.query(scope, 0.5, ("E", "W"))["version"] == 1
    # request at t=1, service 2 s -> computed at 3, installs at 3.5
    res = m.request_update(scope, _snapshot(1.0, 0.0), 1.0)
    v2 = res["ticket"]
    assert v2.version == 2
    # still v1 before install
    assert m.query(scope, 1.5, ("E", "W"))["version"] == 1
    m.finish_compute(v2, 3.0)
    assert m.query(scope, 3.2, ("E", "W"))["version"] == 1
    m.install(v2, 3.5)
    assert m.query(scope, 3.5, ("E", "W"))["version"] == 2


def test_a_new_version_may_repeat_the_same_actions():
    m = _manager(service=1.0, delay=0.0, window=5.0, bins=1)
    scope = (0, "B", "default")
    _activate(m, scope, 0.0, q1=0.0, q2=0.0)  # installs at t=1.0
    first = m.query(scope, 1.2, ("E", "W"))
    assert first["action"] is not None, first
    res = m.request_update(scope, _snapshot(2.0, 0.0, 0.0), 2.0)
    m.finish_compute(res["ticket"], 3.0)
    m.install(res["ticket"], 3.0)
    second = m.query(scope, 3.1, ("E", "W"))
    assert first["action"] == second["action"]
    assert first["version"] != second["version"]


def test_a_busy_scope_merges_exactly_one_pending_trigger():
    m = _manager(service=2.0, delay=0.5)
    scope = (0, "B", "default")
    _activate(m, scope, 0.0)
    m.request_update(scope, _snapshot(1.0, 0.0), 1.0)
    first = m.request_update(scope, _snapshot(1.1, 0.0), 1.1)
    second = m.request_update(scope, _snapshot(1.2, 0.0), 1.2)
    third = m.request_update(scope, _snapshot(1.3, 0.0), 1.3)
    assert first["merged"] is True
    assert second["merged"] is False
    assert third["merged"] is False
    counts = m.snapshot_counts()
    assert counts["merged_triggers"] == 1
    assert counts["versions_issued"] == 2


def test_install_before_the_delay_has_elapsed_is_refused():
    m = _manager(service=1.0, delay=0.5)
    scope = (0, "B", "default")
    res = m.request_update(scope, _snapshot(0.0, 0.0), 0.0)
    m.finish_compute(res["ticket"], 1.0)
    early = m.install(res["ticket"], 1.2)
    assert early == {"installed": False, "reason": "install_delay_not_elapsed"}
    late = m.install(res["ticket"], 1.5)
    assert late["installed"] is True


def test_an_older_completed_result_cannot_overwrite_a_newer_version():
    m = _manager(service=1.0, delay=0.0)
    scope = (0, "B", "default")
    res1 = m.request_update(scope, _snapshot(0.0, 0.0), 0.0)
    m.finish_compute(res1["ticket"], 1.0)
    # a second update starts only after the first is installed
    m.install(res1["ticket"], 1.0)
    res2 = m.request_update(scope, _snapshot(2.0, 0.0), 2.0)
    m.finish_compute(res2["ticket"], 3.0)
    m.install(res2["ticket"], 3.0)
    assert m.scopes[scope].version == 2
    # an old ticket that only NOW becomes installable must be rejected
    stale = res1["ticket"]
    stale.state = ar.STATE_INSTALL_PENDING
    stale.install_at = 100.0
    verdict = m.install(stale, 100.0)
    assert verdict["installed"] is False
    assert verdict["reason"] == "older_version"
    assert m.scopes[scope].version == 2


def test_a_window_that_elapsed_before_install_is_discarded_and_reported():
    m = _manager(service=1.0, delay=0.5, window=1.0)
    scope = (0, "B", "default")
    res = m.request_update(scope, _snapshot(0.0, 0.0), 0.0)
    m.finish_compute(res["ticket"], 1.0)  # predicted install 1.5, expires 2.5
    verdict = m.install(res["ticket"], 9.0)  # a very late install
    assert verdict["installed"] is False
    assert verdict["reason"] == "expired_before_install"
    assert m.scopes[scope].expired_discards == 1


def test_a_query_without_a_table_falls_back_and_asks_for_an_update():
    m = _manager()
    scope = (0, "B", "default")
    result = m.query(scope, 0.0, ("E", "W"))
    assert result["fallback"] is True
    assert result["needs_update"] is True
    assert result["reason"] == "no_table_installed"
    assert m.scopes[scope].fallback_queries == 1


def test_a_query_never_rescores_or_predicts():
    calls = {"n": 0}

    def builder(*args, **kwargs):
        calls["n"] += 1
        return ar._default_builder(*args, **kwargs)

    m = ar.AsyncScheduleManager(install_delay_s=0.0, valid_window_s=5.0,
                                window_bins=1, service_s=0.0, builder=builder)
    scope = (0, "B", "default")
    _activate(m, scope, 0.0)
    before = calls["n"]
    for t in (0.1, 0.2, 0.3, 0.4):
        m.query(scope, t, ("E", "W"))
    assert calls["n"] == before


def test_the_window_starts_at_the_actual_install():
    m = _manager(service=1.0, delay=0.5, window=1.0)
    scope = (0, "B", "default")
    res = m.request_update(scope, _snapshot(0.0, 0.0), 0.0)
    m.finish_compute(res["ticket"], 1.0)
    m.install(res["ticket"], 2.0)
    table = m.scopes[scope].installed
    assert table.installed_at == 2.0
    assert table.expires_at == 3.0
    assert m.query(scope, 2.5, ("E", "W"))["version"] == 1


def test_a_late_install_records_the_offset_without_rewriting_targets():
    m = _manager(service=1.0, delay=0.5, window=10.0)
    scope = (0, "B", "default")
    res = m.request_update(scope, _snapshot(0.0, 0.0), 0.0)
    ticket = res["ticket"]
    predicted = ticket.predicted_install_at
    entries_before = ticket.schedule.entries
    # the compute is queued and finishes much later than predicted
    m.finish_compute(ticket, 5.0)
    assert ticket.predicted_install_at == predicted
    assert ticket.schedule.entries == entries_before
    assert ticket.install_offset_s == pytest.approx(5.5 - predicted)
    m.install(ticket, ticket.install_at)
    assert m.scopes[scope].version == 1


def test_async_window_bins_are_costed_not_free():
    m = _manager(service=1.0, delay=0.0, window=4.0, bins=4)
    scope = (0, "B", "default")
    res = m.request_update(scope, _snapshot(0.0, 0.0), 0.0)
    cost = res["ticket"].cost
    assert cost["bins"] == 4
    assert cost["scorings"] == 4
    assert cost["predictions"] == 4 * 2


def test_async_point_uses_one_bin():
    m = _manager(service=1.0, delay=0.0, window=4.0, bins=1)
    scope = (0, "B", "default")
    res = m.request_update(scope, _snapshot(0.0, 0.0), 0.0)
    assert len(res["ticket"].schedule.entries) == 1
    assert res["ticket"].cost["scorings"] == 1


def test_query_records_scope_version_bin_and_action():
    m = _manager(service=1.0, delay=0.0, window=5.0, bins=4)
    scope = (0, "B", "default")
    _activate(m, scope, 0.0)
    m.query(scope, 2.0, ("E", "W"))
    queries = [e for e in m.events if e["milestone"] == "schedule_query"]
    assert queries
    assert queries[-1]["scope"] == [0, "B", "default"]
    assert queries[-1]["version"] == 1
    assert queries[-1]["bin"] is not None
    assert "action" in queries[-1]


def test_the_path_filter_prevents_a_loop_without_rescoring():
    m = _manager(service=1.0, delay=0.0, window=5.0, bins=1)
    scope = (0, "B", "default")
    _activate(m, scope, 0.0)  # installs at t=1.0
    first = m.query(scope, 1.5, ("E", "W"), path=())
    assert first["action"] is not None, first
    looped = m.query(scope, 1.5, ("E", "W"), path=(first["action"],))
    assert looped["action"] != first["action"]
    blocked = m.query(scope, 1.5, ("E", "W"),
                      path=(first["action"], looped["action"]))
    assert blocked["fallback"] is True
    assert blocked["action"] is None


def test_an_illegal_only_query_triggers_an_update():
    m = _manager(service=1.0, delay=0.0, window=5.0, bins=1)
    scope = (0, "B", "default")
    _activate(m, scope, 0.0)
    result = m.query(scope, 0.5, legal=(), path=())
    assert result["fallback"] is True
    assert result["needs_update"] is True
