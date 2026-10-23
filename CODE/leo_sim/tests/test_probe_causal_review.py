"""Independent causal and cost invariants for the first population probe."""
from types import SimpleNamespace as NS
from pathlib import Path
import pytest
from CODE.leo_sim import config, kernel, time_alignment as ta
from CODE.leo_sim.tests.test_time_alignment_online import A, B, _geo, _forward_rows
from CODE.leo_sim.tests.helpers import make_cfg, row

def test_population_cost_smoke_profile_freezes_extended_ad_protocol_v2():
    profile = (Path(__file__).resolve().parents[1] / "profiles"
               / "t1_population_region_cost_smoke.yaml")
    resolved = config.load_config_file(str(profile))
    assert resolved["config"]["control_plane"][
        "advertisement_protocol_version"] == 2

def test_named_isl_resource_charges_own_packet_service_once():
    r1=ta.ResourceKey(1,'E','isl',generation=1)
    r2=ta.ResourceKey(2,'E','isl',generation=1)
    snap=ta.make_snapshot(satellite=0,snapshot_at=.2,
        history=(ta.StateSample(r1,0,.1,0,1000),ta.StateSample(r2,0,.1,0,1000000)),
        legal_directions=('N','E'),resources={'N':r1,'E':r2},
        egress_queue_bits={'N':0,'E':0},local_egress_in_service_s={'N':0,'E':0},
        link_rate_bps={'N':1e6,'E':1e6},link_propagation_s={'N':0,'E':0},
        peer_process_s={'N':0,'E':0},remaining_prop_s={'N':0,'E':0},
        resource_service_rate_bps={'N':1000,'E':1000000},pkt_bits=12000,
        arm='candidate')
    scored=ta.plan_decision(snap).scored.by_direction()
    assert scored['N'].total_s == pytest.approx(.012+12.)
    assert scored['E'].total_s == pytest.approx(.012+.012)

def test_local_active_transmission_residual_uses_its_start_rate():
    link=NS(current=NS(bits=100000),k=NS(_link_rate=lambda *a,**kw:2e6),
        sat=0,peer=1,_svc_phase='transmitting',_tx_started_at=0.,
        _service_rate_bps=1e6)
    assert kernel.Kernel._isl_in_service_s(link,.025) == pytest.approx(.075)

def test_local_active_transmission_without_start_rate_is_unknown():
    link=NS(current=NS(bits=100000),k=NS(_link_rate=lambda *a,**kw:2e6),
        sat=0,peer=1,_svc_phase='transmitting',_tx_started_at=0.)
    assert kernel.Kernel._isl_in_service_s(link,.025) is None

def _one_peer_run(packet_count=1, ad_protocol=2):
    cfg=make_cfg({'scenario':{'duration_s':10.,'num_satellites':2,'num_planes':1},
        'demand':{'packet_bits':8000},'links':{'isl_rate_mbps':5.},
        'access':{'uplink_rate_mbps':1000.,'downlink_rate_mbps':100.,
            'idle_release_s':100.,'slot_lease_s':100.},
        'control_plane':{'enabled':True,'advertisement_protocol_version':ad_protocol,
            'advertise_interval_s':.2,'ttl_s':10.,
            'vis_k':2,'packet_bits':10000},'routing':{'policy':'hop'},
        'execution':{'decision_observation_mode':'frozen','compute_delay_s':.1},
        'time_alignment':{'enabled':True,'arm':'candidate'}})
    decisions=[];timeline=[]
    sim=kernel.Kernel(cfg,[row(71+i,0.,A,B,bits=8000) for i in range(packet_count)],
        geometry=_geo(),decision_sink=decisions,timeline_sink=timeline)
    result=sim.run()
    return result,decisions,timeline

def test_peer_resource_entry_includes_peer_decision_or_is_explicitly_unknown():
    result,decisions,timeline=_one_peer_run()
    assert result['fates'][71]=='DELIVERED'
    f=next(r for r in _forward_rows(decisions) if r['pid']==71)
    audit=f['observation_at_start']['time_alignment']
    actual=next(r['at'] for r in timeline if r.get('pid')==71
        and r.get('milestone')=='queue_enter' and r.get('queue')=='downlink')
    if audit['eta_unknown_terms']['E']:
        assert audit['scores']['E']['fallback'] is True
    else:
        assert audit['eta_targets']['E']==pytest.approx(actual,abs=1e-8)

def test_protocol_v1_without_peer_processing_is_unknown_with_shared_fallback():
    result,decisions,timeline=_one_peer_run(ad_protocol=1)
    assert result['fates'][71]=='DELIVERED'
    f=next(r for r in _forward_rows(decisions) if r['pid']==71)
    audit=f['observation_at_start']['time_alignment']
    assert audit['eta_targets']['E'] is None or \
        'peer_process_s' in audit['eta_unknown_terms']['E']
    assert audit['scores']['E']['fallback'] is True

def test_missing_received_peer_processing_ad_is_unknown_with_shared_fallback():
    cfg=make_cfg({'scenario':{'duration_s':10.,'num_satellites':2,'num_planes':1},
        'demand':{'packet_bits':8000},'links':{'isl_rate_mbps':5.},
        'access':{'uplink_rate_mbps':1000.,'downlink_rate_mbps':100.,
            'idle_release_s':100.,'slot_lease_s':100.},
        'control_plane':{'enabled':True,'advertisement_protocol_version':2,
            'advertise_interval_s':.2,'ttl_s':10.,
            'vis_k':2,'packet_bits':10000},'routing':{'policy':'hop'},
        'execution':{'decision_observation_mode':'frozen','compute_delay_s':.1},
        'time_alignment':{'enabled':True,'arm':'candidate'}})
    decisions=[];timeline=[]
    sim=kernel.Kernel(cfg,[row(73,0.,A,B,bits=8000)],geometry=_geo(),
        decision_sink=decisions,timeline_sink=timeline)
    original=sim._advertisement_history
    def without_processing(sat,peer,now,*,force=False):
        return [{**record,'advertised_processing':None}
            for record in original(sat,peer,now,force=force)]
    sim._advertisement_history=without_processing
    result=sim.run()
    assert result['fates'][73]=='DELIVERED'
    forward=next(r for r in _forward_rows(decisions) if r['pid']==73)
    audit=forward['observation_at_start']['time_alignment']
    assert 'peer_process_s' in audit['eta_unknown_terms']['E']
    assert audit['scores']['E']['fallback'] is True

def test_query_queue_wait_excludes_prior_compute_interval():
    _,_,timeline=_one_peer_run()
    queries=[r for r in timeline if r.get('milestone')=='query_start']
    assert queries
    for r in queries:
        assert r['wait_s']==pytest.approx(0.,abs=1e-10)
        assert r['requested_at']==pytest.approx(r['started_at'],abs=1e-10)

def test_each_decision_query_charge_is_local_under_two_concurrent_packets():
    _,decisions,_=_one_peer_run(packet_count=2)
    forwards=_forward_rows(decisions)
    assert {r['pid'] for r in forwards}=={71,72}
    for r in forwards:
        q=r['observation_at_start']['time_alignment']['query']
        assert q['requests']==1
        assert q['service_s']==pytest.approx(1e-6,abs=1e-10)
