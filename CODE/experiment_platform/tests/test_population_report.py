"""Offline report arithmetic must retain unsuccessful packets."""
import copy
import pytest
from CODE.experiment_platform.report_population_study import extract
from CODE.experiment_platform.tests.test_population_run_acceptance import _arm


def test_report_distinguishes_capped_loss_deadline_miss_and_delivery_conditioning():
    arm=_arm()
    arm['outcome']['admitted']=3
    arm['replay']['decision_rows']=[]
    packets, candidates, ages, summary, events=extract({'arms':[arm]})
    assert len(packets)==3
    assert summary[0]['D4_capped_loss']==pytest.approx(2/3)
    assert summary[0]['not_delivered_within_4s_fraction']==pytest.approx(1/3)
    assert sum(p['delivered_latency_s'] is None for p in packets)==1
    assert not candidates and not ages
    assert len(events)==5


def test_report_rejects_future_information_in_recorded_snapshot():
    arm=_arm();arm['outcome']['admitted']=3
    arm['replay']['decision_rows']=[{'kind':'forward','observation_at_start':{
        'time_alignment':{'snapshot_at':2.},'neighbours':{
            '1':{'received_at':3.,'generated_at':1.}}}}]
    with pytest.raises(ValueError,match='future advertisement'):
        extract({'arms':[arm]})
