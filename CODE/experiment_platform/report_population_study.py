"""Offline figures and CSVs from independently accepted, complete T1 data."""
from __future__ import annotations
import argparse
import collections
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics

from CODE.experiment_platform import population_run_acceptance as acceptance, t1_suite


def write_csv(path, rows, fields=None):
    fields = fields or list(dict.fromkeys(key for row in rows for key in row))
    with path.open('x', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def extract(document):
    packets, candidates, ages, summaries, events = [], [], [], [], []
    for arm in document['arms']:
        name, replay = arm['arm'], arm['replay']
        emitted = {e['pid']: e for e in replay['packet_events'] if e['kind'] == 'packet_emitted'}
        delivered = {e['pid']: e['at'] for e in replay['packet_events'] if e['kind'] == 'delivered'}
        own_packets = []
        for pid, ev in emitted.items():
            fate = replay['fates'][str(pid)]
            latency = delivered[pid] - ev['at'] if pid in delivered else None
            d4 = acceptance.deadline_loss(fate, emit_time_s=ev['at'],
                delivered_at_s=delivered.get(pid), stop_time_s=arm['stop_time_s'], deadline_s=4.)
            if d4['status'] != 'COMPUTED':
                raise ValueError('D4 not fully observed')
            row = {'arm': name, 'pid': pid, 'emit_s': ev['at'], 'bits': ev['bits'],
                'fate': fate, 'delivered_at_s': delivered.get(pid), 'delivered_latency_s': latency,
                'D4_capped_loss': d4['value'],
                'not_delivered_within_4s': int(latency is None or latency > 4.)}
            own_packets.append(row)
        packets.extend(own_packets)
        for e in replay['packet_events']:
            if e['kind'] in ('packet_emitted', 'satellite_ingress', 'delivered'):
                events.append({'arm':name,'kind':e['kind'],'at_s':e['at'],'pid':e['pid']})
        for row in replay['decision_rows']:
            if row.get('kind') != 'forward':
                continue
            obs = row.get('observation_at_start') or {}
            audit = obs.get('time_alignment') or {}
            t0 = audit.get('snapshot_at')
            if not isinstance(t0, (int,float)):
                continue
            eta = audit.get('eta_targets') or {}
            finite_eta = [v for v in eta.values() if isinstance(v,(int,float)) and math.isfinite(v)]
            spread = max(finite_eta)-min(finite_eta) if len(finite_eta)>=2 else None
            for origin, adv in (obs.get('neighbours') or {}).items():
                if adv['received_at'] > t0 + 1e-9 or adv['generated_at'] > t0 + 1e-9:
                    raise ValueError('future advertisement in decision snapshot')
                ages.append({'arm':name,'decision_id':row.get('decision_id'),
                    'origin':origin,'t0_s':t0,'source_s':adv['generated_at'],
                    'received_s':adv['received_at'],'source_age_s':t0-adv['generated_at'],
                    'transport_age_s':adv['received_at']-adv['generated_at']})
            for direction, score in (audit.get('scores') or {}).items():
                resource = (obs.get('candidate_resources') or {}).get(direction) or {}
                peer = resource.get('peer')
                adv = (obs.get('neighbours') or {}).get(str(peer)) or {}
                history=[]
                for sample in adv.get('advertised_history') or []:
                    if sample['received_at'] > t0 + 1e-9:
                        raise ValueError('future history in snapshot')
                    d = resource.get('egress_direction')
                    if resource.get('kind') != 'isl':
                        continue  # separately mark missing terminal resource diagnostics
                    if (sample.get('advertised_isl_generation') or {}).get(d) != resource.get('egress_generation'):
                        continue
                    bits=(sample.get('advertised_isl_work_ahead_bits_proxy') or {}).get(d)
                    if bits is not None:
                        history.append((sample['generated_at'], sample['received_at'], bits))
                distinct={}
                for measured,received,bits in history:
                    if measured not in distinct or received >= distinct[measured][0]:
                        distinct[measured]=(received,bits)
                samples=[(t,distinct[t][1]) for t in sorted(distinct)][-8:]
                diffs=[(b[1]-a[1])/(b[0]-a[0]) for a,b in zip(samples,samples[1:])]
                target=(audit.get('query_targets') or {}).get(direction)
                candidates.append({'arm':name,'decision_id':row.get('decision_id'),
                    'pid':row.get('pid'),'t0_s':t0,'direction':direction,
                    'resource_kind':resource.get('kind'),'resource_peer':peer,
                    'egress_direction':resource.get('egress_direction'),
                    'generation':resource.get('egress_generation'),
                    'query_target_s':target,'eta_target_s':eta.get(direction),
                    'candidate_eta_spread_s':spread,
                    'query_offset_s':target-t0 if isinstance(target,(int,float)) else None,
                    'resource_work_s':(score.get('terms') or {}).get('resource_work_s'),
                    'score_total_s':score.get('total_s'),'fallback':bool(score.get('fallback')),
                    'missing':','.join(score.get('missing') or []),
                    'history_samples':len(samples),
                    'history_span_s':samples[-1][0]-samples[0][0] if samples else None,
                    'history_work_range_bits':max(v for _,v in samples)-min(v for _,v in samples) if samples else None,
                    'history_median_slope_bits_s':statistics.median(diffs) if diffs else None})
        own_candidates=[r for r in candidates if r['arm']==name]
        scored=[r['resource_work_s'] for r in own_candidates if r['resource_work_s'] is not None]
        changing=[r for r in own_candidates if r['history_work_range_bits'] is not None]
        summaries.append({'arm':name,'offered':len(own_packets),
            'admitted':arm['outcome']['admitted'],'delivered':len(delivered),
            'D4_capped_loss':statistics.fmean(r['D4_capped_loss'] for r in own_packets),
            'not_delivered_within_4s_fraction':statistics.fmean(r['not_delivered_within_4s'] for r in own_packets),
            'candidate_rows':len(own_candidates),'resource_work_observed':len(scored),
            'resource_work_nonzero':sum(x>0 for x in scored),
            'isl_history_observed':len(changing),
            'isl_history_varying':sum(r['history_work_range_bits']>0 for r in changing)})
    return packets,candidates,ages,summaries,events


def make_report(run_dir, out_dir, scope=None):
    run_dir,out_dir=Path(run_dir).absolute(),Path(out_dir)
    if out_dir.exists():
        raise ValueError('report output must be a new directory')
    accepted=acceptance.accept_run(run_dir,scope)
    cell=accepted['identity']['cell_id']
    path=run_dir/'t1-development'/'b_dev'/'cells'/cell/'result.json'
    inspected=t1_suite._inspect_result(path)
    if inspected['sha256'] != accepted['identity']['result_sha256']:
        raise ValueError('result changed after acceptance')
    packets,candidates,ages,summary,events=extract(inspected['payload']['document'])
    for row in summary:
        expected=accepted['arms'][row['arm']]['deadline_primary_loss']['value']
        if abs(row['D4_capped_loss']-expected)>1e-9:
            raise ValueError('CSV D4 differs from independent acceptance')
    out_dir.mkdir()
    for name,rows in [('packets',packets),('candidate_diagnostics',candidates),
                      ('advertisement_ages',ages),('summary',summary),('packet_events',events)]:
        write_csv(out_dir/f'{name}.csv',rows)
    (out_dir/'acceptance.json').write_text(json.dumps(accepted,ensure_ascii=False,indent=2)+'\n')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    arms=list(acceptance.ARMS); colors=['#0072B2','#E69F00','#009E73','#CC79A7']
    fig,axes=plt.subplots(1,3,figsize=(13,4))
    for ax,key,label in zip(axes,['D4_capped_loss','not_delivered_within_4s_fraction','delivered'],
                            ['Mean capped loss D4 (lower better)','Not delivered within 4 s / offered','Delivered packets by stop 8 s']):
        vals=[r[key] for r in summary]; ax.bar(arms,vals,color=colors);ax.set_title(label)
        ax.set_ylim(bottom=0,top=1 if key!='delivered' else max(r['offered'] for r in summary))
        for x,y in zip(arms,vals):ax.annotate(f'{y:.6g}',(x,y),xytext=(0,4),textcoords='offset points',ha='center')
    fig.suptitle(f'{run_dir.name} | seed 7 | complete receipt + packet reconciliation',fontsize=11)
    fig.tight_layout();fig.savefig(out_dir/'01_outcomes.png',dpi=180);fig.savefig(out_dir/'01_outcomes.pdf');plt.close(fig)
    fig,axes=plt.subplots(1,3,figsize=(13,4))
    def ecdf(ax,vals,label,color):
        vals=sorted(v for v in vals if v is not None and math.isfinite(v))
        if vals:ax.step(vals,np.arange(1,len(vals)+1)/len(vals),where='post',label=f'{label} (n={len(vals)})',color=color)
    for arm,color in zip(arms,colors):
        ecdf(axes[0],[r['source_age_s'] for r in ages if r['arm']==arm],arm,color)
        ecdf(axes[1],[r['query_offset_s'] for r in candidates if r['arm']==arm],arm,color)
        ecdf(axes[2],[r['resource_work_s'] for r in candidates if r['arm']==arm],arm,color)
    for ax,label in zip(axes,['Received advertisement source age (s)','Actual query time minus t0 (s)','Scored target-resource work (s)']):
        ax.set_xlabel(label);ax.set_ylabel('ECDF');ax.legend(fontsize=7);ax.grid(alpha=.2)
    fig.suptitle('Recorded online information and score components; overlapping curves retained')
    fig.tight_layout();fig.savefig(out_dir/'02_information.png',dpi=180);fig.savefig(out_dir/'02_information.pdf');plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(11,4))
    for arm,color in zip(arms,colors):
        ecdf(axes[0],[r['delivered_latency_s'] for r in packets if r['arm']==arm],arm,color)
        for kind,style in [('packet_emitted','-'),('satellite_ingress',':'),('delivered','--')]:
            times=sorted(r['at_s'] for r in events if r['arm']==arm and r['kind']==kind)
            axes[1].step(times,range(1,len(times)+1),where='post',color=color,linestyle=style,label=f'{arm} {kind}')
    axes[0].set_xlabel('E2E latency among delivered packets (s)');axes[0].set_ylabel('Conditional ECDF');axes[0].legend(fontsize=7)
    axes[1].set_xlabel('Simulation time (s)');axes[1].set_ylabel('Cumulative packets');axes[1].legend(fontsize=6)
    fig.suptitle('Delivery conditioning shown explicitly; no independence or benefit claim')
    fig.tight_layout();fig.savefig(out_dir/'03_delivery.png',dpi=180);fig.savefig(out_dir/'03_delivery.pdf');plt.close(fig)
    manifest={'status':'ACCEPTED_DATA_DESCRIPTIVE_REPORT','identity':accepted['identity'],
        'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'summary':summary,
        'limits':['Single seed, no statistical significance claim.',
                  'D4 is capped latency loss, not packet-loss probability.',
                  'Future-truth errors and causal counterfactuals are NOT reconstructed by this report.',
                  'Candidate rows are correlated within decisions and trajectories; not independent replicates.'],
        'files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out_dir.iterdir())}}
    (out_dir/'report.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--study-design-dir',type=Path);p.add_argument('--block')
    a=p.parse_args()
    if bool(a.study_design_dir)!=bool(a.block):p.error('study directory and block must be paired')
    scope=acceptance.load_study_scope(a.study_design_dir,a.block) if a.block else None
    r=make_report(a.run_dir,a.out,scope)
    print(json.dumps({'status':r['status'],'out':str(a.out)}))

if __name__=='__main__':main()
