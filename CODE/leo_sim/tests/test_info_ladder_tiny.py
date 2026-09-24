"""Contract tests for the dependency-free information-ladder tiny model."""

import pytest

from CODE.leo_sim.info_ladder_tiny import (
    INFO_LEVELS,
    TinyLinkState,
    TinyLadderScenario,
    choose_link,
    observe,
    run_demo,
)


EDGE_A = (0, 1)
EDGE_B = (0, 2)


def _scenario() -> TinyLadderScenario:
    return TinyLadderScenario(
        links=(
            TinyLinkState(
                edge=EDGE_A,
                direction="east",
                local_queue_bits=8,
                rate_mbps=100.0,
                available=True,
                remote_queue_bits=60,
                topology_available=True,
                field_age_s=0.2,
            ),
            TinyLinkState(
                edge=EDGE_B,
                direction="west",
                local_queue_bits=8,
                rate_mbps=200.0,
                available=True,
                remote_queue_bits=20,
                topology_available=True,
                field_age_s=3.0,
            ),
        )
    )


def test_each_level_exposes_only_declared_fields():
    scenario = _scenario()
    expected = {
        "local_queue_direction": {
            "edge", "direction", "local_queue_bits",
        },
        "link_rate_availability": {
            "edge", "direction", "local_queue_bits", "rate_mbps", "available",
        },
        "remote_queue_topology": {
            "edge", "direction", "local_queue_bits", "rate_mbps", "available",
            "remote_queue_bits", "topology_available",
        },
        "field_age": {
            "edge", "direction", "local_queue_bits", "rate_mbps", "available",
            "remote_queue_bits", "topology_available", "field_age_s",
        },
    }
    for level in INFO_LEVELS:
        observation = observe(scenario, level)
        assert set(observation.fields_for(EDGE_A)) == expected[level]
        assert set(observation.fields_for(EDGE_B)) == expected[level]


def test_hidden_field_changes_cannot_change_lower_level_view_or_action():
    scenario = _scenario()
    changed = TinyLadderScenario(
        links=tuple(
            TinyLinkState(
                edge=link.edge,
                direction=link.direction,
                local_queue_bits=link.local_queue_bits,
                rate_mbps=link.rate_mbps,
                available=link.available,
                remote_queue_bits=999 - link.remote_queue_bits,
                topology_available=not link.topology_available,
                field_age_s=99.0 - link.field_age_s,
            )
            for link in scenario.links
        )
    )
    for level in ("local_queue_direction", "link_rate_availability"):
        original = observe(scenario, level)
        altered = observe(changed, level)
        assert altered == original
        assert choose_link(altered) == choose_link(original)


def test_ladder_decision_changes_only_when_visible_fields_change():
    scenario = _scenario()
    assert choose_link(observe(scenario, "local_queue_direction")) == EDGE_A
    assert choose_link(observe(scenario, "link_rate_availability")) == EDGE_B
    assert choose_link(observe(scenario, "remote_queue_topology")) == EDGE_B
    assert choose_link(observe(scenario, "field_age")) == EDGE_A


def test_negative_controls_are_explicit_and_deterministic():
    scenario = _scenario()
    shuffled = observe(scenario, "remote_queue_topology", shuffle_remote=True)
    original_values = sorted(
        observation["remote_queue_bits"]
        for observation in observe(scenario, "remote_queue_topology").as_dict().values()
    )
    shuffled_values = sorted(
        observation["remote_queue_bits"]
        for observation in shuffled.as_dict().values()
    )
    assert shuffled_values == original_values
    assert shuffled.as_dict()[EDGE_A]["remote_queue_bits"] != \
        observe(scenario, "remote_queue_topology").as_dict()[EDGE_A]["remote_queue_bits"]

    fixed = observe(scenario, "field_age", fixed_age_s=1.0)
    assert {row["field_age_s"] for row in fixed.as_dict().values()} == {1.0}

    with pytest.raises(ValueError, match="unknown information level"):
        observe(scenario, "not-a-level")


def test_demo_evidence_reports_contract_not_paper_effect():
    result = run_demo()
    assert result["levels"] == list(INFO_LEVELS)
    assert result["selected_edges"] == [EDGE_A, EDGE_B, EDGE_B, EDGE_A]
    assert result["negative_controls"]["shuffle_remote_queue"]["preserves_multiset"]
    assert result["negative_controls"]["fixed_age"]["all_age_s"] == [1.0, 1.0]
    assert result["claim_boundary"]
