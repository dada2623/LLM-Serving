"""Resumable P2 control experiment. No production configuration changes/downloads.

All output is synthetic; API keys are read in memory and redacted on disk.
Run prepare, calibrate, then run. Each completed cell is immutable and resumable.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import hashlib
import json
import math
from pathlib import Path
import random
import re
import shutil
import statistics
import subprocess
import threading
import time
import traceback
import urllib.error
import urllib.request

import run_p0 as p

B = p.BENCH
LOCK = threading.Lock()
SEED = 20261031
WORKLOADS = {"128-128": (128, 128), "512-256": (512, 256),
             "2048-256": (2048, 256), "8192-128": (8192, 128), "mixed": None}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def log(value):
    print(p.clean(value), flush=True)


def quantile(values, q):
    if not values:
        return None
    values = sorted(values)
    k = (len(values) - 1) * q
    lo = int(k)
    return values[lo] + (values[min(lo + 1, len(values) - 1)] - values[lo]) * (k - lo)


def write_csv(path, rows):
    if not rows:
        return
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(dict.fromkeys(k for r in rows for k in r)))
        writer.writeheader()
        writer.writerows(rows)


def prometheus_values(body):
    values={}
    for line in body.splitlines():
        match=re.match(r'^([^#\s{]+)(?:\{.*\})?\s+([-+\d.eE]+)(?:\s|$)',line)
        if match:
            key,value=match.groups()
            values[key]=values.get(key,0)+float(value)
    return values


def flag(command, name, value=None):
    if name in command:
        i = command.index(name)
        if value is not None:
            command[i + 1] = str(value)
    else:
        command.append(name)
        if value is not None:
            command.append(str(value))


def command(service, cfg):
    c = p.command_profile(service, 'control')
    flag(c, '--cpu-offload-gb', 0)
    flag(c, '--dtype', 'bfloat16')
    if service == 'vllm':
        flag(c, '--max-num-seqs', cfg['server_concurrency'])
        flag(c, '--gpu-memory-utilization', 0.72)
        flag(c, '--kv-cache-memory-bytes', cfg['vllm_pool_bytes'])
        flag(c, '--no-enable-prefix-caching')
        flag(c, '--mamba-ssm-cache-dtype', 'float32')
        flag(c, '--seed', SEED)
    else:
        flag(c, '--mem-fraction-static', cfg.get('sglang_mem_fraction', 0.90))
        flag(c, '--max-running-requests', cfg['server_concurrency'])
        flag(c, '--max-mamba-cache-size', cfg['sglang_state_slots'])
        flag(c, '--max-total-tokens', cfg['sglang_token_pool'])
        flag(c, '--mamba-ssm-dtype', 'float32')
        flag(c, '--disable-radix-cache')
        flag(c, '--random-seed', SEED)
    return c


def prepare(root, args):
    root.mkdir(parents=True, exist_ok=False)
    cfg = {'stage': 'P2', 'created_at_utc': p.utc(), 'seed': SEED,
           'server_concurrency': 16, 'client_concurrency': [1, 2, 4, 8, 16],
           'vllm_pool_bytes': int(args.pool_gib * 2**30), 'sglang_state_slots': args.sg_states,
           'sglang_token_pool': args.sg_tokens, 'sglang_mem_fraction': 0.90,
           'dtype': 'bfloat16', 'context_length': 16384,
           'cpu_offload_gib': 0, 'graphs': 'disabled', 'prefix_reuse': 'disabled on both engines',
           'screen_requests': 30, 'formal_requests': 100, 'formal_min_seconds': 60,
           'repeats': 3, 'request_timeout_seconds': 180,
           'slo_policy': 'Synthetic benchmark thresholds fixed before engine measurements; not production requirements',
           'slo_ttft_ms': {'128': 2000, '512': 3000, '2048': 5000, '8192': 10000},
           'slo_tpot_ms': 100, 'slo_e2e': 'TTFT threshold + output_target * 100ms',
           'slo_error_rate_max': 0.01, 'slo_attainment_min': 0.95,
           'production_env_sha256': sha(p.ROOT / '.env'),
           'production_compose_sha256': sha(p.ROOT / 'compose.yaml'),
           'scope': 'closed-loop control; open-loop and true static batch excluded',
           'formal_selection': 'all screened cells with >=30 successes in every repeat and zero errors; failed cells retained',
           'long_mixed_gate': 'first three workloads must have valid formal results on both engines',
           'restoration': 'restore production container definitions; original services initially stopped'}
    p.save(root / 'config.json', cfg)
    p.save(root / 'host.json', {'utc': p.utc(), 'nvidia_smi': p.shell(['nvidia-smi', '-q']),
        'processes': p.shell(['nvidia-smi']), 'docker_version': p.shell(['docker', 'version']),
        'wsl_version': p.shell(['wsl', '--version']),
        'docker_info': p.shell(['docker', 'info', '--format', '{{json .}}'])})
    p.save(root / 'initial-containers.json', {s: {k: p.inspect('llm-deployment-' + s + '-1').get(k)
           for k in ('Id', 'Image', 'State')} for s in ('vllm', 'sglang')})
    assets = json.loads((B / 'configs/model-assets.json').read_text(encoding='utf-8'))
    model = Path(p.SETTINGS['LOCAL_MODELS_DIR']) / 'Qwen3.5-9B-AWQ'
    checked = []
    for item in assets['files']:
        path = model / item['path']
        checked.append({'path': item['path'], 'sha256': sha(path),
                        'matches': sha(path) == item['expected_sha256']})
    p.save(root / 'model-hashes.json', checked)
    if not all(x['matches'] for x in checked):
        raise RuntimeError('Model content mismatch')
    code = r'''
import json,hashlib
from transformers import AutoTokenizer
t=AutoTokenizer.from_pretrained('/models/Qwen3.5-9B-AWQ',local_files_only=True)
cases={}
for target in (128,512,2048,8192):
    raw=t.encode('P2 synthetic measurements: latency memory throughput. '*3000,add_special_tokens=False)
    end='\nWrite a numbered list of 1000 practical Python programming tips. Begin immediately.'
    def build(n):
        m=[{'role':'user','content':t.decode(raw[:n])+end}]
        ids=t.apply_chat_template(m,tokenize=True,add_generation_prompt=True,enable_thinking=False)
        if hasattr(ids,'keys'): ids=ids['input_ids']
        return m,ids
    low,high=1,target
    while low<high:
        mid=(low+high)//2
        if len(build(mid)[1])<target: low=mid+1
        else: high=mid
    found=None
    for n in range(max(1,low-12),low+12):
        m,ids=build(n)
        if len(ids)==target: found=(m,ids); break
    if found is None: raise RuntimeError('exact length construction failed '+str(target))
    m,ids=found
    cases[str(target)]={'messages':m,'input_ids':ids,'expected_input_tokens':len(ids)}
print(json.dumps(cases))
'''
    data = []
    for service in ('vllm', 'sglang'):
        result = p.shell(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'python3',
                          '-v', str(model) + ':/models/Qwen3.5-9B-AWQ:ro',
                          'llm-deployment/' + service + ':local', '-c', code], timeout=180)
        dataset = json.loads(result['stdout'].strip().splitlines()[-1])
        p.save(root / (service + '-dataset.json'), dataset)
        data.append(dataset)
        image = json.loads(p.shell(['docker', 'image', 'inspect', 'llm-deployment/' + service + ':local'])['stdout'])[0]
        p.save(root / (service + '-image.json'), {k: image.get(k) for k in ('Id', 'RepoTags', 'RepoDigests', 'Created', 'RootFS')})
    if data[0] != data[1]:
        raise RuntimeError('Tokenizer/template mismatch')
    p.save(root / 'dataset.json', data[0])
    cfg['dataset_sha256'] = sha(root / 'dataset.json')
    cfg['tokenizer_gate'] = 'passed: identical messages and token IDs in both installed images'
    p.save(root / 'config.json', cfg)
    log('PREPARED ' + str(root))


def request(base, case, index, folder):
    out = case['output_tokens']
    payload = {'model': p.SETTINGS.get('SERVED_MODEL_NAME', 'Qwen3.5-9B-AWQ'),
        'messages': case['messages'], 'max_tokens': out, 'temperature': 0, 'top_p': 1,
        'seed': SEED, 'ignore_eos': True, 'stream': True,
        'stream_options': {'include_usage': True}, 'chat_template_kwargs': {'enable_thinking': False}}
    req = urllib.request.Request(base + '/v1/chat/completions', data=json.dumps(payload).encode(),
        headers={'Authorization': 'Bearer ' + p.KEY, 'Content-Type': 'application/json'})
    row = {'index': index, 'input_target': case['expected_input_tokens'], 'output_target': out,
           'send_utc': p.utc(), 'status': 'error'}
    start = time.perf_counter_ns()
    row['send_ns'] = start
    first, last, usage, finish, done = None, None, None, None, False
    chunks = []
    try:
        # Each worker owns its opener; no pooled-connection/client queue ambiguity.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=180) as response:
            row['headers_ms'] = (time.perf_counter_ns() - start) / 1e6
            for raw in response:
                now = time.perf_counter_ns()
                if not raw.startswith(b'data:'):
                    continue
                text = raw[5:].strip()
                if text == b'[DONE]':
                    done = True
                    break
                event = json.loads(text)
                if event.get('error'):
                    raise RuntimeError(str(event['error']))
                usage = event.get('usage') or usage
                for choice in event.get('choices', []):
                    content = choice.get('delta', {}).get('content')
                    if content:
                        first = first or now
                        last = now
                        chunks.append({'ns': now, 'text': content})
                    finish = choice.get('finish_reason') or finish
        row['status'] = 'success' if done and first and usage else 'invalid_stream'
    except Exception as exc:
        if isinstance(exc, urllib.error.HTTPError):
            row['error'] = p.clean(f'HTTP {exc.code}: ' + exc.read().decode(errors='replace'))
        else:
            row['error'] = p.clean(str(exc))
    end = time.perf_counter_ns()
    row.update({'end_ns': end, 'completed_utc': p.utc(), 'e2e_ms': (end-start)/1e6,
        'ttft_ms': (first-start)/1e6 if first else None, 'usage': usage, 'finish_reason': finish,
        'received_done': done, 'content_chunks': len(chunks),
        'tpot_chunk_ms': (last-first)/1e6/(usage['completion_tokens']-1)
        if usage and usage.get('completion_tokens', 0)>1 and first and last>first else None})
    row['length_match'] = bool(usage and usage.get('prompt_tokens')==case['expected_input_tokens']
                               and usage.get('completion_tokens')==out and finish=='length')
    if row['status']=='success' and not row['length_match']:
        row['status']='length_mismatch'
    p.save(folder / ('request-%05d.json' % index), row)
    with LOCK:
        p.append(folder / 'streams.jsonl', {'index': index, 'chunks': chunks})
    return row


def stats(rows, seconds, cfg):
    good = [r for r in rows if r['status']=='success']
    def meets(r):
        limit = cfg['slo_ttft_ms'][str(r['input_target'])]
        return r['ttft_ms']<=limit and r['tpot_chunk_ms'] is not None and r['tpot_chunk_ms']<=cfg['slo_tpot_ms'] and r['e2e_ms']<=limit+r['output_target']*cfg['slo_tpot_ms']
    n_slo = sum(meets(r) for r in good)
    out = {'sent': len(rows), 'success': len(good), 'failed': len(rows)-len(good),
        'window_seconds': seconds, 'error_rate': 1-len(good)/len(rows) if rows else 1,
        'slo_met': n_slo, 'slo_fraction': n_slo/len(rows) if rows else 0,
        'rps': len(good)/seconds, 'goodput_rps': n_slo/seconds,
        'output_tokens_per_s': sum(r['usage']['completion_tokens'] for r in good)/seconds,
        'input_tokens_per_s': sum(r['usage']['prompt_tokens'] for r in good)/seconds}
    for key in ('ttft_ms', 'tpot_chunk_ms', 'e2e_ms'):
        values = [r[key] for r in good if r.get(key) is not None]
        for q, suffix in ((.5, 'p50'), (.9, 'p90'), (.95, 'p95')):
            out[key+'_'+suffix] = quantile(values, q)
    out['slo_pass'] = out['error_rate']<=cfg['slo_error_rate_max'] and out['slo_fraction']>=cfg['slo_attainment_min']
    return out


def cell(root, session, base, collector, cfg, dataset, phase, workload, conc, repeat):
    name = f'{phase}-{workload}-c{conc:02}-r{repeat:02}'
    folder = session / name
    if (folder / 'summary.json').exists():
        return json.loads((folder / 'summary.json').read_text(encoding='utf-8'))
    folder.mkdir(exist_ok=False)
    # Warm the actual batch shape before taking native counter baselines or timing.
    # A new batch shape can compile additional kernels even after c=1 warmup.
    warm=folder/'batch-warmup'; warm.mkdir()
    collector.phase=name+'-warmup'
    inp=8192 if workload=='mixed' else WORKLOADS[workload][0]
    with ThreadPoolExecutor(max_workers=conc) as executor:
        warm_rows=list(executor.map(lambda i:request(base,dataset[str(inp)]|{'output_tokens':8},i,warm),range(conc)))
    if any(r['status']!='success' for r in warm_rows):
        raise RuntimeError('Batch-shape warmup failed; timing was not started')
    time.sleep(1)
    collector.phase = name
    count = cfg['screen_requests'] if phase=='screen' else cfg['formal_requests'] if phase=='formal' else 3
    duration = cfg['formal_min_seconds'] if phase=='formal' else 0
    before = p.get(base, '/metrics', timeout=10)
    (folder / 'metrics-before.prom').write_text(p.clean(before), encoding='utf-8')
    rows = []
    begin = time.perf_counter_ns()
    deadline = begin + duration*10**9
    counter = 0
    counter_lock = threading.Lock()
    rng = random.Random(SEED + repeat)
    mixed = [(i,o) for i in (128,2048,8192) for o in (32,128,512)]
    rng.shuffle(mixed)
    def worker():
        nonlocal counter
        result = []
        while True:
            with counter_lock:
                if counter>=count and time.perf_counter_ns()>=deadline:
                    break
                if len([r for r in rows if r['status']!='success'])>=3:
                    break
                index=counter
                counter+=1
            inp,out = mixed[index%len(mixed)] if workload=='mixed' else WORKLOADS[workload]
            row = request(base, dataset[str(inp)] | {'output_tokens': out}, index, folder)
            with counter_lock:
                rows.append(row)
            result.append(row)
            if row['status']!='success':
                break
        return result
    log(f'CELL {session.name} {name} target={count} min_seconds={duration}')
    with ThreadPoolExecutor(max_workers=conc) as executor:
        for future in as_completed([executor.submit(worker) for _ in range(conc)]):
            future.result()
    end=time.perf_counter_ns()
    (folder / 'metrics-after.prom').write_text(p.clean(p.get(base,'/metrics',timeout=10)),encoding='utf-8')
    result=stats(rows,(end-begin)/1e9,cfg)
    result.update({'phase':phase,'workload':workload,'concurrency':conc,'repeat':repeat,
        'start_ns':begin,'end_ns':end,'run_id':session.name,'cell_id':name,
        'sample_gate':sum(r['status']=='success' for r in rows)>=count,
        'duration_gate':(end-begin)/1e9>=duration,
        'stop_offer_ns':max((r['send_ns'] for r in rows),default=begin),
        'drain_end_ns':end,'throughput_window':'first send through final response; includes drain',
        'stream_limit':'TPOT from content chunks and authoritative usage; not token ITL'})
    samples=[prometheus_values(r['body']) for r in collector.metrics if r['phase']==name]
    gauges={'server_running_peak':('vllm:num_requests_running','sglang:num_running_reqs'),
            'server_waiting_peak':('vllm:num_requests_waiting','sglang:num_queue_reqs'),
            'kv_usage_fraction_peak':('vllm:kv_cache_usage_perc','sglang:full_token_usage'),
            'mamba_usage_fraction_peak':('sglang:mamba_usage',),
            'mamba_state_slots_used_peak':('sglang:mamba_used_tokens',)}
    for output,names in gauges.items():
        observed=[sample[key] for sample in samples for key in names if key in sample]
        result[output]=max(observed) if observed else None
    gpu=[float(r['memory_used_mib']) for r in collector.gpus if r['phase']==name and re.match(r'^\d+(\.\d+)?$',r['memory_used_mib'])]
    result['load_whole_gpu_peak_mib']=max(gpu) if gpu else None
    result['load_gpu_samples']=len(gpu)
    write_csv(folder/'requests.csv', [{k:v for k,v in r.items() if k!='usage'} | (r.get('usage') or {}) for r in sorted(rows,key=lambda r:r['index'])])
    p.save(folder/'summary.json',result)
    log(f"DONE {name} success={result['success']}/{result['sent']} tok/s={result['output_tokens_per_s']:.2f} p95e2e={result['e2e_ms_p95']} slo={result['slo_fraction']:.3f}")
    return result


class Session:
    def __init__(self, root, service, cfg, tag):
        self.root,self.service,self.cfg=root,service,cfg
        self.folder=root/'sessions'/(p.datetime.now(p.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+service+'-'+tag)
        self.folder.mkdir(parents=True)
        shutil.copy2(root/'config.json', self.folder/'frozen-config.json')
        source_dir=self.folder/'sources'; source_dir.mkdir()
        for source in ('run_p2.py','run_p0.py','container_probe.py','report_p2.py','p2_startup_probe.py'):
            path=Path(__file__).parent/source
            if path.exists():
                shutil.copy2(path,source_dir/source)
        self.container='llm-deployment-'+service+'-1'
        self.base='http://127.0.0.1:'+p.SETTINGS.get('VLLM_PORT' if service=='vllm' else 'SGLANG_PORT','8000' if service=='vllm' else '30000')
        self.collector=p.Collector(self.folder,self.base,self.container)
        self.manifest={'service':service,'tag':tag,'created_at_utc':p.utc(),'command':command(service,cfg),'status':'starting',
                       'config_sha256':sha(root/'config.json'),'dataset_sha256':sha(root/'dataset.json'),
                       'script_sha256':sha(Path(__file__)),'production_env_sha256':sha(p.ROOT/'.env')}
        override={'services':{service:{'command':self.manifest['command'], 'healthcheck':{'disable':True},
            'entrypoint':['python3','/opt/p2/p2_startup_probe.py'],
            'environment':{'P2_PROBE_SERVICE':service},
            'volumes':[{'type':'bind','source':str(source_dir),'target':'/opt/p2','read_only':True}]}}}
        # Keep the Compose interpolation placeholder; generic redaction would break auth.
        (self.folder/'compose.override.json').write_text(json.dumps(override,indent=2),encoding='utf-8')
        self.compose=p.COMPOSE+['-f',str(self.folder/'compose.override.json')]
        self.since=p.utc()

    def start(self):
        p.save(self.folder/'manifest.json',self.manifest)
        p.shell(p.COMPOSE+['stop','vllm','sglang'],timeout=90)
        p.save(self.folder/'background-gpu.json',p.shell(['nvidia-smi']))
        self.collector.launch()
        time.sleep(3)
        self.collector.phase='startup'
        begin=time.perf_counter()
        p.save(self.folder/'compose-start.json',p.shell(self.compose+['up','-d','--no-build','--pull','never','--force-recreate',self.service],timeout=120))
        log('START '+self.folder.name)
        while time.perf_counter()-begin<900:
            try:
                p.get(self.base,'/health',timeout=2)
                break
            except (OSError,urllib.error.URLError):
                state=p.inspect(self.container)['State']
                if not state['Running']:
                    raise RuntimeError('Service exited: '+str({k:state.get(k) for k in ('ExitCode','OOMKilled','Error')}))
                time.sleep(3)
        else:
            raise TimeoutError('Engine readiness timeout')
        self.manifest['ready_seconds']=time.perf_counter()-begin
        self.manifest['status']='ready'
        raw=p.shell(['docker','logs',self.container],timeout=30,check=False)
        startup_text=raw['stdout']+raw['stderr']
        storage=[]
        for line in startup_text.splitlines():
            if 'P2_STORAGE_JSON ' in line:
                storage.append(json.loads(line.split('P2_STORAGE_JSON ',1)[1]))
        p.save(self.folder/'startup-storage-ledger.json',storage)
        weights=[r for r in storage if r['kind']=='weights']
        if not weights:
            raise RuntimeError('Startup resident-parameter instrumentation did not run; no formal comparison allowed')
        if any(r['parameters']['unique_storage_bytes_by_device'].get('cpu',0) for r in weights):
            raise RuntimeError('CPU-resident model parameters remain despite offload=0')
        self.manifest['cpu_residency_gate']='passed: zero CPU parameter storage bytes'
        pool_kind='runner_caches' if self.service=='vllm' else 'pool'
        pools=[r for r in storage if r['kind']==pool_kind]
        if not pools:
            raise RuntimeError('Actual cache storage bytes unavailable; resource gate failed')
        pool_bytes=sum(sum(v for k,v in r['pool']['unique_storage_bytes_by_device'].items() if k.startswith('cuda')) for r in pools)
        residual=abs(pool_bytes-self.cfg['vllm_pool_bytes'])/self.cfg['vllm_pool_bytes']
        self.manifest['actual_cache_storage_bytes']=pool_bytes
        self.manifest['cache_budget_residual_fraction']=residual
        if residual>self.cfg.get('max_cache_budget_residual_fraction',0.02):
            raise RuntimeError('Measured cache pool residual exceeds frozen 2% tolerance: '+str(residual))
        self.manifest['resource_gate']='passed: measured cache storage within 2% of shared 1.5GiB target'
        p.save(self.folder/'manifest.json',self.manifest)
        probe=p.shell(['docker','exec','-i',self.container,'python3','-','--service',self.service],stdin=(B/'scripts/container_probe.py').read_bytes(),timeout=120)
        p.save(self.folder/'runtime.json',json.loads(probe['stdout'].strip().splitlines()[-1]))
        for route in ('/get_server_info','/get_model_info','/v1/models'):
            try:
                p.save(self.folder/(route.strip('/').replace('/','-')+'.json'),json.loads(p.get(self.base,route,timeout=10)))
            except (OSError,ValueError):
                pass
        p.save(self.folder/'clock.json',{'host_before_ns':time.time_ns(),
            'container':p.shell(['docker','exec',self.container,'python3','-c','import time;print(time.time_ns())']),
            'host_after_ns':time.time_ns()})
        self.collector.phase='warmup'
        data=json.loads((self.root/'dataset.json').read_text(encoding='utf-8'))
        warm=self.folder/'warmup'; warm.mkdir()
        # Warm each input shape separately. All warmups excluded from measurements.
        for index,inp in enumerate((128,512,2048,8192)):
            r=request(self.base,data[str(inp)]|{'output_tokens':8},index,warm)
            if r['status']!='success':
                raise RuntimeError('Warmup failed '+str(r))
        self.collector.phase='warm_idle'
        time.sleep(10)
        log(f"READY {self.folder.name} {self.manifest['ready_seconds']:.1f}s")

    def close(self, error=None):
        self.collector.phase='stopping'
        try:
            raw=p.shell(['docker','logs','--timestamps','--since',self.since,self.container],timeout=60,check=False)
            text=p.clean(raw['stdout']+raw['stderr'])
            (self.folder/'server.log').write_text(text,encoding='utf-8')
            storage=[]
            for line in text.splitlines():
                if 'P2_STORAGE_JSON ' in line:
                    try:
                        storage.append(json.loads(line.split('P2_STORAGE_JSON ',1)[1]))
                    except ValueError:
                        pass
            p.save(self.folder/'storage-ledger.json',storage)
            lines=[s for s in text.splitlines() if re.search(r'allocated|GiB|GB|cache size|cache group|num_gpu_blocks|state.*size|offload|Marlin',s,re.I) and 'server_args=' not in s]
            (self.folder/'resource-evidence.txt').write_text('\n'.join(lines),encoding='utf-8')
            p.shell(p.COMPOSE+['stop',self.service],timeout=90)
        finally:
            self.collector.finish()
        self.manifest.update({'status':'failed' if error else 'completed','error':error,'completed_at_utc':p.utc(),
            'env_unchanged':sha(p.ROOT/'.env')==self.cfg['production_env_sha256'],
            'compose_unchanged':sha(p.ROOT/'compose.yaml')==self.cfg['production_compose_sha256']})
        p.save(self.folder/'manifest.json',self.manifest)


def run_session(root,service,cfg,tag,callback):
    s=Session(root,service,cfg,tag)
    error=None
    try:
        s.start()
        callback(s)
    except Exception as exc:
        error=p.clean(str(exc))
        p.save(s.folder/'failure.json',{'error':error,'traceback':traceback.format_exc()})
        log('FAILED '+s.folder.name+' '+error)
    finally:
        s.close(error)
    return s,error


def all_results(root):
    rows=[]
    for path in sorted((root/'sessions').glob('*/*/summary.json')):
        row=json.loads(path.read_text(encoding='utf-8'))
        row['service']=path.parents[1].name.split('-')[1]
        row['path']=str(path.relative_to(p.ROOT))
        rows.append(row)
    return rows


def run(root,calibrate=False,services=('vllm','sglang')):
    cfg=json.loads((root/'config.json').read_text(encoding='utf-8'))
    data=json.loads((root/'dataset.json').read_text(encoding='utf-8'))
    if sha(p.ROOT/'.env')!=cfg['production_env_sha256'] or sha(p.ROOT/'compose.yaml')!=cfg['production_compose_sha256']:
        raise RuntimeError('Production config changed since freeze')
    for repeat in range(1,2 if calibrate else 4):
        order=('vllm','sglang') if repeat%2 else ('sglang','vllm')
        for service in order:
            if service not in services:
                continue
            existing=all_results(root)
            workloads=['128-128','512-256','2048-256']
            if calibrate:
                todo=[('calibrate','128-128',1)]
            else:
                todo=[]
                for w in workloads:
                    for c in cfg['client_concurrency']:
                        matches=[r for r in existing if r['service']==service and r['repeat']==repeat and r['workload']==w and r['concurrency']==c]
                        if not any(r['phase']=='screen' for r in matches):
                            todo.append(('screen',w,c))
            if not todo:
                continue
            def callback(s):
                blocked_workloads=set()
                for phase,w,c in todo:
                    if (root/'STOP_AFTER_CELL').exists():
                        raise RuntimeError('Stop requested at cell boundary')
                    if w in blocked_workloads:
                        p.append(root/'not-run.jsonl', {'utc':p.utc(),'service':service,'phase':phase,
                            'workload':w,'concurrency':c,'repeat':repeat,'reason':'lower concurrency failed; no further increase'})
                        continue
                    r=cell(root,s.folder,s.base,s.collector,cfg,data,phase,w,c,repeat)
                    summarize(root)
                    if r['failed']:
                        blocked_workloads.add(w)
                        p.get(s.base,'/health',timeout=10)
            _,error=run_session(root,service,cfg,f'calibration' if calibrate else f'screen-r{repeat:02}',callback)
            summarize(root)
            if error:
                return False
    if calibrate:
        return True
    # Formal results use independently restarted/warmed engine processes each repeat.
    for repeat in range(1,4):
        order=('sglang','vllm') if repeat%2 else ('vllm','sglang')
        for service in order:
            if service not in services:
                continue
            existing=all_results(root)
            todo=[]
            for w in ('128-128','512-256','2048-256'):
                for c in cfg['client_concurrency']:
                    screens=[r for r in existing if r['service']==service and r['phase']=='screen' and r['workload']==w and r['concurrency']==c]
                    if len(screens)!=3 or not all(r['sample_gate'] and r['failed']==0 for r in screens):
                        continue
                    if any(r['service']==service and r['phase']=='formal' and r['repeat']==repeat and r['workload']==w and r['concurrency']==c for r in existing):
                        continue
                    todo.append((w,c))
            if not todo:
                continue
            def formal(s):
                blocked_workloads=set()
                for w,c in todo:
                    if (root/'STOP_AFTER_CELL').exists():
                        raise RuntimeError('Stop requested at cell boundary')
                    if w in blocked_workloads:
                        continue
                    r=cell(root,s.folder,s.base,s.collector,cfg,data,'formal',w,c,repeat)
                    summarize(root)
                    if r['failed']:
                        blocked_workloads.add(w)
                        p.get(s.base,'/health',timeout=10)
            _,error=run_session(root,service,cfg,f'formal-r{repeat:02}',formal)
            summarize(root)
            if error:
                return False
    return True


def execute(root, services):
    """Run with an exclusive lock, durable status, reports and guaranteed restoration."""
    import os
    lock=root/'RUNNING.lock'
    handle=lock.open('x',encoding='utf-8')
    handle.write(json.dumps({'pid':os.getpid(),'started_at_utc':p.utc()}));handle.close()
    state={'pid':os.getpid(),'started_at_utc':p.utc(),'status':'running','stage':'screen_then_formal'}
    state['sleep_prevention']=bool(p.ctypes.windll.kernel32.SetThreadExecutionState(0x80000001))
    p.save(root/'execution.json',state)
    okay=False
    try:
        okay=run(root,False,services)
        formal=[r for r in all_results(root) if r['phase']=='formal']
        complete=len(formal)==90 and all(r['sample_gate'] and r['duration_gate'] and not r['failed'] for r in formal)
        state['status']='completed_primary_matrix' if okay and complete else 'incomplete_matrix' if okay else 'stopped_on_request' if (root/'STOP_AFTER_CELL').exists() else 'stopped_on_gate_failure'
    except BaseException as exc:
        state['status']='failed'
        state['error']=p.clean(str(exc))
        p.save(root/'execution-error.json',{'error':str(exc),'traceback':traceback.format_exc()})
        raise
    finally:
        try:
            summarize(root)
            import report_p2
            report_p2.report(root)
            validation=json.loads((root/'validation.json').read_text(encoding='utf-8'))
            if state['status']=='completed_primary_matrix' and not validation['cell_checks_pass']:
                state['status']='completed_with_validation_failures'
        except Exception as exc:
            state['report_error']=p.clean(str(exc))
        try:
            restore(root)
        except Exception as exc:
            state['restoration_error']=p.clean(str(exc))
        state['completed_at_utc']=p.utc()
        p.ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        p.save(root/'execution.json',state)
        lock.unlink(missing_ok=True)
    return okay


def summarize(root):
    cfg=json.loads((root/'config.json').read_text(encoding='utf-8'))
    rows=all_results(root)
    write_csv(root/'cell-results.csv',rows)
    groups=[]
    for service in ('vllm','sglang'):
        for phase in ('screen','formal'):
            for workload in WORKLOADS:
                for c in cfg['client_concurrency']:
                    selected=[r for r in rows if (r['service'],r['phase'],r['workload'],r['concurrency'])==(service,phase,workload,c)]
                    if not selected:
                        continue
                    raw=[]
                    for r in selected:
                        folder=p.ROOT/Path(r['path']).parent
                        raw.extend(json.loads(path.read_text(encoding='utf-8')) for path in folder.glob('request-*.json'))
                    result=stats(raw,sum(r['window_seconds'] for r in selected),cfg)
                    throughputs=[r['output_tokens_per_s'] for r in selected]
                    result.update({'service':service,'phase':phase,'workload':workload,'concurrency':c,
                        'repeats':len(selected),'min_repeat_success':min(r['success'] for r in selected),
                        'min_repeat_seconds':min(r['window_seconds'] for r in selected),
                        'throughput_mean':statistics.mean(throughputs),
                        'throughput_sd':statistics.stdev(throughputs) if len(throughputs)>1 else None,
                        'all_repeat_slo_pass':all(r['slo_pass'] for r in selected),
                        'formal_gate':phase=='formal' and len(selected)==3 and all(r['sample_gate'] and r['duration_gate'] for r in selected)})
                    groups.append(result)
    write_csv(root/'aggregate.csv',groups)
    p.save(root/'summary.json',{'updated_at_utc':p.utc(),'cells':len(rows),'groups':groups,
        'percentiles':'linear interpolated quantiles from pooled raw request observations, not averaged percentiles',
        'capacity':'largest tested closed-loop concurrency satisfying frozen SLO in all 3 formal repeats; not open-loop capacity'})
    log('SUMMARY cells='+str(len(rows)))


def restore(root):
    cfg=json.loads((root/'config.json').read_text(encoding='utf-8'))
    result=p.shell(p.COMPOSE+['create','--no-build','--pull','never','--force-recreate','vllm','sglang'],timeout=120,check=False)
    p.save(root/'restoration.json',{'utc':p.utc(),'result':result,
        'env_unchanged':sha(p.ROOT/'.env')==cfg['production_env_sha256'],
        'compose_unchanged':sha(p.ROOT/'compose.yaml')==cfg['production_compose_sha256'],
        'containers':{s:p.inspect('llm-deployment-'+s+'-1')['State'] for s in ('vllm','sglang')}})


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action',choices=['prepare','calibrate','run','summarize','restore'])
    ap.add_argument('--root',required=True,type=Path)
    ap.add_argument('--pool-gib',type=float,default=1.5)
    ap.add_argument('--sg-states',type=int,default=17)
    ap.add_argument('--sg-tokens',type=int,default=16384)
    ap.add_argument('--services',default='vllm,sglang')
    args=ap.parse_args()
    if args.action=='prepare':
        prepare(args.root,args)
    elif args.action in ('calibrate','run'):
        services=tuple(args.services.split(','))
        if not services or any(s not in ('vllm','sglang') for s in services):
            raise ValueError('Invalid engine selection')
        okay=run(args.root,True,services) if args.action=='calibrate' else execute(args.root,services)
        if not okay:
            raise SystemExit(1)
    elif args.action=='restore':
        restore(args.root)
    else:
        summarize(args.root)


if __name__=='__main__':
    main()
