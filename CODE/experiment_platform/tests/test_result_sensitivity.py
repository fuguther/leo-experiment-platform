"""Regression cases for misleading winner-only sensitivity and ID matching."""
import copy
import importlib.util
from pathlib import Path


_path = Path(__file__).resolve().parents[2] / 'work/WP-T1-COMPLETE/make_result_figures.py'
_spec = importlib.util.spec_from_file_location('result_figures', _path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)


def fixture():
    entry = {'legal': ('N', 'E'), 'sat': 1, 't_decision_start': 2.0, 'chosen': 'N',
             'applied_order': ('N', 'E'), 'ranking': ('N', 'E'),
             'audit': {'scores': {
                 'N': {'missing': [], 'total_s': 0.01,
                       'terms': {'prop_s': 0.01, 'resource_work_s': 0.0}},
                 'E': {'missing': [], 'total_s': 0.02,
                       'terms': {'prop_s': 0.0, 'resource_work_s': 0.02}},
             }}}
    return {a: {'decisions': {(1, 1): copy.deepcopy(entry)}} for a in _module.ARMS}


def test_competitor_decrease_can_reverse_winner_without_winner_work_change():
    data = fixture()
    row = data['candidate']['decisions'][(1, 1)]
    row['audit']['scores']['E']['terms']['resource_work_s'] = 0.0
    row['audit']['scores']['E']['total_s'] = 0.0
    row['chosen'] = 'E'
    result = _module.sensitivity(data)
    assert len(result) == 1
    assert result[0]['work_shift_s'] > result[0]['margin_s']
    assert result[0]['chosen_differs']


def test_common_work_shift_does_not_change_relative_margin():
    data = fixture()
    for score in data['candidate']['decisions'][(1, 1)]['audit']['scores'].values():
        score['terms']['resource_work_s'] += 1
        score['total_s'] += 1
    assert abs(_module.sensitivity(data)[0]['work_shift_s']) < 1e-12


def test_same_id_at_different_satellite_is_not_a_paired_context():
    data = fixture()
    data['candidate']['decisions'][(1, 1)]['sat'] = 2
    assert _module.sensitivity(data) == []


def test_changed_nonwork_terms_are_not_query_time_effects():
    data = fixture()
    data['candidate']['decisions'][(1, 1)]['audit']['scores']['N']['terms']['prop_s'] += 0.01
    assert _module.sensitivity(data) == []
