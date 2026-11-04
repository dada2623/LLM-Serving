"""Offline validation and resource report for the independent P3 baseline batch."""
import csv
import json
import statistics
import sys
from pathlib import Path

import run_p2 as p2

p = p2.p
PHASES = ('no_service_60s','warm_idle_60s','post_request_60s','post_stop_60s')


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def distribution(rows, key, divisor=1):
    values = []
    for row in rows:
        try:
            values.append(float(row[key])/divisor)
        except (ValueError, TypeError, KeyError):
            pass
    return {'n':len(values), 'median':statistics.median(values) if values else None,
            'min':min(values) if values else None, 'max':max(values) if values else None}


def coverage(rows, window):
    ns = sorted(int(r['monotonic_ns']) for r in rows)
    gaps = [(b-a)/1e9 for a,b in zip(ns,ns[1:])]
    span = (ns[-1]-ns[0])/1e9 if ns else 0
    return {'samples':len(ns), 'span_seconds':span,
            'max_gap_seconds':max(gaps,default=None),
            'start_lag_seconds':(ns[0]-window['start_ns'])/1e9 if ns else None,
            'end_lag_seconds':(window['end_ns']-ns[-1])/1e9 if ns else None,
            'pass':len(ns)>=50 and span>=55 and max(gaps,default=999)<=5}


def analyze_session(folder, result):
    gpu = read_csv(folder / 'gpu.csv'); host = read_csv(folder / 'host.csv')
    stages = []
    for window in result['windows']:
        def select(rows):
            return [r for r in rows if r['phase']==window['phase'] and
                    window['start_ns']<=int(r['monotonic_ns'])<=window['end_ns']]
        g = select(gpu); h = select(host)
        stages.append({'phase':window['phase'], 'seconds':window['seconds'],
              'gpu_coverage':coverage(g,window), 'host_coverage':coverage(h,window),
              'gpu_mib':distribution(g,'memory_used_mib'),
              'gpu_util_percent':distribution(g,'gpu_util_percent'),
              'gpu_power_w':distribution(g,'power_w'),
              'host_ram_gib':distribution(h,'windows_ram_used_bytes',2**30),
              'host_cpu_percent':distribution(h,'windows_cpu_percent')})
    manifest = load(folder / 'manifest.json')
    stopped = load(folder / 'stop.json')
    warm = [load(x) for x in sorted((folder / 'warmup').glob('request-*.json'))]
    errors = load(folder / 'collector-errors.json')
    ledger = load(folder / 'startup-storage-ledger.json')
    weights = [x for x in ledger if x['kind']=='weights']
    cuda_params = [sum(v for k,v in x['parameters']['unique_storage_bytes_by_device'].items() if k.startswith('cuda')) for x in weights]
    checks = {'four_windows':len(stages)==4 and {s['phase'] for s in stages}==set(PHASES),
              'durations':all(s['seconds']>=60 for s in stages),
              'gpu_sampling':all(s['gpu_coverage']['pass'] and s['gpu_mib']['n']>=50 for s in stages),
              'host_sampling':all(s['host_coverage']['pass'] for s in stages),
              'request':result['request']['status']=='success' and result['request']['length_match'],
              'four_excluded_warmups':len(warm)==4 and all(x['status']=='success' for x in warm),
              'native_counts':result['native']['matches'],
              'idle_no_extra_requests':result['native']['warm_idle_counters_unchanged'] and result['native']['post_request_counters_unchanged'],
              'weights_match_control':bool(cuda_params) and all(n==10977765888 for n in cuda_params),
              'resource_gate':manifest.get('cpu_residency_gate','').startswith('passed') and manifest.get('resource_gate','').startswith('passed'),
              'production_files':manifest['env_unchanged'] and manifest['compose_unchanged'],
              'stopped':not any(x['Running'] for x in stopped['containers'].values()),
              'collector_no_errors':not errors}
    pre = next(s for s in stages if s['phase']==PHASES[0])
    post = next(s for s in stages if s['phase']==PHASES[3])
    return {'stages':stages, 'checks':checks, 'checks_pass':all(checks.values()),
            'first_warmup_e2e_ms':warm[0]['e2e_ms'] if warm else None,
            'actual_cache_storage_bytes':manifest.get('actual_cache_storage_bytes'),
            'cuda_parameter_bytes':cuda_params[0] if cuda_params else None,
            'whole_gpu_peak_mib':distribution(gpu,'memory_used_mib')['max'],
            'post_stop_minus_no_service_gpu_mib':post['gpu_mib']['median']-pre['gpu_mib']['median'],
            'post_stop_minus_no_service_host_ram_gib':post['host_ram_gib']['median']-pre['host_ram_gib']['median'],
            'sampling_errors':errors}


def plot(root, results):
    sys.path.insert(0, str(p.ROOT / '.tools/benchmark-plot'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(len(results), 3, figsize=(16,3*len(results)), squeeze=False)
    colors = dict(zip(PHASES, ('#d5e7f5','#d9efcf','#ffe2b7','#e4d6f0')))
    sources = []
    for idx, r in enumerate(results):
        folder = root / 'sessions' / r['session']
        gpu = read_csv(folder/'gpu.csv'); host = read_csv(folder/'host.csv')
        origin = min(int(x['monotonic_ns']) for x in gpu)
        def series(rows,key,divisor=1):
            x,y = [],[]
            for row in rows:
                try:
                    value = float(row[key])/divisor
                except (KeyError,ValueError,TypeError):
                    continue
                x.append((int(row['monotonic_ns'])-origin)/1e9); y.append(value)
            return x,y
        for ax, rows, key, unit, divisor in (
            (axes[idx,0],gpu,'memory_used_mib','Whole GPU memory (MiB)',1),
            (axes[idx,1],host,'windows_ram_used_bytes','Windows used RAM (GiB)',2**30),
            (axes[idx,2],gpu,'power_w','GPU power (W)',1)):
            x,y = series(rows,key,divisor); ax.plot(x,y,linewidth=.8)
            for w in r['windows']:
                ax.axvspan((w['start_ns']-origin)/1e9,(w['end_ns']-origin)/1e9,
                           color=colors[w['phase']],alpha=.55,label=w['phase'].replace('_60s',''))
            ax.set_ylabel(unit); ax.set_xlabel('Seconds since first sample'); ax.grid(alpha=.2)
        axes[idx,0].set_title(f"{r['service']} repeat {r['repeat']}")
        if idx==0:
            axes[idx,2].legend(fontsize=7,loc='best')
        for name in ('gpu.csv','host.csv','baseline-summary.json'):
            sources.append({'path':str((folder/name).relative_to(root)), 'sha256':p2.sha(folder/name)})
    fig.tight_layout()
    fig.savefig(root/'resources.png',dpi=150)
    fig.savefig(root/'resources.svg', metadata={'Date': None})
    plt.close(fig)
    p.save(root/'resources.sources.json', sources)


def report(root):
    paths = sorted((root/'sessions').glob('*/baseline-summary.json'))
    results = [load(x) for x in paths]
    # Recompute validation from raw data so summary flags are not trusted by themselves.
    for r in results:
        r.update(analyze_session(root/'sessions'/r['session'],r))
    keys = [(r['service'],r['repeat']) for r in results]
    expected = {(s,i) for s in ('vllm','sglang') for i in (1,2,3)}
    restoration = load(root/'restoration.json') if (root/'restoration.json').exists() else {}
    cfg = load(root/'config.json')
    checks = {'six_independent_restarts':len(keys)==6 and set(keys)==expected,
              'session_checks':bool(results) and all(r['checks_pass'] for r in results),
              'dataset_hash':p2.sha(root/'dataset.json')==cfg['dataset_sha256'],
              'restoration_exit_zero':restoration.get('result',{}).get('exit_code')==0,
              'production_files_unchanged':restoration.get('env_unchanged') is True and restoration.get('compose_unchanged') is True,
              'containers_stopped':bool(restoration.get('containers')) and not any(x['Running'] for x in restoration.get('containers',{}).values())}
    p.save(root/'validation.json', {'utc':p.utc(),'checks':checks,'all_checks_pass':all(checks.values()),
           'completed_sessions':len(results),'expected_sessions':6,
           'sessions':[{k:r[k] for k in ('session','service','repeat','checks','checks_pass')} for r in results]})
    p.save(root/'summary.json', results)
    rows=[]
    for r in results:
        for stage in r['stages']:
            row={k:r[k] for k in ('service','repeat','session','ready_seconds','stop_seconds')}
            row.update(phase=stage['phase'],seconds=stage['seconds'])
            for metric in ('gpu_mib','host_ram_gib','host_cpu_percent','gpu_util_percent','gpu_power_w'):
                row.update({metric+'_'+k:v for k,v in stage[metric].items()})
            rows.append(row)
    p2.write_csv(root/'stage-resources.csv', rows)
    if results:
        plot(root, results)
    write_report(root,results,checks)


def write_report(root,results,checks):
    execution=load(root/'execution.json') if (root/'execution.json').exists() else {}
    measured_at=execution.get('completed_at_utc') or '尚未完成'
    lines=['# P3：Control 启动、空闲与释放基线', '',
           
           f"完成 {len(results)}/6 次独立启动，校验{'通过' if all(checks.values()) else '未全部通过'}。目标为每框架三轮，已记录 {sum(len(r['windows']) for r in results)} 个 60 秒观察窗口。", '',
           '## 结果概览', '',
           '| 框架 | 预热空闲整卡 MiB | 停服后整卡 MiB | 停服后相对本轮启动前的残差 MiB |',
           '| --- | ---: | ---: | --- |']
    for service in ('vllm','sglang'):
        selected=[r for r in results if r['service']==service]
        if not selected: continue
        def med(phase):
            values=[s['gpu_mib']['median'] for r in selected for s in r['stages'] if s['phase']==phase]
            return statistics.median(values)
        residuals=[r['post_stop_minus_no_service_gpu_mib'] for r in selected]
        lines.append(f"| {service} | {med('warm_idle_60s'):.0f} | {med('post_stop_60s'):.0f} | {min(residuals):+.0f} 至 {max(residuals):+.0f} |")
    lines += ['', '整卡显存包含桌面和其他进程。容器停止后显存下降，支持主要驻留占用释放；剩余的显存与主机 RAM 差值，现有采样无法归因给模型、桌面进程或驱动缓存。', '',
           '## 配置与流程', '',
           '本次沿用 P2 冻结 Control：BF16、16K、0 GiB CPU 卸载、服务端并发上限 16，关闭 Graph 和前缀缓存。vLLM 显式缓存预算 1.5 GiB；SGLang 比例 0.90、17 个状态槽和 20480 token 池。生产 `.env` 默认配置另有参数，未在本批测量。', '',
           '每框架独立启动三次，交替执行。每轮按下列顺序记录资源：', '',
           '1. 无服务观察 60 秒。',
           '2. 启动服务，等待 /health 成功。',
           '3. 依次预热 128/512/2048/8192 输入，各输出 8 tokens，再空闲观察 60 秒。',
           '4. 发一条 512/256 测量请求，再观察 60 秒。',
           '5. 停服，再观察 60 秒。', '',
           f"已完成轮次的 {len(results)} 条测量请求与 {4*len(results)} 条预热分开保存。脚本核对单请求原生计数增量为 1/512/256，并检查空闲段没有额外业务请求。", '',
           'ready 从 Compose 启动调用前计到 /health 首次成功，包含容器重建、权重读取和引擎预热，健康检查间隔约 3 秒。表中的首次预热为 128/8，其耗时与 512/256 测量请求分开统计。', '',
           '## 各轮启动与释放', '',
           '| 框架 / 轮次 | ready 秒 | 首次预热 E2E 秒 | 测量请求 E2E 秒 | 停服秒 | 整卡峰值 MiB | 停服后 − 无服务 MiB | 主机 RAM 差 GiB |',
           '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in results:
        lines.append(f"| {r['service']} / {r['repeat']} | {r['ready_seconds']:.2f} | {r['first_warmup_e2e_ms']/1000:.3f} | {r['request']['e2e_ms']/1000:.3f} | {r['stop_seconds']:.2f} | {r['whole_gpu_peak_mib']:.0f} | {r['post_stop_minus_no_service_gpu_mib']:+.1f} | {r['post_stop_minus_no_service_host_ram_gib']:+.3f} |")
    lines += ['', '## 各阶段资源', '',
              '各轮先取 60 秒窗口的中位数，再取三轮中位数；括号为轮间最小值至最大值。no_service 为启动前，warm_idle 为预热空闲，post_request 为请求后，post_stop 为停服后。', '',
              '| 框架 | 阶段 | 整卡 MiB | 主机 RAM GiB | GPU 利用率 % | GPU 功耗 W |',
              '| --- | --- | ---: | ---: | ---: | ---: |']
    for service in ('vllm','sglang'):
        for phase in PHASES:
            stages=[s for r in results if r['service']==service for s in r['stages'] if s['phase']==phase]
            if not stages: continue
            def format_metric(key):
                values=[s[key]['median'] for s in stages if s[key]['median'] is not None]
                return f'{statistics.median(values):.2f} ({min(values):.2f}–{max(values):.2f})' if values else 'unavailable'
            lines.append('| '+' | '.join([service,phase]+[format_metric(k) for k in ('gpu_mib','host_ram_gib','gpu_util_percent','gpu_power_w')])+' |')
    lines += ['', '## 采样与判断限制', '',
              'GPU 和 Windows CPU/RAM 约每秒采样；服务运行期间另采集 cgroup、WSL /proc、Docker stats 与 Prometheus。每窗口要求至少 50 个 GPU/主机样本，采样跨度至少 55 秒，最大间隔不超过 5 秒。整卡峰值仍可能漏过短尖峰。', '',
              '本次在启动的两个时点记录 torch allocated/reserved，未在服务进程内跟踪全程 allocator 峰值。参数与缓存字节见启动张量账本。容器停止后 cgroup 无法读取，主机采样继续。', '',
              '每框架只有三轮，本报告使用中位数和范围描述资源基线。正式并发数据另存于 P4；SGLang CPU 卸载消融尚未执行。', '',
              '## 校验与原始数据', '',
              '[阶段 CSV](stage-resources.csv) · [完整汇总](summary.json) · [校验](validation.json) · [生产恢复](restoration.json) · [执行状态](execution.json)', '',
              '最终恢复记录核对生产文件哈希、恢复命令退出码与两容器停止状态。原始请求、窗口、采样、计数和张量账本在 sessions/；sources/ 保存测量脚本快照，图像输入哈希见 resources.sources.json。', '',
              '![资源时间序列](resources.png)']
    (root/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('--root',required=True,type=Path)
    report(ap.parse_args().root)
