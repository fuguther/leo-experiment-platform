"""Build a self-contained, event-based HTML replay from frozen T1 results."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ARMS = ("stale", "now", "common", "candidate")


class ReplayError(ValueError):
    """Required real event evidence is absent or inconsistent."""


def _read_json(path):
    try:
        from CODE.experiment_platform.replay_codec import read_document
        value = read_document(path)
    except (OSError, ValueError) as exc:
        raise ReplayError(f"cannot read JSON result {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReplayError(f"result must be a JSON object: {path}")
    return value


def _queue_series(replay):
    states = {}
    result = []
    events = list(replay.get("queue_state_events") or [])
    events = [(index, event) for index, event in enumerate(events)
              if isinstance(event, dict) and isinstance(
                  event.get("at"), (int, float))]
    events.sort(key=lambda pair: (float(pair[1]["at"]), pair[0]))
    for _index, event in events:
        key = "%s@g%s" % (event.get("resource_id"),
                           event.get("generation", "unknown"))
        states[key] = event
        result.append({
            "at": float(event["at"]),
            "queued_data_bits": sum(int(v.get("queued_data_bits") or 0)
                                     for v in states.values()),
            "queued_control_bits": sum(int(v.get("queued_control_bits") or 0)
                                        for v in states.values()),
            "in_service_bits": sum(int(v.get("in_service_bits") or 0)
                                    for v in states.values()),
            "resource_instance_count": len(states),
            "source": "exact kernel queue_state events; one latest state per directed ISL generation",
        })
    return result


def _population_events(summary, replay):
    manifest = summary.get("packet_manifest") or []
    od_by_pid = {int(item["pid"]): str(item.get("od_id") or "unknown")
                 for item in manifest}
    events = []
    for index, item in enumerate(manifest):
        events.append((float(item["emit_time_s"]), index, int(item["pid"]),
                       "emit", od_by_pid[int(item["pid"])]))
    terminal_seen = set()
    seq = len(events)
    for row in replay.get("timeline_rows") or []:
        if (row.get("milestone") != "packet_fate"
                or row.get("pid") is None
                or not isinstance(row.get("at"), (int, float))):
            continue
        pid = int(row["pid"])
        if pid not in od_by_pid:
            continue
        terminal_seen.add(pid)
        events.append((float(row["at"]), seq, pid,
                       "terminal", od_by_pid[pid]))
        seq += 1
    result_doc = replay.get("result") or {}
    fates = result_doc.get("fates") or {}
    stop_at = float(result_doc.get("stop_time_s") or
                    result_doc.get("horizon_s") or 0.0)
    # System-resident packets receive their authoritative IN_SYSTEM_AT_STOP
    # fate at the exact simulation stop; the kernel does not invent an earlier
    # timestamp for administrative censoring.
    for raw_pid, fate in fates.items():
        pid = int(raw_pid)
        if pid not in od_by_pid or pid in terminal_seen:
            continue
        events.append((stop_at, seq, pid, "terminal", od_by_pid[pid]))
        seq += 1
    events.sort(key=lambda row: (row[0], row[1]))
    counts, series = {}, []
    delivered, terminal = 0, 0
    for at, _index, _pid, kind, od_id in events:
        bucket = counts.setdefault(od_id, 0)
        counts[od_id] = bucket + (1 if kind == "emit" else -1)
        if kind == "terminal":
            terminal += 1
            # Delivered packets are counted from the kernel's exact fate map.
            if fates.get(str(_pid), fates.get(_pid)) == "DELIVERED":
                delivered += 1
        series.append({"at": at, "active_by_od": dict(counts),
                       "active_total": sum(counts.values()),
                       "terminal_total": terminal,
                       "delivered_total": delivered,
                       "source": "synthetic packet manifest plus exact packet_fate/stop-time events"})
    return series


def _arm_payload(row, summary):
    replay = row.get("replay") or {}
    if replay.get("captured") is not True:
        raise ReplayError(f"network arm {row.get('arm')} has no captured replay")
    replay = dict(replay)
    replay["result"] = {
        "fates": replay.get("fates") or {},
        "stop_time_s": row.get("stop_time_s"),
        "horizon_s": row.get("horizon_s"),
    }
    if (not isinstance(replay["result"]["stop_time_s"], (int, float))
            or not isinstance(replay["result"]["horizon_s"], (int, float))):
        raise ReplayError("captured arm is missing exact stop/horizon times")
    replay["queue_series"] = _queue_series(replay)
    replay["population_series"] = _population_events(summary, replay)
    return {"arm": row.get("arm"), "scope": row.get("scope"),
            "outcome": row.get("outcome"), "cost": row.get("total_cost"),
            "replay": replay,
            "routing_audit_log": row.get("routing_audit_log") or {}}


def _embedded_json(value):
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            .replace("&", "\\u0026").replace("<", "\\u003c")
            .replace(">", "\\u003e").replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029"))


def build_document(network_payload, branch_payload, *, run_identity=None):
    network = (network_payload or {}).get("document") or {}
    branch = (branch_payload or {}).get("document") or {}
    if network.get("status") != "ok":
        raise ReplayError("four-arm network result is not complete")
    source = network.get("source") or {}
    workload = source.get("synthetic_workload") or {}
    summary = workload.get("summary") or {}
    if len(summary.get("flows") or []) != 6:
        raise ReplayError("replay requires the frozen six-directed-OD source manifest")
    if not summary.get("compiled_od_mapping"):
        raise ReplayError("replay requires the OD-to-compiled-platform endpoint map")
    rows = {row.get("arm"): row for row in network.get("arms") or []}
    if set(rows) != set(ARMS):
        raise ReplayError(f"expected all four information arms, got {sorted(rows)}")
    arm_data = [_arm_payload(rows[name], summary) for name in ARMS]
    explanation = branch.get("explanation_replay") or {}
    if explanation.get("captured") is not True:
        raise ReplayError("the structurally selected same-snapshot branch was not captured")
    return {
        "schema": "t1-event-replay/v1",
        "run_identity": run_identity or {},
        "scenario": source.get("scenario"),
        "seed": summary.get("seed"),
        "trace_sha256": source.get("trace_sha256"),
        "config_sha256": source.get("config_sha256"),
        "platform": {"profile": "t1_dev_regional_multiod.yaml",
                     "num_satellites": 24,
                     "num_planes": 3,
                     "direction_order": ["N", "E", "S", "W"],
                     "regional_business_endpoints_only": True,
                     "note": "regional OD endpoints; complete orbital platform, dynamic links and routing path retained"},
        "workload": summary,
        "arms": arm_data,
        "branch": {
            "selection": branch.get("explanation_selection"),
            "offline_diagnostic": explanation.get("offline_diagnostic"),
            "target_decision": explanation.get("target_decision"),
            "target_packet_id": explanation.get("target_packet_id"),
            "shared_baseline": explanation.get("shared_baseline"),
            "forced_candidate_branches": explanation.get(
                "forced_candidate_branches"),
            "information_arms": explanation.get("information_arms"),
            "candidate_outcomes": explanation.get("candidate_outcomes"),
        },
    }


HTML = r'''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>T1 区域多 OD 事件回放</title>
<style>
:root{color-scheme:light;--ink:#122235;--muted:#617181;--line:#d7e0e8;--panel:#fff;--bg:#f2f6f9;--accent:#087f8c;--orange:#db7b27}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}header{padding:24px 4vw 14px;background:#102a43;color:#fff}h1{margin:0 0 6px;font-size:24px}header p{margin:0;color:#c7d5e0}.wrap{max-width:1500px;margin:auto;padding:18px 3vw 44px}.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px;margin:12px 0;box-shadow:0 2px 8px #0e294212}.identity{font:12px/1.6 ui-monospace,SFMono-Regular,monospace;color:#45586a;overflow-wrap:anywhere}.toolbar{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.toolbar input{flex:1;min-width:220px}.toolbar button{background:var(--accent);color:white;border:0;border-radius:8px;padding:8px 14px;cursor:pointer}.grid4{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.arm{border:1px solid var(--line);border-radius:10px;padding:12px;min-width:0}.arm h3{margin:0 0 5px}.arm svg{width:100%;height:auto;background:#fbfdff;border:1px solid #e6edf2;border-radius:8px}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin:8px 0}.stat{background:#f1f6f8;border-radius:7px;padding:6px 8px;font-size:12px}.stat b{display:block;font-size:15px}.small{font-size:12px;color:var(--muted)}.table-wrap{overflow:auto}table{border-collapse:collapse;width:100%;font-size:12px}th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}th{background:#f5f8fa;position:sticky;top:0}.tag{display:inline-block;border-radius:20px;padding:2px 7px;background:#e8f2f4;font-size:11px}.warn{color:#8a4e0d;background:#fff3dc;padding:8px 10px;border-radius:7px}.good{color:#12623d}.bad{color:#9b3126}canvas{width:100%;height:145px;display:block}.cols{display:grid;grid-template-columns:1fr 1fr;gap:12px}.direction{font:11px ui-monospace,monospace}.details{max-height:480px;overflow:auto}.chain{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.45 ui-monospace,SFMono-Regular,monospace;background:#f7fafc;padding:10px;border-radius:7px}.od-now{font-variant-numeric:tabular-nums}.timebar{position:relative;height:22px;min-width:220px;margin:4px 0;background:linear-gradient(90deg,#f3f7f9,#e9f0f4);border-bottom:1px solid #738796}.timebar:before,.timebar:after{position:absolute;top:21px;font:10px ui-monospace,monospace;color:#687b89}.timebar:before{content:"0s";left:0}.timebar:after{content:attr(data-end);right:0}.tm{position:absolute;top:0;bottom:0;width:2px;transform:translateX(-1px);background:#334}.tm.measure{background:#16824b}.tm.decision{background:#153e66}.tm.query{background:#8a62ba}.tm.predicted{background:#db7b27}.tm.actual{background:#c74343}.tm.propagation{background:#8c3a71}.time-legend{font-size:10px;color:#506475}.time-legend b{white-space:nowrap;margin-right:6px}.time-legend .measure{color:#16824b}.time-legend .decision{color:#153e66}.time-legend .query{color:#8a62ba}.time-legend .predicted{color:#db7b27}.time-legend .actual{color:#c74343}.time-legend .propagation{color:#8c3a71}@media(max-width:850px){.grid4,.cols{grid-template-columns:1fr}.stats{grid-template-columns:repeat(2,1fr)}}
</style>
<header><h1>区域多 OD 并发：真实事件回放</h1><p id="subtitle"></p></header>
<main class="wrap">
<section class="card"><div class="identity" id="identity"></div><p class="warn">节点按编号作示意布局；连边、拓扑快照、队列和包事件取自对应仿真臂的原始日志。四臂在选择分化后分别演化。右侧下钻的同快照强制分支是离线诊断，不能解释为在线可用信息。</p>
<div class="toolbar"><button id="play">播放</button><button id="pause">暂停</button><label>统一仿真时钟 <b id="timeText">0.0 s</b></label><input id="clock" type="range" min="0" max="50" step="0.1" value="0"></div></section>
<section class="card"><h2>完整 24 星网络与并发演化</h2><p class="small">四个面板共用同一时钟。上图按当前臂真实拓扑快照绘制 24 个卫星节点和实际链路；下方分别显示精确 ISL 队列/在服务比特阶梯与仍在系统的包数。业务源/目的限于区域站点，路由可经过区域外卫星。</p><div id="armGrid" class="grid4"></div></section>
<section class="card"><h2>六个有向 OD：源流定义与各臂当前活跃包</h2><div id="odTable" class="table-wrap"></div></section>
<section class="card"><h2>包与决策下钻</h2><p id="targetNote" class="small"></p><label>查看包 PID <select id="packetSelect"></select></label><div class="cols"><div><h3>该包在四个独立全网臂中的真实决策（点选一条查看其收到信息）</h3><div id="armDecisions" class="table-wrap details"></div></div><div><h3>结构采样包的同快照 N/E/S/W 候选与离线反事实</h3><div id="candidateTable" class="table-wrap details"></div></div></div><h3>选中决策实际收到的历史测量</h3><div id="measurements" class="table-wrap"></div><h3>选中包真实事件时序：收到信息 → 决策 → 资源使用 → 交付/终止</h3><div id="chain" class="chain"></div></section>
<section class="card"><h2>结果与成本联动</h2><div id="summaryTable" class="table-wrap"></div><p class="small">时延、期限损失和吞吐取自每臂逐包 outcome 表；成本分开呈现前台计算、后台更新、查询服务和预计算建表。预计算基线可以零决策作业但不因此省略其建表/更新范围。</p></section>
</main><script id="data" type="application/json">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById('data').textContent), names={stale:'陈旧状态',now:'当前补偿',common:'统一未来',candidate:'候选使用时刻'};const $=s=>document.querySelector(s), fmt=(x,n=3)=>x===null||x===undefined?'未知':(Number.isFinite(Number(x))?Number(x).toFixed(n):String(x)), esc=x=>String(x??'未知').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));$('#subtitle').textContent=`场景 ${D.scenario} · seed ${D.seed} · trace ${D.trace_sha256||'未知'}`;$('#identity').textContent=`执行身份：${JSON.stringify(D.run_identity)}\n配置 SHA256：${D.config_sha256||'未知'}\n业务包数：${D.workload.packet_count} · 有向 OD：${D.workload.unique_od_count} · 平台：${D.platform.num_satellites} 星 / ${D.platform.num_planes} 面 / N-E-S-W`;
const duration=Math.max(50,...D.arms.map(a=>(a.scope||{}).duration_s||0));$('#clock').max=duration;const armEls={};const ns='http://www.w3.org/2000/svg';function el(tag,attrs={}){const x=document.createElementNS(ns,tag);for(const[k,v]of Object.entries(attrs))x.setAttribute(k,v);return x}
function snapAt(replay,t){let found=null;for(const s of replay.topology_trace||[]){if(Number(s.at)<=t)found=s;else break}return found}
function seriesAt(series,t){let lo=0,hi=series.length;while(lo<hi){let m=(lo+hi)>>1;if(Number(series[m].at)<=t)lo=m+1;else hi=m}return lo?series[lo-1]:null}
function activeState(replay,t){return seriesAt(replay.population_series||[],t)||{active_total:0,active_by_od:{},terminal_total:0,delivered_total:0}}
function qState(replay,t){return seriesAt(replay.queue_series||[],t)||{queued_data_bits:0,queued_control_bits:0,in_service_bits:0}}
function topologySvg(arm,t){const replay=arm.replay,s=snapAt(replay,t),svg=armEls[arm.arm].svg;while(svg.firstChild)svg.removeChild(svg.firstChild);if(!s){svg.append(el('text',{x:18,y:28,fill:'#667','font-size':13}));svg.lastChild.textContent='缺少可用拓扑快照';return}const peers=s.peer_by_satellite||{}, w=600,h=290, cols=6, nodes=24, pos={};for(let id=0;id<nodes;id++){const x=45+(id%cols)*100,y=35+Math.floor(id/cols)*64;pos[id]=[x,y]}const seen=new Set();for(const [sid,m]of Object.entries(peers)){for(const d of ['N','E','S','W']){const peer=m?.[d];if(peer===null||peer===undefined)continue;const a=Number(sid),b=Number(peer),key=[Math.min(a,b),Math.max(a,b)].join(':');if(seen.has(key))continue;seen.add(key);const p=pos[a],q=pos[b];if(!p||!q)continue;svg.append(el('line',{x1:p[0],y1:p[1],x2:q[0],y2:q[1],stroke:'#9ab0be','stroke-width':1.5,opacity:.72}))}}const pop=activeState(replay,t);const latestSat={};for(const r of (arm.routing_audit_log.decision_records||[])){if(Number(r.t_decision_start)<=t)latestSat[r.pid]=r.sat}for(let id=0;id<nodes;id++){const [x,y]=pos[id],focus=Object.values(latestSat).includes(id);svg.append(el('circle',{cx:x,cy:y,r:15,fill:focus?'#e1a34a':'#087f8c',stroke:'#fff','stroke-width':2}));const label=el('text',{x,y:y+4,'text-anchor':'middle',fill:'#fff','font-size':10,'font-family':'monospace'});label.textContent=String(id);svg.append(label)}const label=el('text',{x:12,y:278,fill:'#526777','font-size':11});label.textContent=`拓扑采样：${fmt(s.at,2)} s · ${s.reason||'event snapshot'} · 活跃包 ${pop.active_total||0}`;svg.append(label)}
function drawSeries(canvas,arm,t){const ctx=canvas.getContext('2d'),w=canvas.width=canvas.clientWidth*devicePixelRatio,h=canvas.height=146*devicePixelRatio;ctx.clearRect(0,0,w,h);const pad=30*devicePixelRatio,mid=h*.53,end=Math.max(duration,1);ctx.strokeStyle='#d9e3e8';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(pad,mid);ctx.lineTo(w-pad,mid);ctx.stroke();const qs=arm.replay.queue_series||[],ps=arm.replay.population_series||[];const qMax=Math.max(1,...qs.map(x=>x.queued_data_bits+x.queued_control_bits+x.in_service_bits));const pMax=Math.max(1,...ps.map(x=>x.active_total));function step(series,get,max,y0,y1,color){if(!series.length)return;ctx.strokeStyle=color;ctx.lineWidth=2*devicePixelRatio;ctx.beginPath();let began=false,prev=null;for(const r of series){const x=pad+(w-2*pad)*Math.max(0,Math.min(1,Number(r.at)/end));const value=get(r);const y=y1-(y1-y0)*value/max;if(!began){ctx.moveTo(x,y);began=true}else{ctx.lineTo(x,prev);ctx.lineTo(x,y)}prev=y}if(prev!==null){const x=pad+(w-2*pad)*Math.max(0,Math.min(1,t/end));ctx.lineTo(x,prev)}ctx.stroke()}step(qs,r=>r.queued_data_bits+r.queued_control_bits+r.in_service_bits,qMax,8*devicePixelRatio,mid-8*devicePixelRatio,'#087f8c');step(ps,r=>r.active_total,pMax,mid+8*devicePixelRatio,h-20*devicePixelRatio,'#db7b27');const x=pad+(w-2*pad)*Math.max(0,Math.min(1,t/end));ctx.setLineDash([4*devicePixelRatio,4*devicePixelRatio]);ctx.strokeStyle='#24394b';ctx.beginPath();ctx.moveTo(x,4);ctx.lineTo(x,h-4);ctx.stroke();ctx.setLineDash([]);ctx.fillStyle='#506475';ctx.font=`${10*devicePixelRatio}px system-ui`;ctx.fillText('精确 ISL 队列+在服务 (bits)',pad,11*devicePixelRatio);ctx.fillText('全网在系统包数',pad,mid+18*devicePixelRatio);ctx.fillText(`${fmt(qMax/1e6,2)} Mbit max`,w-pad-98*devicePixelRatio,11*devicePixelRatio);ctx.fillText(`${fmt(pMax,0)} max`,w-pad-55*devicePixelRatio,mid+18*devicePixelRatio)}
function setupPanels(){const grid=$('#armGrid');for(const arm of D.arms){const box=document.createElement('article');box.className='arm';box.innerHTML=`<h3>${esc(names[arm.arm]||arm.arm)}</h3><div class="small">same clock · ${esc(arm.arm)}</div><svg viewBox="0 0 600 290" role="img" aria-label="真实拓扑节点和链路"></svg><canvas></canvas><div class="stats"></div>`;grid.append(box);armEls[arm.arm]={box,svg:box.querySelector('svg'),canvas:box.querySelector('canvas'),stats:box.querySelector('.stats')}}}
function update(t){$('#timeText').textContent=fmt(t,1)+' s';$('#clock').value=t;for(const arm of D.arms){topologySvg(arm,t);drawSeries(armEls[arm.arm].canvas,arm,t);const p=activeState(arm.replay,t),q=qState(arm.replay,t);const stats=armEls[arm.arm].stats;stats.innerHTML=`<div class="stat">在系统包<b>${fmt(p.active_total,0)}</b></div><div class="stat">精确排队比特<b>${fmt((q.queued_data_bits+q.queued_control_bits)/1e6,3)} Mbit</b></div><div class="stat">在服务比特<b>${fmt(q.in_service_bits/1e6,3)} Mbit</b></div><div class="stat">已终态/交付<b>${fmt(p.terminal_total,0)} / ${fmt(p.delivered_total,0)}</b></div>`}renderOds(t);renderSummary();}
function renderOds(t){const flows=D.workload.flows||[],mapping=D.workload.compiled_od_mapping||{},sites=new Map((D.workload.sites||[]).map(s=>[s.id,s]));const cells=flows.map(flow=>{let html=`<tr><td>${esc(flow.id)}</td><td>${esc(flow.src)} → ${esc(flow.dst)}</td><td>${esc((mapping[flow.id]||{}).src_grid_id||'未知')} → ${esc((mapping[flow.id]||{}).dst_grid_id||'未知')}</td>`;for(const arm of D.arms){const st=activeState(arm.replay,t);html+=`<td class="od-now">${fmt(st.active_by_od?.[flow.id]||0,0)}</td>`}return html+'</tr>'}).join('');const phases=(D.workload.phases||[]).map(p=>`${p.id} ${p.start_s}–${p.end_s}s (${fmt(p.nominal_mbps,2)} Mbps)`).join(' → ');$('#odTable').innerHTML=`<p class="small">站点：${(D.workload.sites||[]).map(s=>`${esc(s.id)} (${s.lat}, ${s.lon})`).join(' · ')}<br>负载阶段：${phases}<br>合成业务来源：冻结OD率矩阵；各臂均由同一逐包输入轨迹开始。</p><table><thead><tr><th>有向 OD</th><th>站点</th><th>实际编译 endpoint cell</th>${D.arms.map(a=>`<th>${esc(names[a.arm])} 活跃包</th>`).join('')}</tr></thead><tbody>${cells}</tbody></table>`}
const targetPid=Number(D.branch.target_packet_id), target=D.branch.target_decision||{}, decisionId=target.decision_id;let activePid=targetPid, selectedDecision=null;$('#targetNote').textContent=`结构规则选取的离线分支目标：决策 ${decisionId}，包 ${targetPid}，卫星 ${target.sat}，t_dec=${fmt(target.t_decision_start,6)} s，动作 ${target.chosen}。选择不读取赢家或收益；其他包只显示真实全网轨迹，同快照分支仍只对应该结构采样包。各臂 decision_id 独立，不按编号强行对齐。`;
function renderArmDecisions(){const rows=[];for(const arm of D.arms){const decisions=(arm.routing_audit_log.decision_records||[]).filter(r=>Number(r.pid)===activePid);for(const r of decisions){const o=r.observation_at_start||{},ta=o.time_alignment||{},mask=r.four_direction_audit||o.four_direction_audit||{},selected=selectedDecision&&selectedDecision.arm===arm.arm&&selectedDecision.id===String(r.decision_id);rows.push(`<tr data-arm="${esc(arm.arm)}" data-decision="${esc(r.decision_id)}" style="cursor:pointer;${selected?'background:#e5f2f3':''}"><td>${esc(names[arm.arm])}</td><td>${fmt(r.t_decision_start,6)} / ${fmt(r.t_decision_commit,6)}</td><td>${r.sat}</td><td>${r.decision_id}</td><td>${esc((mask.route_candidates||[]).join(','))}</td><td>${esc((mask.committed_legal_directions||[]).join(','))}</td><td>${esc(r.chosen)}</td><td>${esc(JSON.stringify(ta.query_targets||{}))}</td></tr>`)}}$('#armDecisions').innerHTML=`<table><thead><tr><th>臂</th><th>开始/提交 s</th><th>卫星</th><th>本臂 decision_id</th><th>路由候选</th><th>物理/队列合法动作</th><th>动作</th><th>候选目标时刻</th></tr></thead><tbody>${rows.join('')||'<tr><td colspan="8">该包在某些臂中没有找到带路由审计的转发决策；保留缺失，不按编号补齐。</td></tr>'}</tbody></table>`}
function resourceId(candidate){if(!candidate||candidate.peer==null||candidate.egress_peer==null)return null;return `isl:${candidate.peer}:${candidate.egress_peer}`}
function branchUse(branchReplay,direction,candidate){if(!branchReplay)return null;const timeline=branchReplay.timeline_rows||[],pid=targetPid,preferred=resourceId(candidate);const events=timeline.filter(e=>e.pid===pid&&e.milestone==='queue_enter'&&String(e.link_id||'').startsWith('isl:'));const targetQueue=events.find(e=>e.link_id===preferred);const actual=targetQueue||events[0];if(!actual)return {status:'未观察到真实 ISL 入队事件',resource:preferred};const service=timeline.find(e=>e.pid===pid&&e.milestone==='service_start'&&e.link_id===actual.link_id&&Number(e.at)>=Number(actual.at));const propagation=(branchReplay.packet_events||[]).find(e=>e.pid===pid&&e.kind==='propagation_start'&&e.link_id===actual.link_id&&Number(e.at)>=Number(actual.at));return {status:targetQueue?'进入预测资源':'实际首先进入其他资源',resource:actual.link_id,queue_at:actual.at,service_at:service?.at??null,propagation_at:propagation?.at??null,predicted_resource:preferred}}
function timeMark(value,kind,label){const n=Number(value);if(value===null||value===undefined||!Number.isFinite(n))return '';const x=Math.max(0,Math.min(100,100*n/Math.max(duration,1)));return `<span class="tm ${kind}" style="left:${x}%" title="${esc(label)}：${fmt(n,6)} s"></span>`}
function timeBar(events){const marks=events.map(e=>timeMark(e[1],e[2],e[0])).join('');return `<div class="timebar" data-end="${fmt(duration,0)}s">${marks}</div><div class="time-legend"><b class="measure">● 收到测量</b><b class="decision">● 决策</b><b class="query">● 查询目标</b><b class="predicted">● 预计使用</b><b class="actual">● 实际服务</b><b class="propagation">● 实际传播</b></div>`}
function measurementReceivedAt(obs,detail,res){const peer=String(detail.peer),history=obs.neighbours?.[peer]?.advertised_history||[],dir=res.egress_direction;if(!dir)return null;const usable=history.filter(m=>Number(m.received_at)<=Number(obs.t_observed)&&(m.advertised_isl_queue_bits||{}).hasOwnProperty(dir));return usable.length?usable[usable.length-1].received_at:null}
function renderCandidates(){if(activePid!==targetPid){$('#candidateTable').innerHTML='<p class="small">未为所选包生成同快照分支；离线候选轨迹只对应上方结构采样包。</p>';return}const obs=target.observation_at_start||{},dirs=['N','E','S','W'],audit=target.four_direction_audit||obs.four_direction_audit||{},ta=obs.time_alignment||{},resources=obs.candidate_resources||{},scores=ta.scores||{},queries=ta.query_targets||{},branches=D.branch.forced_candidate_branches||{};const rows=dirs.map(d=>{const detail=audit.candidate_details?.[d]||{},res=resources[d]||{},branch=branches[d],use=branchUse(branch,d,res),legal=!!audit.final_legal_mask?.[d],outcome=branch?((D.branch.candidate_outcomes||{})[d]||{}):null,traj=(outcome?.resource_trajectory)||[],pressure=traj.filter(e=>e.milestone==='queue_enter'&&Number(e.backlog_before)>0).length,received=measurementReceivedAt(obs,detail,res),estimatedUse=res.estimated_use_at_s??res.estimated_use_s??ta.eta_targets?.[d]??null,events=[['信息收到',received,'measure'],['决策开始',target.t_decision_start,'decision'],['查询目标',queries[d],'query'],['预计资源使用',estimatedUse,'predicted'],['实际服务',use?.service_at,'actual'],['实际传播',use?.propagation_at,'propagation']];return `<tr><td><b>${d}</b><br>peer ${esc(detail.peer)}</td><td>路由 ${audit.route_candidate_mask?.[d]?'1':'0'} · 物理 ${audit.physical_legal_mask?.[d]?'1':'0'} · 最终 ${legal?'1':'0'}<br>${esc((audit.filter_reason_by_direction?.[d]||[]).join('; ')||'—')}</td><td>${fmt(queries[d],6)} s 查询目标<br>预计使用 ${fmt(estimatedUse,6)} s<br>${esc(res.egress_direction||'未知')} → ${esc(resourceId(res)||'未知')}</td><td>${fmt(scores[d]?.total_s,6)} s<br>${esc(JSON.stringify(scores[d]?.terms||{}))}<br>${scores[d]?.fallback?'回退':''} ${esc((scores[d]?.missing||[]).join(','))}</td><td>${timeBar(events)}<div class="small">${branch?esc(use.status):'未强制：该方向未通过实际合法掩码'}；资源 ${esc(use?.resource||'未知')}；入队 ${fmt(use?.queue_at,6)} s；正队列竞争事件 ${pressure}</div></td><td>${outcome?esc(JSON.stringify(outcome.outcome||{})):'未执行分支'}</td></tr>`}).join('');$('#candidateTable').innerHTML=`<table><thead><tr><th>N/E/S/W 邻居</th><th>路由/物理/最终合法掩码与理由</th><th>查询/预计资源使用时刻</th><th>评分</th><th>收到→决策→预计→实际的同一时间条（0–${fmt(duration,0)}s）</th><th>真实分支结果</th></tr></thead><tbody>${rows}</tbody></table>`}
function selectedDecisionRow(){if(selectedDecision){const arm=D.arms.find(a=>a.arm===selectedDecision.arm);const row=(arm?.routing_audit_log?.decision_records||[]).find(r=>String(r.decision_id)===selectedDecision.id);if(row&&Number(row.pid)===activePid)return {arm,row}}for(const arm of D.arms){const row=(arm.routing_audit_log.decision_records||[]).find(r=>Number(r.pid)===activePid);if(row)return {arm,row}}return null}
function renderMeasurements(){const selected=selectedDecisionRow(),obs=selected?.row?.observation_at_start||{},neighbours=obs.neighbours||{},rows=[];for(const [peer,n]of Object.entries(neighbours)){for(const m of n.advertised_history||[]){rows.push(`<tr><td>${esc(selected.arm.arm)}</td><td>${selected.row.decision_id}</td><td>${peer}</td><td>${fmt(m.generated_at,6)}</td><td>${fmt(m.received_at,6)}</td><td>${fmt(Number(obs.t_observed)-Number(m.received_at),6)}</td><td>${esc(JSON.stringify(m.advertised_isl_queue_bits||{}))}</td></tr>`)}}$('#measurements').innerHTML=`<table><thead><tr><th>臂</th><th>decision_id</th><th>广播源卫星</th><th>生成 s</th><th>收到 s</th><th>决策时信息年龄 s</th><th>公告队列</th></tr></thead><tbody>${rows.join('')||'<tr><td colspan="7">当前包未找到可审计决策或该决策未记录实际收到历史。</td></tr>'}</tbody></table>`}
function renderChain(){const rows=[];const selected=selectedDecisionRow();if(selected){const obs=selected.row.observation_at_start||{};for(const[peer,n]of Object.entries(obs.neighbours||{})){for(const m of n.advertised_history||[])rows.push({at:Number(m.received_at),text:`${names[selected.arm.arm]} · peer ${peer} 测量 ${fmt(m.generated_at,6)} s 收到 ${fmt(m.received_at,6)} s`})}}for(const arm of D.arms){const replay=arm.replay||{},events=(replay.packet_events||[]).filter(e=>Number(e.pid)===activePid&&['satellite_ingress','queue_enter','service_start','propagation_start','propagation_arrival','delivered'].includes(e.kind));for(const e of events)if(Number.isFinite(Number(e.at)))rows.push({at:Number(e.at),text:`${names[arm.arm]} · ${e.kind} · ${e.link_id||e.satellite||''} · ${JSON.stringify(e)}`});for(const d of (arm.routing_audit_log.decision_records||[]).filter(r=>Number(r.pid)===activePid))rows.push({at:Number(d.t_decision_start),text:`${names[arm.arm]} · 决策 ${d.decision_id} 在卫星 ${d.sat}，开始 ${fmt(d.t_decision_start,6)} s，提交 ${fmt(d.t_decision_commit,6)} s，动作 ${d.chosen}`});const fate=(replay.fates||{})[String(activePid)],delivery=(replay.deliveries||{})[String(activePid)];if(fate&&Number.isFinite(Number(replay.result?.stop_time_s)))rows.push({at:Number(replay.result.stop_time_s),text:`${names[arm.arm]} · 终态 ${fate}（仿真停止 ${fmt(replay.result.stop_time_s,6)} s）`});if(delivery&&Number.isFinite(Number(delivery.delivered_at)))rows.push({at:Number(delivery.delivered_at),text:`${names[arm.arm]} · 交付 ${fmt(delivery.delivered_at,6)} s · 路径 ${JSON.stringify(delivery.path||[])}`})}if(activePid===targetPid){for(const d of ['N','E','S','W']){const b=D.branch.forced_candidate_branches?.[d],use=branchUse(b,d,(target.observation_at_start?.candidate_resources||{})[d]||{});if(use?.queue_at!=null)rows.push({at:Number(use.queue_at),text:`离线分支 ${d} · 包进入 ${use.resource} 队列；预测资源 ${use.predicted_resource||'未知'}`});if(use?.service_at!=null)rows.push({at:Number(use.service_at),text:`离线分支 ${d} · 在 ${use.resource} 开始服务`});if(use?.propagation_at!=null)rows.push({at:Number(use.propagation_at),text:`离线分支 ${d} · ${use.resource} 的实际传播起点`})}}rows.sort((a,b)=>a.at-b.at);$('#chain').textContent=rows.length?rows.map(r=>`${fmt(r.at,6)} s  ${r.text}`).join('\n'):'缺少可复算的时序事件；没有绘制推测路径。'}

function renderSummary(){const rows=D.arms.map(a=>{const o=a.outcome||{},cost=a.cost||{},log=a.routing_audit_log||{},front=cost.packet_decisions||{},back=cost.background_updates||{},query=cost.query_service||{},pre=cost.control_traffic?.precompute||{},install=cost.install||{};return `<tr><td>${esc(names[a.arm])}</td><td>${fmt(o.offered,0)}</td><td>${fmt(o.delivered,0)}</td><td>${fmt(o.delivered_bits,0)}</td><td>${fmt(o.goodput_bps_in_window,1)}</td><td>${fmt(o.deadline_primary_loss?.value,4)} (${esc(o.deadline_primary_loss?.status||'未知')})</td><td>${fmt(front.service_s,4)}</td><td>${fmt(back.service_s,4)}</td><td>${fmt(query.service_s,4)}</td><td>${fmt(pre.builds,0)} / ${fmt(pre.build_wall_s,4)}</td><td>${fmt(install.installs,0)}</td><td>${fmt(log.decision_record_count,0)}</td></tr>`}).join('');$('#summaryTable').innerHTML=`<table><thead><tr><th>信息臂</th><th>发包</th><th>交付</th><th>交付 bits</th><th>Goodput bps</th><th>D=30 loss</th><th>前台服务 s</th><th>后台服务 s</th><th>查询服务 s</th><th>预计算建表/秒</th><th>安装数</th><th>方向决策记录</th></tr></thead><tbody>${rows}</tbody></table>`}
setupPanels();const packetRows=D.workload.packet_manifest||[];$('#packetSelect').innerHTML=packetRows.map(p=>`<option value="${Number(p.pid)}">PID ${Number(p.pid)} · ${esc(p.od_id||'OD未知')} · ${fmt(p.emit_time_s,3)} s</option>`).join('');$('#packetSelect').value=String(targetPid);$('#packetSelect').addEventListener('change',e=>{activePid=Number(e.target.value);selectedDecision=null;renderArmDecisions();renderCandidates();renderMeasurements();renderChain()});$('#armDecisions').addEventListener('click',e=>{const row=e.target.closest('tr[data-decision]');if(!row)return;selectedDecision={arm:row.dataset.arm,id:row.dataset.decision};renderArmDecisions();renderMeasurements();renderChain()});renderArmDecisions();renderCandidates();renderMeasurements();renderChain();renderSummary();$('#clock').addEventListener('input',e=>update(Number(e.target.value)));let timer=null;$('#play').onclick=()=>{if(timer)return;timer=setInterval(()=>{let t=Number($('#clock').value)+.1;if(t>duration)t=0;update(t)},100)};$('#pause').onclick=()=>{clearInterval(timer);timer=null};update(0);
</script></html>'''


def write_html(network_path, branch_path, out_path, *, run_identity=None):
    network_payload = _read_json(network_path)
    branch_payload = _read_json(branch_path)
    document = build_document(network_payload, branch_payload,
                              run_identity=run_identity)
    content = HTML.replace("__DATA__", _embedded_json(document))
    out = Path(out_path)
    if out.exists() or out.is_symlink():
        raise ReplayError(f"refusing to overwrite replay HTML: {out}")
    if not out.parent.is_dir() or out.parent.is_symlink():
        raise ReplayError(f"replay output parent must exist: {out.parent}")
    out.write_text(content, encoding="utf-8")
    return {"path": str(out), "sha256": hashlib.sha256(
        out.read_bytes()).hexdigest(), "bytes": out.stat().st_size,
        "scenario": document["scenario"], "seed": document["seed"],
        "arm_count": len(document["arms"]),
        "target_packet_id": document["branch"].get("target_packet_id"),
        "selection": document["branch"].get("selection")}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network-result", type=Path, required=True)
    parser.add_argument("--branch-result", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--run-label", default="")
    args = parser.parse_args(argv)
    try:
        result = write_html(args.network_result, args.branch_result, args.out,
                            run_identity={"run_label": args.run_label,
                                          "network_result_sha256": hashlib.sha256(
                                              args.network_result.read_bytes()).hexdigest(),
                                          "branch_result_sha256": hashlib.sha256(
                                              args.branch_result.read_bytes()).hexdigest()})
    except (OSError, ReplayError) as exc:
        print(f"T1 REPLAY REFUSED: {exc}")
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
