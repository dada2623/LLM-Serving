"""Offline supplementary tables for native cache gauges and container RAM.

Run after report_baseline.py; no Docker calls or requests, no measurement changes.
"""
import argparse
import json
import statistics
import shutil
import sys
from pathlib import Path

import report_baseline as report
import run_p2 as p2

p = p2.p
MARKER = '\n## 原生缓存池与容器内存补充\n'


def scrapes(path):
    stamp, body = None, []
    for line in path.read_text(encoding='utf-8').splitlines():
        if line.startswith('# P0_SCRAPE '):
            if stamp is not None:
                yield stamp, p2.prometheus_values('\n'.join(body))
            stamp = json.loads(line[len('# P0_SCRAPE '):]); body = []
        else:
            body.append(line)
    if stamp is not None:
        yield stamp, p2.prometheus_values('\n'.join(body))


def summarize(root):
    results = report.load(root/'summary.json')
    rows, sources, resources = [], [], []
    for r in results:
        folder = root/'sessions'/r['session']
        snapshots = list(scrapes(folder/'metrics.prom'))
        cgroups = [json.loads(line) for line in (folder/'wsl-container.jsonl').read_text(encoding='utf-8').splitlines()]
        for window in r['windows']:
            def inside(stamp):
                return stamp['phase']==window['phase'] and window['start_ns']<=int(stamp['monotonic_ns'])<=window['end_ns']
            values = [metrics for stamp,metrics in snapshots if inside(stamp)]
            groups = [x['cgroup'] for x in cgroups if inside(x)]
            row = {'service':r['service'],'repeat':r['repeat'],'phase':window['phase'],
                   'metric_scrapes':len(values),'cgroup_samples':len(groups)}
            for output, names in (
                ('kv_usage_native',('vllm:kv_cache_usage_perc','sglang:full_token_usage')),
                ('mamba_usage_native',('sglang:mamba_usage',)),
                ('mamba_slots_used_native',('sglang:mamba_used_tokens',)),
                ('running_requests',('vllm:num_requests_running','sglang:num_running_reqs'))):
                observed = [m[key] for m in values for key in names if key in m]
                row[output+'_median'] = statistics.median(observed) if observed else None
                row[output+'_max'] = max(observed) if observed else None
            for name, field in (('container_ram_gib','memory.current'),('container_swap_gib','memory.swap.current')):
                observed = [int(m[field].strip())/2**30 for m in groups if m.get(field)]
                row[name+'_median'] = statistics.median(observed) if observed else None
                row[name+'_max'] = max(observed) if observed else None
            rows.append(row)
        ledger = report.load(folder/'startup-storage-ledger.json')
        weights = [x for x in ledger if x['kind']=='weights']
        pools = [x for x in ledger if x['kind']==('runner_caches' if r['service']=='vllm' else 'pool')]
        def total(item, kind, device):
            return sum(v for k,v in item[kind]['unique_storage_bytes_by_device'].items() if k.startswith(device))
        resources.append({'service':r['service'],'repeat':r['repeat'],
             'cuda_parameter_bytes':max(total(x,'parameters','cuda') for x in weights),
             'cuda_buffer_bytes':max(total(x,'buffers','cuda') for x in weights),
             'cpu_parameter_bytes':max(total(x,'parameters','cpu') for x in weights),
             'cache_storage_bytes':r['actual_cache_storage_bytes'],
             'load_torch_allocated_bytes':weights[-1].get('torch_allocated_bytes'),
             'load_torch_reserved_bytes':weights[-1].get('torch_reserved_bytes'),
             'pool_torch_allocated_bytes':pools[-1].get('torch_allocated_bytes') if pools else None,
             'pool_torch_reserved_bytes':pools[-1].get('torch_reserved_bytes') if pools else None})
        for name in ('metrics.prom','wsl-container.jsonl','startup-storage-ledger.json','baseline-summary.json','gpu.csv','host.csv'):
            sources.append({'path':str((folder/name).relative_to(root)),'sha256':p2.sha(folder/name)})
    p2.write_csv(root/'native-stage-resources.csv',rows)
    p2.write_csv(root/'actual-storage.csv',resources)
    analysis_sources=root/'postprocessing-sources'; analysis_sources.mkdir(exist_ok=True)
    shutil.copy2(Path(__file__),analysis_sources/Path(__file__).name)
    p.save(root/'details.sources.json',{'script_path':str((analysis_sources/Path(__file__).name).relative_to(root)),
           'script_sha256':p2.sha(Path(__file__)),'inputs':sources})
    sys.path.insert(0,str(p.ROOT/'.tools/benchmark-plot'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes = plt.subplots(len(results),2,figsize=(14,2.7*len(results)),squeeze=False)
    for i,r in enumerate(results):
        folder=root/'sessions'/r['session']
        gpu=report.read_csv(folder/'gpu.csv'); host=report.read_csv(folder/'host.csv')
        origin=min(int(x['monotonic_ns']) for x in gpu)
        for ax,raw,key,label in ((axes[i,0],gpu,'gpu_util_percent','GPU utilization (%)'),
                                 (axes[i,1],host,'windows_cpu_percent','Windows CPU (%)')):
            valid=[]
            for x in raw:
                try: valid.append(((int(x['monotonic_ns'])-origin)/1e9,float(x[key])))
                except (TypeError,ValueError): pass
            ax.plot([x[0] for x in valid],[x[1] for x in valid],linewidth=.8)
            for w,color in zip(r['windows'],('#d5e7f5','#d9efcf','#ffe2b7','#e4d6f0')):
                ax.axvspan((w['start_ns']-origin)/1e9,(w['end_ns']-origin)/1e9,color=color,alpha=.55)
            ax.set_title(f"{r['service']} repeat {r['repeat']}")
            ax.set_ylabel(label); ax.set_xlabel('Seconds since first sample'); ax.grid(alpha=.2)
    fig.tight_layout(); fig.savefig(root/'utilization.png',dpi=130); plt.close(fig)
    write_details(root,results,resources,rows)


def write_details(root,results,resources,rows):
    lines=[MARKER.strip(),'','以下表格来自同批原始 Prometheus、cgroup 与启动张量账本，离线汇总。', '',
           '无服务窗口保留 Docker Desktop、WSL 与桌面程序。释放差值以各轮自身基线为参照。', '',
           '各轮释放差值：','']
    for service in ('vllm','sglang'):
        selected=[x for x in results if x['service']==service]
        if not selected: continue
        drops=[]
        for r in selected:
            stages={s['phase']:s for s in r['stages']}
            drops.append(stages['warm_idle_60s']['gpu_mib']['median']-stages['post_stop_60s']['gpu_mib']['median'])
        residuals=[r['post_stop_minus_no_service_gpu_mib'] for r in selected]
        ram=[r['post_stop_minus_no_service_host_ram_gib'] for r in selected]
        lines.append(f"- {service}：停服后相对预热空闲的整卡显存下降中位数 {statistics.median(drops):.0f} MiB（{min(drops):.0f}–{max(drops):.0f}）；相对本轮无服务基线残差 {min(residuals):+.0f} 至 {max(residuals):+.0f} MiB；主机 RAM 残差 {min(ram):+.3f} 至 {max(ram):+.3f} GiB。")
    lines += ['', '六轮均确认容器停止。下表按 storage 指针去重记录参数、缓冲区和缓存池。', '',
           '| 框架 | GPU 参数字节（3 轮范围） | GPU 缓冲区字节 | CPU 参数字节 | 缓存池字节 |',
           '| --- | ---: | ---: | ---: | ---: |']
    def bounds(items,key):
        v=[x[key] for x in items if x[key] is not None]
        if not v: return 'unavailable'
        return str(v[0]) if min(v)==max(v) else f'{min(v)}–{max(v)}'
    for service in ('vllm','sglang'):
        selected=[x for x in resources if x['service']==service]
        lines.append('| '+' | '.join([service]+[bounds(selected,k) for k in ('cuda_parameter_bytes','cuda_buffer_bytes','cpu_parameter_bytes','cache_storage_bytes')])+' |')
    lines += ['', '| 框架 | 权重加载后 allocated 字节 | 权重加载后 reserved 字节 | 池初始化后 allocated 字节 | 池初始化后 reserved 字节 |',
              '| --- | ---: | ---: | ---: | ---: |']
    for service in ('vllm','sglang'):
        selected=[x for x in resources if x['service']==service]
        lines.append('| '+' | '.join([service]+[bounds(selected,k) for k in ('load_torch_allocated_bytes','load_torch_reserved_bytes','pool_torch_allocated_bytes','pool_torch_reserved_bytes')])+' |')
    lines += ['', 'allocated/reserved 来自权重加载后、池初始化后两个时点。它们不包含非 torch 分配、驱动与桌面占用，全程峰值未测。']
    lines += ['', '| 框架 / 轮次 | 阶段 | 容器 RAM 中位 GiB | 容器 swap 中位 GiB | KV 原生占用中位 / 最大 | SSM 原生占用中位 / 最大 |',
              '| --- | --- | ---: | ---: | ---: | ---: |']
    def number(v): return 'unavailable' if v is None else f'{v:.6f}'
    for x in rows:
        if x['phase'] not in ('warm_idle_60s','post_request_60s'): continue
        lines.append('| '+' | '.join([f"{x['service']} / {x['repeat']}",x['phase'],number(x['container_ram_gib_median']),
             number(x['container_swap_gib_median']),
             number(x['kv_usage_native_median'])+' / '+number(x['kv_usage_native_max']),
             number(x['mamba_usage_native_median'])+' / '+number(x['mamba_usage_native_max'])])+' |')
    lines += ['', 'KV/SSM 列保留框架原生 gauge，含义不同于 allocator reserved。SGLang 三轮预热后约 19 秒内仍报 KV=0.400049、SSM=0.058824，随后归零，业务计数没有增加。指标更新滞后是一种解释，尚未验证；表内保留最大值。逻辑占用为零时，约 1.5 GiB 的预分配池仍在 GPU。停服后的 cgroup 记为 unavailable。', '',
              '[缓存与容器资源 CSV](native-stage-resources.csv) · [实际存储 CSV](actual-storage.csv) · [输入哈希](details.sources.json)', '',
              '![GPU 与 CPU 利用率](utilization.png)']
    path=root/'REPORT.md'
    text=path.read_text(encoding='utf-8').split(MARKER)[0]
    path.write_text(text.rstrip()+'\n\n'+'\n'.join(lines)+'\n',encoding='utf-8')


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('--root',required=True,type=Path)
    summarize(ap.parse_args().root)
