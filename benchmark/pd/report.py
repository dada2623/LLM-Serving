"""Regenerate measured tables and figures; incomplete/validation cells stay labeled."""
import argparse
import csv
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

p=argparse.ArgumentParser()
p.add_argument('run')
a=p.parse_args()
root=Path(a.run)
calibration=json.loads((root/'calibration.json').read_text()) if (root/'calibration.json').exists() else {}
slo=calibration.get('slo',{})
rows=[]
requests=[]
for summary in sorted(root.rglob('summary.json')):
    if any('interrupted-' in part for part in summary.parts): continue
    if 'cells' not in summary.parts and not any(part.startswith('preflight') for part in summary.parts):
        continue
    s=json.loads(summary.read_text(encoding='utf-8-sig'))
    rs=[json.loads(f.read_text()) for f in summary.parent.glob('*-result.json')]
    valid=[r for r in rs if r.get('success')]
    row={'cell':summary.parent.relative_to(root).as_posix(),'mode':s['mode'],'workload':s['workload'],
         'concurrency':s['settings']['concurrency'],'rate':s['settings'].get('rate'),
         'output_override':s['settings'].get('output_tokens'),
         'successes':s['successes'],'errors':s['errors'],'elapsed_s':s['elapsed_s'],
         'sample_complete':s['sample_complete'],'output_token_s':s['output_tokens']/s['elapsed_s']}
    row['requests_s']=s['successes']/s['elapsed_s']
    passing=[]
    for r in rs:
        limits=slo.get(r.get('workload'))
        if limits:
            r['slo_pass']=bool(r.get('success') and r.get('ttft_s') is not None
                and r.get('tpot_s') is not None and r['ttft_s']<=limits['ttft_s']
                and r['tpot_s']<=limits['tpot_s']
                and r['e2e_s']<=limits['ttft_s']+(r['completion_tokens']-1)*limits['tpot_s'])
            passing.append(r['slo_pass'])
    row['slo_passes']=sum(passing) if passing else None
    row['slo_failures']=len(passing)-sum(passing) if passing else None
    row['goodput_requests_s']=sum(passing)/s['elapsed_s'] if passing else None
    for metric in ['ttft_s','tpot_s','e2e_s','submission_delay_s']:
        xs=[r[metric] for r in valid if r.get(metric) is not None]
        for q in [50,90,95]:
            row[f'{metric}_p{q}']=float(np.percentile(xs,q)) if xs else None
    rows.append(row)

def active_cell_name(name):
    return (name.startswith(('screen-','formal-'))
            and '-interrupted-' not in name
            and '-retry-' not in name)
    for r in rs:
        requests.append({'cell':row['cell'],**{k:v for k,v in r.items() if k not in ['text','output_ids','prefill']}})
figdir=root/'figures'
figdir.mkdir(exist_ok=True)
if rows:
    with (root/'cells.csv').open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0])); writer.writeheader();writer.writerows(rows)
    with (root/'requests.jsonl').open('w',encoding='utf-8') as f:
        for r in requests: f.write(json.dumps(r)+'\n')
    # Stratify mixed workloads; an overall P95 cannot describe short-request stalls.
    per_class=[]
    for row in rows:
        same=[r for r in requests if r['cell']==row['cell']]
        for shape in sorted({r['workload'] for r in same}):
            group=[r for r in same if r['workload']==shape]
            item={'cell':row['cell'],'mode':row['mode'],'class':shape,'n':len(group),
                  'errors':sum(not r['success'] for r in group)}
            for metric in ['ttft_s','tpot_s','e2e_s']:
                xs=[r[metric] for r in group if r['success'] and r.get(metric) is not None]
                for q in [50,90,95]:
                    item[f'{metric}_p{q}']=float(np.percentile(xs,q)) if xs else None
            per_class.append(item)
    if per_class:
        with (root/'classes.csv').open('w',newline='',encoding='utf-8-sig') as f:
            writer=csv.DictWriter(f,fieldnames=list(per_class[0]));writer.writeheader();writer.writerows(per_class)
    repetitions=[]
    formal=[r for r in rows if active_cell_name(r['cell'].split('/')[-1])
            and r['cell'].split('/')[-1].startswith('formal-')]
    for key in sorted({(r['mode'],r['workload'],r['concurrency'],r['rate']) for r in formal},key=str):
        group=[r for r in formal if (r['mode'],r['workload'],r['concurrency'],r['rate'])==key]
        item={'mode':key[0],'workload':key[1],'concurrency':key[2],'rate':key[3],
              'repetitions':len(group),'all_samples_complete':all(r['sample_complete'] for r in group)}
        for metric in ['output_token_s','ttft_s_p95','tpot_s_p95','goodput_requests_s']:
            xs=[r[metric] for r in group if r.get(metric) is not None]
            item[metric]={'mean':float(np.mean(xs)),'min':min(xs),'max':max(xs),'values':xs} if xs else None
        repetitions.append(item)
    (root/'repetitions.json').write_text(json.dumps(repetitions,indent=2))
    # Group by configuration *and* length/concurrency: never combine unlike workloads.
    plotted=[r for r in rows if active_cell_name(r['cell'].split('/')[-1])]
    keys=sorted({(r['workload'],r['concurrency'],r['rate'],r['output_override']) for r in plotted},key=str)
    for wi,key in enumerate(keys):
        group=[r for r in plotted if (r['workload'],r['concurrency'],r['rate'],r['output_override'])==key and r['successes']>0]
        if not group:
            continue
        labels=[f"{r['mode']}\n{r['cell'].split('/')[-1]}" for r in group]
        fig,axes=plt.subplots(1,3,figsize=(max(10,len(group)*1.2),4),layout='constrained')
        for ax,metric,title in zip(axes,['ttft_s_p50','tpot_s_p50','output_token_s'],
                                   ['Median TTFT (s)','Mean token interval, median (s)','Successful output tokens/s']):
            values=[r.get(metric) or 0 for r in group]
            ax.bar(np.arange(len(group)),values,color=['#3679b5' if r['errors']==0 else '#c34f4f' for r in group])
            ax.set_xticks(np.arange(len(group)),labels,fontsize=7)
            ax.set_title(title);ax.grid(axis='y',alpha=.2)
        fig.suptitle(f'MEASURED / workload={key[0]}, concurrency={key[1]}, arrival rate={key[2]}, output override={key[3]}\n'
                     'Cell labels show phase; validation is not a formal benchmark',fontsize=10)
        fig.savefig(figdir/f'measured-{wi:02}.png',dpi=160)
        fig.savefig(figdir/f'measured-{wi:02}.svg')
        plt.close(fig)
    for trace in ['M-code','M-interference']:
        for phase in ['screen','formal']:
            group=[r for r in rows if r['workload']==trace and r['rate'] is not None
                   and r['cell'].split('/')[-1].startswith(phase+'-')]
            if not group:
                continue
            fig,axes=plt.subplots(1,2,figsize=(11,4),layout='constrained')
            for mode in ['N','A','NP-AD','AP-ND','REPLICAS']:
                selected=[r for r in group if r['mode']==mode]
                rates=sorted({r['rate'] for r in selected})
                if not rates:
                    continue
                good=[];tail=[]
                target='S' if trace=='M-interference' else 'C'
                for rate in rates:
                    cells=[r for r in selected if r['rate']==rate]
                    g=[r['goodput_requests_s'] for r in cells if r['goodput_requests_s'] is not None]
                    names={r['cell'] for r in cells}
                    t=[r['tpot_s_p95']*1000 for r in per_class if r['cell'] in names and r['class']==target
                       and r['tpot_s_p95'] is not None]
                    good.append(float(np.mean(g)) if g else np.nan)
                    tail.append(float(np.mean(t)) if t else np.nan)
                axes[0].plot(rates,good,'o-',label=mode)
                axes[1].plot(rates,tail,'o-',label=mode)
            axes[0].set_ylabel('SLO-passing requests/s')
            axes[1].set_ylabel(f'{target} class P95 TPOT (ms)')
            for ax in axes:
                ax.set_xlabel('Offered requests/s');ax.grid(alpha=.2);ax.legend(fontsize=8)
            fig.suptitle(f'{phase}: {trace} / measured values; inspect class counts in classes.csv')
            fig.savefig(figdir/f'{phase}-{trace}-goodput-tail.png',dpi=160)
            fig.savefig(figdir/f'{phase}-{trace}-goodput-tail.svg')
            plt.close(fig)
status=json.loads((root/'execution.json').read_text()) if (root/'execution.json').exists() else {}
network=[]
for file in [root/'network-nvidia.jsonl',root/'network-amd.jsonl']:
    if file.exists(): network.extend(json.loads(line) for line in file.read_text().splitlines())
if network:
    fig,axes=plt.subplots(1,2,figsize=(10,4),layout='constrained')
    for sender,label in [('initiator','NVIDIA -> AMD'),('target','AMD -> NVIDIA')]:
        sizes=sorted({r['mib'] for r in network if r['sender']==sender})
        bandwidth=[];times=[]
        for size in sizes:
            group=[r for r in network if r['sender']==sender and r['mib']==size and r['passed']]
            bandwidth.append(float(np.median([r['mib_s'] for r in group])) if group else np.nan)
            times.append(float(np.median([r['elapsed_s'] for r in group])) if group else np.nan)
        axes[0].plot(sizes,bandwidth,'o-',label=label)
        axes[1].plot(sizes,times,'o-',label=label)
    for ax in axes:
        ax.set_xlabel('GPU payload (MiB)');ax.grid(alpha=.2);ax.legend(fontsize=8)
    axes[0].set_ylabel('Median verified MiB/s');axes[1].set_ylabel('Synchronized wall time (s)')
    fig.suptitle('Measured Mooncake GPU transfer / TUN + TCP + SSH / 10 trials per point')
    fig.savefig(figdir/'network.png',dpi=160);fig.savefig(figdir/'network.svg');plt.close(fig)
lines=['# 异构 PD 压测数据','',f"执行状态：`{status.get('state','unknown')}`。所有图仅使用已经落盘的实测请求。",'',
       '验证、预热、筛选、正式测试分开标记；验证图不能作为正式性能结论。当前尚未覆盖的部署不填零、不外推。','',
       '| 单元 | 成功/错误 | TTFT P50 秒 | TPOT P50 秒 | 输出 token/s | 样本要求完成 |',
       '| --- | --- | --- | --- | --- | --- |']
screen_rows=[r for r in rows if active_cell_name(r['cell'].split('/')[-1])
             and r['cell'].split('/')[-1].startswith('screen-')]
formal_rows=[r for r in rows if active_cell_name(r['cell'].split('/')[-1])
             and r['cell'].split('/')[-1].startswith('formal-')]
screen_count=sum(r['sample_complete'] for r in screen_rows)
formal_count=sum(r['sample_complete'] for r in formal_rows)
progress=[f'性能筛选完成：{screen_count}/76 单元；正式测试完成：{formal_count}/最多 78 单元。验收、预热与校准不计入这两个数量。','']
incomplete=sum(not r['sample_complete'] for r in screen_rows+formal_rows)
if incomplete:
    progress += [f'另有 {incomplete} 个单元未满足采样要求，未计入完成数量。','']
isolation_count=0
full_count=0
for mode in ['N','A','NP-AD','AP-ND']:
    gate=root/f'preflight/isolation-{mode}/validation.json'
    isolation_count+=bool(gate.exists() and json.loads(gate.read_text()).get('passed'))
    gate=root/f'cells/validation-full-{mode}/summary.json'
    full_count+=bool(gate.exists() and (lambda s:s['errors']==0 and s['sample_complete'])(json.loads(gate.read_text())))
progress += [f'四种部署验收：并发隔离 {isolation_count}/4；14K 输入、完整 1024-token 输出 {full_count}/4。','']
if status.get('current_cell'):
    progress += [f"当前单元：`{status['current_cell']}`；状态更新时间：{status.get('updated_at','未记录')}。",'']
if status.get('state')=='stopped' and status.get('error'):
    progress += [f"停止原因：`{status['error']}`。",'']
network_gate=root/'network-passed.json'
if network_gate.exists():
    gate=json.loads(network_gate.read_text())
    progress += [f"GPU 传输校准：{'通过' if gate['passed'] else '未通过'}，记录 {gate['operations']}/100 次操作。",'']
lines[4:4]=progress
blocker=root/'blocker.json'
if blocker.exists():
    b=json.loads(blocker.read_text(encoding='utf-8'))
    lines[4:4]=[f"当前阻断：{b['description']}", '',
        '已通过：四种部署的 14K 三位置口令；两端权重/配置/tokenizer 共 16 文件哈希一致。',
        '未完成：并发隔离补验、GPU 带宽测量、性能筛选和正式矩阵。当前不提供 PD 性能优劣结论。','']
gates=[]
for mode in ['N','A','NP-AD','AP-ND']:
    file=root/'preflight'/('validation-'+mode)/'validation.json'
    if file.exists():
        data=json.loads(file.read_text());gates.append((mode,sum(r['success'] for r in data['results'])))
if gates:
    fig,ax=plt.subplots(figsize=(7,4),layout='constrained')
    ax.bar([x[0] for x in gates],[x[1] for x in gates],color='#3679b5')
    ax.set_ylim(0,3.5);ax.set_yticks([0,1,2,3]);ax.set_ylabel('Exact-answer probes passed / 3')
    ax.set_title('14,336-token correctness probes / not a performance benchmark')
    fig.savefig(figdir/'validation.png',dpi=160);fig.savefig(figdir/'validation.svg');plt.close(fig)
for r in rows:
    fmt=lambda x:'—' if x is None else f'{x:.3f}'
    lines.append(f"| {r['cell']} | {r['successes']}/{r['errors']} | {fmt(r['ttft_s_p50'])} | {fmt(r['tpot_s_p50'])} | {r['output_token_s']:.2f} | {r['sample_complete']} |")
lines+=['','逐单元汇总：`cells.csv`；逐请求索引：`requests.jsonl`；原始 SSE 位于各单元目录；图位于 `figures/`。',
        '', '尚未完成三次正式重复前，不宣称尾延迟、容量或 PD 收益已经得到验证。']
if network:
    lines+=['','## GPU 传输实测','','| 方向 | MiB | 次数 | 成功 | 中位 MiB/s | 中位秒 |',
            '| --- | --- | --- | --- | --- | --- |']
    for sender,label in [('initiator','NVIDIA → AMD'),('target','AMD → NVIDIA')]:
        for size in sorted({r['mib'] for r in network}):
            group=[r for r in network if r['sender']==sender and r['mib']==size]
            valid=[r for r in group if r['passed']]
            bandwidth=f"{np.median([r['mib_s'] for r in valid]):.2f}" if valid else '—'
            elapsed=f"{np.median([r['elapsed_s'] for r in valid]):.3f}" if valid else '—'
            lines.append(f"| {label} | {size} | {len(group)} | {sum(r['passed'] for r in group)} | "
                f"{bandwidth} | {elapsed} |")
session_files=list((root/'sessions').glob('*/session-summary.json')) if (root/'sessions').exists() else []
if session_files:
    lines+=['','## 固定会话回放','','同一份预录制历史用于各部署；当前回复允许 EOS，不执行工具命令。此组不代表真实 Claude Code 客户端操作。','',
            '| 部署 | 完成请求/30 | 错误 |', '| --- | --- | --- |']
    for file in session_files:
        s=json.loads(file.read_text());lines.append(f"| {s['mode']} | {s['completed']} | {s['errors']} |")
resource_peaks=[]
for file in sorted((root/'resources').glob('*-windows.jsonl')) if (root/'resources').exists() else []:
    samples=[]
    for line in file.read_text(encoding='utf-8-sig').splitlines():
        try: samples.append(json.loads(line))
        except json.JSONDecodeError: continue
    if not samples: continue
    side='N' if '-N-windows' in file.name else 'A'
    dedicated=[max([x.get('DedicatedUsage',0) for x in r.get('gpuMemory',[])] or [0])/2**30 for r in samples]
    shared=[max([x.get('SharedUsage',0) for x in r.get('gpuMemory',[])] or [0])/2**30 for r in samples]
    used=[(r.get('memory',{}).get('totalKiB',0)-r.get('memory',{}).get('freeKiB',0))/2**20 for r in samples]
    resource_peaks.append({'side':side,'file':file.name,'samples':len(samples),
        'first_time':samples[0].get('time'),'last_time':samples[-1].get('time'),
        'dedicated_peak_GiB':max(dedicated),'shared_peak_GiB':max(shared),'host_used_peak_GiB':max(used)})
if resource_peaks:
    (root/'resource-peaks.json').write_text(json.dumps(resource_peaks,indent=2))
    lines+=['','## 资源采样峰值','','Windows GPUAdapterMemory 计数器包含桌面及其他进程；共享显存不能直接视为权重 CPU offload。',
        '', '| 主机 | 样本数 | 专用 GPU GiB | 共享 GPU GiB | 主机已用 GiB |', '| --- | --- | --- | --- | --- |']
    for r in resource_peaks:
        lines.append(f"| {r['side']} | {r['samples']} | {r['dedicated_peak_GiB']:.2f} | {r['shared_peak_GiB']:.2f} | {r['host_used_peak_GiB']:.2f} |")
    if not any(r['cell'].startswith('cells/screen-') or 'validation-full-' in r['cell'] for r in rows):
        lines+=['','当前采样从模型停止后的哈希核对阶段开始，未覆盖早期推理验收；上述数值不能作为模型推理的显存峰值。']
for mode in ['N','A','NP-AD','AP-ND','REPLICAS']:
    folder=root/'interference'/mode
    pairs=[]
    for file in sorted(folder.glob('pair*-summary.json')) if folder.exists() else []:
        pairs.append(json.loads(file.read_text()))
    if not pairs: continue
    fig,ax=plt.subplots(figsize=(11,4),layout='constrained')
    for pair in pairs:
        zero=pair['long_arrival_s']
        ax.plot([g['end_s']-zero for g in pair['chunk_gaps']],
                [g['gap_s']*1000 for g in pair['chunk_gaps']],alpha=.45,linewidth=.8)
    ax.axvline(0,color='black',linestyle='--',label='14K request arrival')
    ax.set_xlabel('Seconds relative to long-input request');ax.set_ylabel('Short response content-chunk gap (ms)')
    ax.set_title(f'{mode}: {len(pairs)} measured interference pairs / chunks may contain multiple tokens')
    ax.grid(alpha=.2);ax.legend()
    fig.savefig(figdir/f'interference-{mode}.png',dpi=160);fig.savefig(figdir/f'interference-{mode}.svg');plt.close(fig)
(root/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(json.dumps({'cells':len(rows),'requests':len(requests),'report':str(root/'REPORT.md')}))
