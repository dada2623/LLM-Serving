"""Validate and report P2 data without touching earlier benchmark evidence."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import statistics

import run_p2 as p2


def metric_totals(path):
    result={}
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line or line.startswith('#'):
            continue
        m=re.match(r'([^\s{]+)(?:\{.*\})?\s+([-+\d.eE]+)(?:\s|$)',line)
        if m:
            name,value=m.groups()
            result[name]=result.get(name,0)+float(value)
    return result


def resources(root):
    output=[]
    for session in sorted((root/'sessions').iterdir()):
        if not (session/'manifest.json').exists():
            continue
        m=json.loads((session/'manifest.json').read_text(encoding='utf-8'))
        rows=list(csv.DictReader((session/'gpu.csv').open(encoding='utf-8'))) if (session/'gpu.csv').exists() else []
        def values(phase):
            return [float(r['memory_used_mib']) for r in rows if (phase is None or r['phase']==phase) and re.match(r'^\d+(\.\d+)?$',r['memory_used_mib'])]
        before=values('pre_start'); idle=values('warm_idle'); all_values=values(None)
        log=(session/'server.log').read_text(encoding='utf-8') if (session/'server.log').exists() else ''
        ledger=session/'storage-ledger.json'
        if not ledger.exists():
            ledger=session/'startup-storage-ledger.json'
        storage=json.loads(ledger.read_text(encoding='utf-8')) if ledger.exists() else []
        weights=[r for r in storage if r['kind']=='weights']
        def gpu_bytes(row,kind):
            return sum(v for k,v in row[kind]['unique_storage_bytes_by_device'].items() if k.startswith('cuda'))
        weights_bytes=max((gpu_bytes(r,'combined') for r in weights),default=None)
        cpu_weight_bytes=max((r['parameters']['unique_storage_bytes_by_device'].get('cpu',0) for r in weights),default=None)
        pools=[r for r in storage if r['kind']==('runner_caches' if m['service']=='vllm' else 'pool')]
        pool_bytes=sum(gpu_bytes(r,'pool') for r in pools) if pools else m.get('actual_cache_storage_bytes')
        def find(pattern):
            match=re.search(pattern,log,re.I)
            return match.group(1) if match else None
        output.append({'run_id':session.name,'service':m['service'],'status':m['status'],
            'baseline_gpu_mib':statistics.median(before) if before else None,
            'warm_idle_gpu_mib':statistics.median(idle) if idle else None,
            'whole_gpu_peak_mib':max(all_values) if all_values else None,
            'gpu_samples':len(rows),'ready_seconds':m.get('ready_seconds'),
            'gpu_model_parameter_buffer_storage_bytes':weights_bytes,'cpu_parameter_storage_bytes':cpu_weight_bytes,
            'measured_cache_pool_storage_bytes':pool_bytes,
            'shared_cache_target_residual_fraction':(pool_bytes-1610612736)/1610612736 if pool_bytes else None,
            'vllm_weights_gib_log_rounded':find(r'Model loading took ([\d.]+) GiB'),
            'vllm_kv_tokens':find(r'GPU KV cache size: ([\d,]+) tokens'),
            'sglang_mamba_conv_gib_log_rounded':find(r'conv_state size: ([\d.]+)GB'),
            'sglang_mamba_ssm_gib_log_rounded':find(r'ssm_state size: ([\d.]+)GB'),
            'sglang_kv_tokens':find(r'KV Cache is allocated.*?#tokens: ([\d,]+)'),
            'sglang_k_gib_log_rounded':find(r'K size: ([\d.]+) GB'),
            'sglang_v_gib_log_rounded':find(r'V size: ([\d.]+) GB'),
            'cpu_offload_gib_arg':0,'error':m.get('error')})
    p2.write_csv(root/'resources.csv',output)
    return output


def alignment(root):
    paired={}
    for session in sorted((root/'sessions').iterdir()):
        ledger=session/'storage-ledger.json'
        if not ledger.exists():
            ledger=session/'startup-storage-ledger.json'
        if not ledger.exists():
            continue
        storage=json.loads(ledger.read_text(encoding='utf-8'))
        weights=[r for r in storage if r['kind']=='weights']
        if not weights:
            continue
        row=weights[-1]
        service=row['service']
        def gpu(record,key):
            return sum(v for k,v in record[key]['unique_storage_bytes_by_device'].items() if k.startswith('cuda'))
        pools=[r for r in storage if r['kind']==('runner_caches' if service=='vllm' else 'pool')]
        if not pools:
            continue
        paired[service]={'run_id':session.name,'cuda_parameter_bytes':gpu(row,'parameters'),
            'cuda_buffers_bytes':gpu(row,'buffers'),'cpu_parameter_bytes':row['parameters']['unique_storage_bytes_by_device'].get('cpu',0),
            'cache_pool_bytes':sum(gpu(r,'pool') for r in pools)}
    if len(paired)==2:
        a,b=paired['vllm'],paired['sglang']
        paired['comparison']={'parameter_bytes_identical':a['cuda_parameter_bytes']==b['cuda_parameter_bytes'],
            'no_cpu_resident_parameters':a['cpu_parameter_bytes']==b['cpu_parameter_bytes']==0,
            'sglang_minus_vllm_buffer_bytes':b['cuda_buffers_bytes']-a['cuda_buffers_bytes'],
            'sglang_minus_vllm_cache_bytes':b['cache_pool_bytes']-a['cache_pool_bytes'],
            'cache_relative_residual':abs(b['cache_pool_bytes']-a['cache_pool_bytes'])/a['cache_pool_bytes'],
            'scope':'actual tensor storages; runtime workspace and whole-GPU load peaks remain separately observed residuals'}
    p2.p.save(root/'resource-alignment.json',paired)
    return paired


def validate(root):
    cfg=json.loads((root/'config.json').read_text(encoding='utf-8'))
    cells=p2.all_results(root)
    checks=[]
    for c in cells:
        folder=p2.p.ROOT/Path(c['path']).parent
        rows=[json.loads(f.read_text(encoding='utf-8')) for f in folder.glob('request-*.json')]
        before=metric_totals(folder/'metrics-before.prom')
        after=metric_totals(folder/'metrics-after.prom')
        delta={k:after.get(k,0)-before.get(k,0) for k in after}
        service=c['service']
        keys=(['vllm:request_success_total','vllm:prompt_tokens_total','vllm:generation_tokens_total'] if service=='vllm'
              else ['sglang:num_requests_total','sglang:prompt_tokens_total','sglang:generation_tokens_total'])
        good=[r for r in rows if r['status']=='success']
        expected=[len(good),sum(r['usage']['prompt_tokens'] for r in good),sum(r['usage']['completion_tokens'] for r in good)]
        reconciled={k:{'client':n,'native_delta':delta.get(k),'matches':k in delta and abs(delta[k]-n)<.1} for k,n in zip(keys,expected)}
        check={'run_id':c['run_id'],'cell_id':c['cell_id'],'requests':len(rows),
            'count_matches':len(rows)==c['sent'],
            'lengths_match':all(r['length_match'] for r in good),
            'unique_request_indices':len({r['index'] for r in rows})==len(rows),
            'reconciliation':reconciled,
            'prefix_hits_delta':{k:v for k,v in delta.items() if 'hit' in k and ('cache' in k or 'token' in k)},
            'preemptions_or_retractions_delta':{k:v for k,v in delta.items() if 'preempt' in k or 'retract' in k}}
        check['passed']=check['count_matches'] and check['lengths_match'] and check['unique_request_indices'] and all(r['matches'] for r in reconciled.values())
        checks.append(check)
    formal=[c for c in cells if c['phase']=='formal']
    full_primary=len(formal)==90 and all(c['sample_gate'] and c['duration_gate'] and c['failed']==0 for c in formal)
    secret_hits=[]
    secrets=[v for k,v in p2.p.SETTINGS.items() if k.upper().endswith(('_KEY','_TOKEN','_PASSWORD','_SECRET')) and len(v)>=6]
    for path in root.rglob('*'):
        if path.is_file() and path.suffix in ('.json','.jsonl','.csv','.prom','.log','.md','.txt'):
            data=path.read_bytes()
            if any(s.encode() in data for s in secrets):
                secret_hits.append(str(path.relative_to(root)))
    result={'updated_at_utc':p2.p.utc(),'cells':checks,'cell_checks_pass':bool(checks) and all(c['passed'] for c in checks),
        'full_primary_formal_matrix_complete':full_primary,'formal_cells_observed':len(formal),'formal_cells_required':90,
        'production_env_unchanged':p2.sha(p2.p.ROOT/'.env')==cfg['production_env_sha256'],
        'production_compose_unchanged':p2.sha(p2.p.ROOT/'compose.yaml')==cfg['production_compose_sha256'],
        'secret_scan_pass':not secret_hits,'secret_hit_files':secret_hits}
    p2.p.save(root/'validation.json',result)
    return result


def plots(root,groups):
    formal=[g for g in groups if g['phase']=='formal']
    if not formal:
        return
    import sys
    sys.path.insert(0,str(p2.p.ROOT/'.tools/benchmark-plot'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    workloads=['128-128','512-256','2048-256']
    colors={'vllm':'#2563eb','sglang':'#e07020'}
    fig,axes=plt.subplots(3,3,figsize=(13,10),layout='constrained')
    for col,w in enumerate(workloads):
        for service in ('vllm','sglang'):
            points=sorted([r for r in formal if r['service']==service and r['workload']==w],key=lambda r:r['concurrency'])
            if not points:
                continue
            x=[r['concurrency'] for r in points]
            axes[0,col].errorbar(x,[r['throughput_mean'] for r in points],
                yerr=[r['throughput_sd'] or 0 for r in points],marker='o',capsize=3,color=colors[service],label=service)
            axes[1,col].plot(x,[r['ttft_ms_p95']/1000 for r in points],marker='o',color=colors[service])
            axes[2,col].plot(x,[r['goodput_rps'] for r in points],marker='o',color=colors[service])
        for row in range(3):
            axes[row,col].set_xscale('log',base=2)
            axes[row,col].set_xticks([1,2,4,8,16],[1,2,4,8,16])
            axes[row,col].grid(alpha=.2)
        axes[0,col].set_title('Input/output '+w)
        if axes[0,col].get_legend_handles_labels()[0]:
            axes[0,col].legend()
        axes[2,col].set_xlabel('Closed-loop client concurrency')
    axes[0,0].set_ylabel('Output tokens/s (repeat mean +/- SD)')
    axes[1,0].set_ylabel('TTFT P95 (seconds, pooled raw requests)')
    axes[2,0].set_ylabel('Goodput (requests/s, pooled full windows)')
    fig.suptitle('P2 control: Qwen3.5-9B-AWQ, BF16, 16K, no CPU offload\nOfficial image stacks differ; see resource residuals in REPORT.md')
    for extension in ('png','svg'):
        fig.savefig(root/('comparison.'+extension),dpi=170, metadata={'Date': None} if extension == 'svg' else None)
    plt.close(fig)
    p2.p.save(root/'comparison.sources.json',{'aggregate_sha256':p2.sha(root/'aggregate.csv'),
        'generated_at_utc':p2.p.utc(),'group_count':len(formal)})


def report(root):
    cfg=json.loads((root/'config.json').read_text(encoding='utf-8'))
    summary=json.loads((root/'summary.json').read_text(encoding='utf-8'))
    res=resources(root)
    aligned=alignment(root)
    v=validate(root)
    write_report(root,cfg,summary,res,aligned,v)
    plots(root,summary['groups'])
    print(json.dumps({k:value for k,value in v.items() if k!='cells'},ensure_ascii=False),flush=True)


def write_report(root,cfg,summary,res,aligned,v):
    formal=[g for g in summary['groups'] if g['phase']=='formal']
    screen=[g for g in summary['groups'] if g['phase']=='screen']
    def f(x):
        return 'N/A' if x is None else f'{x:.2f}' if isinstance(x,(float,int)) else str(x).rstrip(',')
    complete=v['full_primary_formal_matrix_complete']
    lines=['# P4：Control 正式并发测试', '',
        
        '状态：'+('三档负载的正式矩阵已完成。' if complete else '正式矩阵尚未完成，以下仅为已有单元结果。'), '',
        f"筛选成功 {sum(g['success'] for g in screen)} 条，错误 {sum(g['failed'] for g in screen)} 条；正式成功 {sum(g['success'] for g in formal)} 条，错误 {sum(g['failed'] for g in formal)} 条。请求成功与延迟达标分别统计。", '',
        '本批使用冻结的 Control 配置。生产 `.env` 保留早期部署参数，其性能需另测。历史目录名为 p2，按[当前计划](../../PLAN.md)归入 P2 校准与 P4 正式测试。', '',
        '## 最大达标已测并发', '',
        '| 框架 | 输入/输出 tokens | 三轮均达标的最大已测并发 |', '| --- | --- | ---: |']
    for service in ('vllm','sglang'):
        for workload in ('128-128','512-256','2048-256'):
            passing=[g['concurrency'] for g in formal if g['service']==service and g['workload']==workload and g['formal_gate'] and g['all_repeat_slo_pass']]
            lines.append(f"| {service} | {workload} | {max(passing) if passing else '尚无结论'} |")
    lines+=['', '容量指三轮都满足本次 SLO 的最大已测闭环并发。开环到达率扫描尚未执行，最大 RPS 未知。', '',
        '## 配置与样本', '',
        '| 项目 | 设置 |', '| --- | --- |',
        '| 模型与输入 | 同一 QuantTrio/Qwen3.5-9B-AWQ、tokenizer、聊天模板，两镜像生成的完整输入 IDs 一致 |',
        '| 计算与内存 | BF16、16K、0 GiB CPU 卸载、prefill 分块 512 |',
        '| 执行方式 | CUDA Graph、前缀缓存、thinking 均关闭；服务端并发上限 16 |',
        '| 工作负载 | 128/128、512/256、2048/256 输入/输出 tokens |',
        '| 客户端 | 闭环并发 1/2/4/8/16 |',
        '| 筛选 | 每单元至少 30 条，3 轮 |',
        '| 正式 | 每单元至少 100 条且至少 60 秒，3 次独立容器重复 |', '',
        '每轮独立启动、预热，交替框架顺序。每单元另做同长度、同并发预热；预热与校准不计正式统计。脚本保留失败请求，中断单元重测。', '',
        '脚本用 `ignore_eos=true` 和 `max_tokens` 固定输出，逐条核对 usage 与 `finish_reason=length`。吞吐从第一条提交计到最后一条响应结束，包含收尾；分位数从合并后的原始请求重算，另存逐轮吞吐及标准差。TPOT 根据内容 chunk 时间和最终 usage 估算，精确逐 token ITL 未测。', '',
        '## 延迟阈值', '',
        '| 指标 | 冻结阈值 |', '| --- | --- |',
        '| TTFT | 输入 128/512/2048/8192 分别不超过 2/3/5/10 秒 |',
        '| TPOT | 不超过 100 ms |',
        '| E2E | 不超过对应 TTFT 阈值 + 输出目标 × 100 ms |',
        '| 错误率 / 延迟达标率 | 不超过 1% / 不低于 95% |', '',
        '这些是合成测试阈值，尚未按生产需求验收。Goodput 为同时满足三项延迟阈值的成功请求数除以完整窗口。', '',
        '## 正式结果', '',
        '表中延迟单位为 ms，分位数顺序为 P50/P90/P95。输出 tok/s 为批次总吞吐。', '',
        '| 框架 | 输入/输出 | 并发 | 重复 | 成功请求 | 输出 tok/s | TTFT P50/P90/P95 ms | TPOT P50/P90/P95 ms | E2E P50/P90/P95 ms | Goodput req/s | SLO达标率 |',
        '| --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- | ---: | ---: |']
    for g in formal:
        parts=['/'.join(f(g[k+'_'+q]) for q in ('p50','p90','p95')) for k in ('ttft_ms','tpot_chunk_ms','e2e_ms')]
        lines.append('| '+' | '.join([g['service'],g['workload'],str(g['concurrency']),str(g['repeats']),str(g['success']),f(g['output_tokens_per_s']),*parts,f(g['goodput_rps']),f(g['slo_fraction']*100)+'%'])+' |')
    lines+=['','## 实际资源与残差','',
        f"vLLM 显式缓存预算为 {cfg['vllm_pool_bytes']} bytes。SGLang 固定 token 池 {cfg['sglang_token_pool']}、线性状态槽 {cfg['sglang_state_slots']}。池结构不同，需按实际张量存储核对。"]
    if 'comparison' in aligned:
        a,b,c=aligned['vllm'],aligned['sglang'],aligned['comparison']
        lines+=['', '| 资源 / bytes | vLLM | SGLang |', '| --- | ---: | ---: |',
            f"| CUDA 参数 | {a['cuda_parameter_bytes']} | {b['cuda_parameter_bytes']} |",
            f"| CPU 参数 | {a['cpu_parameter_bytes']} | {b['cpu_parameter_bytes']} |",
            f"| 缓存池 | {a['cache_pool_bytes']} | {b['cache_pool_bytes']} |", '',
            f"缓存池残差为 {c['cache_relative_residual']*100:.4f}%；SGLang 模型缓冲区多 {c['sglang_minus_vllm_buffer_bytes']} bytes。配对 run 和账本见 [resource-alignment.json](resource-alignment.json)。"]
    lines+=['', '两边均无 CPU 参数驻留，仍存在以下差异：', '',
        '- 混合缓存池的布局、块粒度、状态槽与工作区不同；客户端并发也可能因排队或抢占而高于服务端同时运行数。',
        '- 官方镜像携带的 PyTorch、Triton、FlashInfer、tokenizers 版本不同，无法单独分离框架与依赖栈的贡献。',
        '- Windows / WSL 2、9P 挂载和桌面后台负载影响本机结果。整卡采样包含其他进程，约 1 秒间隔可能漏掉短峰值。', '',
        '因此结果适用于本机这两套 Control 实现。P0 带卸载的速率与本批分开统计。', '',
        '## 校验与数据', '',
        f"完成单元共 {summary['cells']} 个（含校准）；正式单元 {v['formal_cells_observed']}/{v['formal_cells_required']}。数据校验通过：{v['cell_checks_pass']}。", '',
        '| 文件 | 内容 |', '| --- | --- |',
        '| [aggregate.csv](aggregate.csv) | 合并请求后的统计 |',
        '| [cell-results.csv](cell-results.csv) | 各单元、各轮结果 |',
        '| [validation.json](validation.json) | 长度、usage、原生计数、完整性和凭据检查 |',
        '| [restoration.json](restoration.json) | 生产文件哈希、容器定义恢复与停止状态 |',
        '| sessions/ | 逐请求、流式时间戳、服务日志、GPU/主机/WSL 与原生指标 |', '',
        '8192/128、混合长度、开环与静态 batch 尚未执行。续跑和退出检查见[批次说明](README.md)。', '',
        '![吞吐与延迟对照](comparison.png)', '',
        '<details>', '<summary>筛选结果（与正式统计分开）</summary>', '',
        '| 框架 | 输入/输出 | 并发 | 已完成重复 | 成功数 | 输出 tok/s | TTFT P95 ms | E2E P95 ms |',
        '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for g in screen:
        lines.append('| '+' | '.join([g['service'],g['workload'],str(g['concurrency']),str(g['repeats']),str(g['success']),f(g['output_tokens_per_s']),f(g['ttft_ms_p95']),f(g['e2e_ms_p95'])])+' |')
    lines+=['', '</details>', '', '<details>', '<summary>逐次启动的资源记录（含校准与中断）</summary>', '',
        '缺失值记为 N/A。完整机器字段见 [resources.csv](resources.csv)。', '',
        '| 运行 | 框架 | 预热整卡 MiB | 整卡峰值 MiB | CUDA 参数与缓冲区 bytes | CPU 参数 bytes | 实测缓存 bytes | vLLM KV tokens | SGLang KV tokens | SGLang Conv/SSM GiB |',
        '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |']
    for r in res:
        lines.append('| '+' | '.join([r['run_id'],r['service'],f(r['warm_idle_gpu_mib']),f(r['whole_gpu_peak_mib']),f(r['gpu_model_parameter_buffer_storage_bytes']),f(r['cpu_parameter_storage_bytes']),f(r['measured_cache_pool_storage_bytes']),f(r['vllm_kv_tokens']),f(r['sglang_kv_tokens']),f(r['sglang_mamba_conv_gib_log_rounded'])+'/'+f(r['sglang_mamba_ssm_gib_log_rounded'])])+' |')
    lines+=['', '</details>', '']
    (root/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    index=p2.B/'reports/P2-CONTROL-REPORT.md'
    rel='../'+str(root.resolve().relative_to(p2.B)).replace('\\','/')
    index.write_text('# Control 报告入口\n\n完整报告：['+root.name+']('+rel+'/REPORT.md)。\n\n'
        '本批包含 P2 资源校准和 P4 正式并发测试，历史目录名保持不变。配置、统计与续跑见[批次说明]('+rel+'/README.md)。\n',encoding='utf-8')


if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--root',required=True,type=Path)
    report(a.parse_args().root)
