"""Pure contract tests: no raster compilation, simulator, or performance run."""
import dataclasses
import json
from pathlib import Path
import pytest
from CODE.experiment_platform import population_run_acceptance as acceptance
from CODE.experiment_platform import study_design


@pytest.fixture
def plan(tmp_path):
    target = tmp_path / 'plan'
    study_design.compile_study(study_design.ROOT / 'CODE/work/WP-T1-COMPLETE/time_alignment_study.yaml', target)
    data = {'simulator_calls': 0,
            'burst_trace_identical_across_predictor_and_advertisement_blocks': True,
            'blocks': [{'block': block, 'packets': acceptance.STUDY_EXPECTED_PACKETS[block],
                        'trace_sha256': 'a' * 64, 'rows_digest': 'b' * 64,
                        'population_sha256': acceptance.POPULATION_SHA256}
                       for block in acceptance.STUDY_BLOCKS]}
    (target / 'input-compilation.json').write_text(json.dumps(data))
    return target


def test_scopes_are_explicit_immutable_and_do_not_change_legacy(plan):
    old = acceptance._legacy_scope()
    steady = acceptance.load_study_scope(plan, 'primary_steady')
    burst = acceptance.load_study_scope(plan, 'primary_burst')
    assert (steady.expected_packets, burst.expected_packets) == (844, 9036)
    assert steady.cell_id == 'b-primary_steady-network-seed-7'
    assert steady.expected_resolved_config_sha256 != burst.expected_resolved_config_sha256
    assert acceptance._legacy_scope() == old
    assert old.expected_packets == 1236
    with pytest.raises(dataclasses.FrozenInstanceError):
        steady.expected_packets = 1


@pytest.mark.parametrize('change', ['count', 'raster', 'profile', 'deadline', 'duplicate', 'tracehash'])
def test_mismatched_prior_plan_is_rejected(plan, change):
    input_path = plan / 'input-compilation.json'
    design_path = plan / 'design.json'
    data, design = json.loads(input_path.read_text()), json.loads(design_path.read_text())
    if change == 'count':
        data['blocks'][0]['packets'] = 1236
    elif change == 'raster':
        data['blocks'][0]['population_sha256'] = 'c' * 64
    elif change == 'profile':
        path = plan / design['execution_blocks'][0]['profile']
        path.write_text(path.read_text() + '\n# changed after freeze\n')
    elif change == 'deadline':
        design['execution_blocks'][0]['driver_options']['--deadline-s'] = '5.0'
    elif change == 'duplicate':
        data['blocks'].append(data['blocks'][0])
    else:
        data['blocks'][0]['trace_sha256'] = 'not-a-digest'
    input_path.write_text(json.dumps(data))
    design_path.write_text(json.dumps(design))
    with pytest.raises(acceptance.PopulationRunAcceptanceError):
        acceptance.load_study_scope(plan, 'primary_steady')


def test_runtime_self_report_cannot_override_frozen_trace(plan, tmp_path):
    scope = acceptance.load_study_scope(plan, 'primary_burst')
    with pytest.raises(acceptance.PopulationRunAcceptanceError, match='trace SHA'):
        acceptance._rebuild_trace(tmp_path, tmp_path, {}, {}, {},
                                 {'trace_sha256': 'c' * 64}, scope)
