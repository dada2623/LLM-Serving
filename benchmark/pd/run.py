"""Windows coordinator for checkpointed CUDA/ROCm PD measurements.

Requires the explicitly prepared carrier/tunnel. Does not edit production .env.
Stops only experiment workers. Refreshes measured tables/plots after every cell.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import math
import random
from pathlib import Path
import shlex
import subprocess
import sys
import time
import concurrent.futures as cf

ROOT=Path(__file__).resolve().parents[2]
RUN=ROOT/'benchmark/pd/runs/pd-run'
LIN='/workspace/benchmark/pd/runs/pd-run'
DOCKER=Path(os.environ['LOCALAPPDATA'])/'Programs/DockerDesktop/resources/bin/docker.exe'
SSH=Path(os.environ['WINDIR'])/'System32/OpenSSH/ssh.exe'
KEY=Path.home()/'.ssh/llm-serving-amd'
CONTAINER='llm-pd-benchmark'
MODEL='MODEL_PATH'
AMD_MODEL='MODEL_PATH'
AMD_MANAGE='/opt/llm-serving/setup/pd-benchmark-manage.py'
AMD_PID='/opt/llm-serving/experiments/pd-benchmark-pd-run/server.pid'
AMD_RUNTIME='/opt/llm-serving/setup/sglang-runtime-pd-run.sh'
AMD_LAUNCH='/opt/llm-serving/setup/pd-benchmark-launch.py'
PLOT=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
state={}
samplers=[]
active_logs={}

def process_exists(pid):
    if pid<=0: raise ValueError('Expected a positive process ID')
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.restype=wintypes.BOOL
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        kernel.CloseHandle.restype=wintypes.BOOL
        handle=kernel.OpenProcess(0x1000,False,pid)
        if not handle:
            error=ctypes.get_last_error()
            if error==87: return False
            raise ctypes.WinError(error)
        try:
            code=wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle,ctypes.byref(code)):
                raise ctypes.WinError(ctypes.get_last_error())
            return code.value==259
        finally:
            kernel.CloseHandle(handle)
    try: os.kill(pid,0)
    except ProcessLookupError: return False
    return True

def save_state(**updates):
    state.update(updates,pid=os.getpid(),updated_at=dt.datetime.now().astimezone().isoformat())
    temp=RUN/'execution.tmp.json'
    temp.write_text(json.dumps(state,indent=2))
    temp.replace(RUN/'execution.json')
    print(json.dumps(state),flush=True)

def call(argv, *, timeout=900, check=True):
    proc=subprocess.run([str(x) for x in argv],capture_output=True,timeout=timeout)
    text=proc.stdout.decode('utf-8',errors='replace').replace('\x00','')
    err=proc.stderr.decode('utf-8',errors='replace').replace('\x00','')
    if proc.returncode and check:
        raise RuntimeError(f'Command exited {proc.returncode}: {text[-2500:]} {err[-1500:]}')
    return text,err,proc.returncode

def nv(*args,**kwargs):
    return call([DOCKER,'exec',CONTAINER,*args],**kwargs)

def amd(*args,**kwargs):
    command=subprocess.list2cmdline(['wsl.exe','-d','Ubuntu-24.04','-u','root','--',*[str(x) for x in args]])
    return call([SSH,'-i',KEY,'-o','BatchMode=yes','-o','ConnectTimeout=15',
                 '-o','StrictHostKeyChecking=yes','user@example.invalid',command],**kwargs)

def stop_workers():
    nv('python3','/workspace/benchmark/pd/manage_worker.py','stop','--pidfile',LIN+'/nvidia.pid')
    amd('python3',AMD_MANAGE,'stop','--pidfile',AMD_PID)
    if active_logs.get('A'):
        output,error,rc=amd('cat',active_logs['A'],timeout=120,check=False)
        (RUN/'logs'/Path(active_logs['A']).name).write_text(output+'\n'+error,encoding='utf-8')
    active_logs.clear()

def health(host):
    code=("import urllib.request; o=urllib.request.build_opener(urllib.request.ProxyHandler({})); "
          f"print(o.open('http://{host}:18100/health',timeout=3).status)")
    return nv('python3','-c',code,timeout=10,check=False)[2]==0

def configure(mode,tag,checksum=False):
    instance=tag+'-'+str(time.time_ns())
    save_state(state='starting',mode=mode,current_cell=tag,instance=instance)
    stop_workers()
    roles={'N':{'N':'single'},'A':{'A':'single'},'REPLICAS':{'N':'single','A':'single'},
           'NP-AD':{'N':'prefill','A':'decode'},'AP-ND':{'A':'prefill','N':'decode'}}[mode]
    for side,role in roles.items():
        common=['--pool','32768','--concurrency','4','--slots','8','--role',role]
        if checksum: common+=['--checksum']
        if side=='N':
            nv('python3','/workspace/benchmark/pd/manage_worker.py','start','--pidfile',LIN+'/nvidia.pid',
               '--log',LIN+f'/logs/{instance}-N.log','--','python3','/workspace/benchmark/pd/launch_server.py',
               '--host','nvidia-pd-host','--model',MODEL,'--memory-fraction','0.94',*common)
        else:
            active_logs['A']=f'/opt/llm-serving/setup/pd-benchmark-{instance}-A.log'
            amd('python3',AMD_MANAGE,'start','--pidfile',AMD_PID,
                '--log',active_logs['A'],'--','bash',AMD_RUNTIME,
                AMD_LAUNCH,'--host','amd-pd-host','--model',AMD_MODEL,*common)
    deadline=time.monotonic()+900
    while time.monotonic()<deadline:
        if all(health('nvidia-pd-host' if side=='N' else 'amd-pd-host') for side in roles):
            break
        time.sleep(3)
    else:
        raise TimeoutError(f'{mode} startup exceeded 900 seconds')
    # Read actual runtime limits, not merely requested CLI values.
    info={}
    for side in roles:
        host='nvidia-pd-host' if side=='N' else 'amd-pd-host'
        code=("import urllib.request,json; o=urllib.request.build_opener(urllib.request.ProxyHandler({})); "
              f"print(o.open('http://{host}:18100/get_server_info').read().decode())")
        raw=nv('python3','-c',code)[0]
        info[side]=json.loads(raw)
        pools=[]
        def walk(v):
            if isinstance(v,dict):
                for k,x in v.items():
                    if k in ['max_total_num_tokens','max_total_tokens'] and isinstance(x,int): pools.append(x)
                    walk(x)
            elif isinstance(v,list):
                for x in v: walk(x)
        walk(info[side])
        if not pools or any(p!=32768 for p in pools):
            raise RuntimeError(f'{side} actual token pool differs from frozen 32768: {pools}')
    (RUN/f'logs/{instance}-server-info.json').write_text(json.dumps(info,indent=2))

def plot():
    call([PLOT,ROOT/'benchmark/pd/report.py',RUN],timeout=120)

def upload(local,remote):
    scp=SSH.with_name('scp.exe')
    call([scp,'-i',KEY,'-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
          local,'user@example.invalid:'+remote],timeout=120)

def start_resources():
    folder=RUN/'resources';folder.mkdir(exist_ok=True)
    epoch=dt.datetime.now().strftime('%Y%m%d-%H%M%S')
    upload(ROOT/'benchmark/pd/resource_sample.py','F:/llm-serving/setup/pd-resource-sample.py')
    upload(ROOT/'benchmark/pd/resource-windows.ps1','F:/llm-serving/setup/pd-resource-windows.ps1')
    linux_n=LIN+f'/resources/{epoch}-N-linux.jsonl'
    linux_a=f'/opt/llm-serving/setup/{epoch}-A-linux.jsonl'
    windows_a=f'F:/llm-serving/setup/{epoch}-A-windows.jsonl'
    windows_n=folder/f'{epoch}-N-windows.jsonl'
    for f in [folder/f'{epoch}-N-linux.jsonl',windows_n]:
        Path(str(f)+'.STOP').unlink(missing_ok=True)
    argv_n=[DOCKER,'exec',CONTAINER,'python3','/workspace/benchmark/pd/resource_sample.py',
            '--side','N','--output',linux_n]
    linux_cmd=subprocess.list2cmdline(['wsl.exe','-d','Ubuntu-24.04','-u','root','--','python3',
                 '/opt/llm-serving/setup/pd-resource-sample.py','--side','A','--output',linux_a])
    windows_cmd=subprocess.list2cmdline(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass',
                   '-File','F:/llm-serving/setup/pd-resource-windows.ps1','-OutputPath',windows_a])
    ssh=[SSH,'-i',KEY,'-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','user@example.invalid']
    commands=[argv_n,ssh+[linux_cmd],ssh+[windows_cmd],
              ['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',ROOT/'benchmark/pd/resource-windows.ps1',
               '-OutputPath',windows_n]]
    for command in commands:
        samplers.append(subprocess.Popen([str(x) for x in command],stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW))
    (folder/'samplers.json').write_text(json.dumps({'epoch':epoch,'pids':[p.pid for p in samplers],
        'linux_a':linux_a,'windows_a':windows_a,'windows_n':str(windows_n)},indent=2))

def stop_resources():
    if not samplers: return
    folder=RUN/'resources';data=json.loads((folder/'samplers.json').read_text())
    for f in [folder/f'{data["epoch"]}-N-linux.jsonl',Path(data['windows_n'])]:
        Path(str(f)+'.STOP').touch()
    amd('touch',data['linux_a']+'.STOP',data['windows_a'].replace('F:/','/mnt/f/')+'.STOP')
    for process in samplers:
        try: process.wait(timeout=20)
        except subprocess.TimeoutExpired: process.terminate()
    for source in [data['linux_a'],data['windows_a'].replace('F:/','/mnt/f/')]:
        text=amd('cat',source,timeout=120)[0]
        (folder/Path(source).name).write_text(text.lstrip('\ufeff'),encoding='utf-8')

def inventory():
    save_state(state='inventory',current_cell='model-hashes')
    target='/opt/llm-serving/setup/pd-benchmark-inventory.py'
    upload(ROOT/'benchmark/pd/inventory.py',target.replace('/mnt/f/','F:/'))
    raw=amd('bash',AMD_RUNTIME,target,AMD_MODEL,'--output',
            '/opt/llm-serving/setup/pd-model-inventory.json',timeout=600)[0]
    output=amd('cat','/opt/llm-serving/setup/pd-model-inventory.json')[0]
    (RUN/'model-amd.json').write_text(output)
    nv('python3','/workspace/benchmark/pd/inventory.py',MODEL,'--output',LIN+'/model-nvidia.json',timeout=600)
    a=json.loads(output);n=json.loads((RUN/'model-nvidia.json').read_text())
    ah={f['name']:f['sha256'] for f in a['files']}
    nh={f['name']:f['sha256'] for f in n['files']}
    equal=ah==nh
    (RUN/'model-comparison.json').write_text(json.dumps({'all_files_equal':equal,
        'nvidia_files':len(nh),'amd_files':len(ah),'differences':
        [k for k in sorted(set(ah)|set(nh)) if ah.get(k)!=nh.get(k)]},indent=2))
    if not equal: raise RuntimeError('Model/tokenizer files differ between workers')

def network():
    passed=RUN/'network-passed.json'
    if passed.exists():
        previous=json.loads(passed.read_text())
        if previous.get('passed') and previous.get('transfer_timeout_s')==180: return
    save_state(state='network',current_cell='gpu-buffer-transfer')
    stop_workers()
    # Keep failed calibration evidence when repeating after hardware recovery.
    old_files=['network-nvidia.jsonl','network-amd.jsonl','network-nvidia.rtt.json',
               'network-passed.json','logs/network-N.log','logs/network-A.log']
    existing=[RUN/name for name in old_files if (RUN/name).exists()]
    if existing:
        archive=RUN/'network-attempts'/str(time.time_ns())
        archive.mkdir(parents=True)
        for source in existing:
            source.rename(archive/source.name)
    remote_output=f'/opt/llm-serving/setup/pd-network-{time.time_ns()}.jsonl'
    upload(ROOT/'benchmark/pd/network_measure.py','F:/llm-serving/setup/pd-benchmark-network.py')
    with cf.ThreadPoolExecutor(max_workers=2) as pool:
        target=pool.submit(amd,'bash',AMD_RUNTIME,'/opt/llm-serving/setup/pd-benchmark-network.py',
            'target','--local','amd-pd-host','--output',remote_output,timeout=1800,check=False)
        time.sleep(8)
        initiator=nv('python3','/workspace/benchmark/pd/network_measure.py','initiator',
            '--local','nvidia-pd-host','--remote','amd-pd-host','--output',LIN+'/network-nvidia.jsonl',timeout=1800,check=False)
        receiver=target.result()
    (RUN/'logs/network-N.log').write_text(initiator[0]+'\n'+initiator[1])
    (RUN/'logs/network-A.log').write_text(receiver[0]+'\n'+receiver[1])
    raw=amd('cat',remote_output,check=False)[0]
    (RUN/'network-amd.jsonl').write_text(raw)
    rows=[json.loads(line) for file in ['network-nvidia.jsonl','network-amd.jsonl']
          if (RUN/file).exists() for line in (RUN/file).read_text().splitlines()]
    okay=len(rows)==100 and all(r['passed'] for r in rows) and initiator[2]==receiver[2]==0
    passed.write_text(json.dumps({'passed':okay,'operations':len(rows),'transfer_timeout_s':180,
        'sizes_mib':[1,16,64,128,512],'repeats_per_direction':10,
        'measurement_sha256':hashlib.sha256((ROOT/'benchmark/pd/network_measure.py').read_bytes()).hexdigest()},indent=2))
    if not okay: raise RuntimeError('GPU network calibration failed')

def cell(mode,workload,tag,*,concurrency=1,requests=10,duration=30,deadline=180,
         output_tokens=None,rate=None,drain_timeout=120,allow_deadline_errors=False,
         retries=0,continue_on_failure=False):
    destination=RUN/'cells'/tag
    settings={'mode':mode,'workload':workload,'concurrency':concurrency,'requests':requests,
              'duration':duration,'deadline':deadline,'output_tokens':output_tokens,'rate':rate,
              'drain_timeout':drain_timeout}
    fingerprint=hashlib.sha256(json.dumps({'settings':settings,
        'dataset':hashlib.sha256((RUN/'dataset.json').read_bytes()).hexdigest(),
        'measure':hashlib.sha256((ROOT/'benchmark/pd/measure.py').read_bytes()).hexdigest(),
        'launch':hashlib.sha256((ROOT/'benchmark/pd/launch_server.py').read_bytes()).hexdigest() },sort_keys=True).encode()).hexdigest()
    boundary=destination/'boundary.json'
    if boundary.exists() and continue_on_failure:
        return json.loads((destination/'summary.json').read_text())
    if (destination/'summary.json').exists() and (destination/'fingerprint').exists():
        if (destination/'fingerprint').read_text()!=fingerprint:
            raise RuntimeError(f'Changed cell cannot silently resume: {tag}')
        summary=json.loads((destination/'summary.json').read_text())
        try:
            check_cell_result(destination,summary,allow_deadline_errors)
            return summary
        except RuntimeError:
            if not (retries or continue_on_failure): raise

    attempts=max(1,retries+1)
    last_summary=None
    failures=[]
    for attempt in range(1,attempts+1):
        if destination.exists():
            suffix='-retry-'+str(attempt-1)+'-'+str(time.time_ns()) if attempt>1 else '-interrupted-'+str(time.time_ns())
            destination.rename(destination.with_name(tag+suffix))
        save_state(state='running',mode=mode,current_cell=tag,attempt=attempt,max_attempts=attempts)
        argv=['python3','/workspace/benchmark/pd/measure.py','--dataset',LIN+'/dataset.json',
              '--output',LIN+'/cells/'+tag,'--mode',mode,'--workload',workload,
              '--concurrency',str(concurrency),'--requests',str(requests),'--duration',str(duration),
              '--deadline',str(deadline),'--drain-timeout',str(drain_timeout)]
        if output_tokens: argv+=['--output-tokens',str(output_tokens)]
        if rate: argv+=['--rate',str(rate)]
        output,error,rc=nv(*argv,timeout=deadline+900,check=False)
        (destination/'command-output.txt').write_text(output+'\n'+error)
        if not (destination/'summary.json').exists():
            failures.append({'attempt':attempt,'rc':rc,'error':f'Cell did not finish: {tag}'})
            if attempt<attempts: continue
            raise RuntimeError(f'Cell did not finish: {tag}, rc={rc}')
        (destination/'fingerprint').write_text(fingerprint)
        last_summary=json.loads((destination/'summary.json').read_text())
        plot()
        try:
            check_cell_result(destination,last_summary,allow_deadline_errors)
            return last_summary
        except RuntimeError as exc:
            failures.append({'attempt':attempt,'error':str(exc),
                             'summary':last_summary})
            if attempt<attempts: continue
            if continue_on_failure:
                boundary.write_text(json.dumps({'tag':tag,'mode':mode,'workload':workload,
                    'attempts':attempts,'reason':'retry limit reached','failures':failures},indent=2))
                return last_summary
            raise
    raise RuntimeError(f'Cell failed unexpectedly: {tag}')

def check_cell_result(destination,summary,allow_deadline_errors):
    # Apply the same acceptance rules to fresh and resumed checkpoints.
    if summary['errors']:
        failures=[json.loads(f.read_text()) for f in destination.glob('*-result.json')]
        failures=[r for r in failures if not r['success']]
        deadline_only=bool(failures) and all('Drain deadline exceeded' in r.get('error','') for r in failures)
        if not (allow_deadline_errors and deadline_only):
            raise RuntimeError(f'Request failure; inspect {destination.name}')
    if (RUN/'STOP_AFTER_CELL').exists():
        raise InterruptedError('STOP_AFTER_CELL observed at completed-cell boundary')

def warm(mode,tag):
    # Three stable windows, max ten. Workload C preheats the long-input shape.
    times=[]
    # A restarted server must warm again even when older windows are on disk.
    tag=tag+'-'+str(time.time_ns())
    for i in range(10):
        s=cell(mode,'C',f'warmup-{tag}-{i}',requests=1,duration=0,deadline=600,output_tokens=32)
        times.append(s['elapsed_s'])
        if len(times)>=3 and max(times[-3:])/min(times[-3:])<1.10:
            return
    raise RuntimeError(f'Warmup unstable for {tag}: {times}')

def validate_all():
    for mode in ['N','A','NP-AD','AP-ND']:
        tag='validation-'+mode
        done=RUN/'preflight'/tag/'validation.json'
        quality_done=done.exists() and json.loads(done.read_text()).get('passed')
        def concurrency_passed(c):
            for prefix in ['validation-concurrency','validation-recheck']:
                f=RUN/f'cells/{prefix}-{mode}-{c}/summary.json'
                if f.exists():
                    s=json.loads(f.read_text())
                    if s['errors']==0 and s['successes']>=c and s['sample_complete']: return True
            return False
        concurrency_done=all(concurrency_passed(c) for c in [1,2,4])
        if quality_done and concurrency_done:
            continue
        configure(mode,tag,checksum=True)
        if not quality_done:
            destination=RUN/'preflight'/tag
            if destination.exists():
                destination.rename(destination.with_name(tag+'-interrupted-'+str(time.time_ns())))
            save_state(state='validating',mode=mode,current_cell=tag)
            text,error,rc=nv('python3','/workspace/benchmark/pd/validate.py','--model',MODEL,
                '--dataset',LIN+'/dataset.json','--mode',mode,'--output',LIN+'/preflight/'+tag,
                timeout=1800,check=False)
            (RUN/f'logs/{tag}-client.log').write_text(text+'\n'+error)
            if rc or not done.exists() or not json.loads(done.read_text()).get('passed'):
                raise RuntimeError(f'14K fixed-answer/state gate failed: {mode}')
        for c in [1,2,4]:
            if concurrency_passed(c): continue
            cell(mode,'C',f'validation-recheck-{mode}-{c}',concurrency=c,requests=c,
                 duration=0,deadline=600,output_tokens=32,drain_timeout=600)

def validate_isolation():
    for mode in ['N','A','NP-AD','AP-ND']:
        tag='isolation-'+mode
        destination=RUN/'preflight'/tag
        done=destination/'validation.json'
        quality_done=done.exists() and json.loads(done.read_text()).get('passed')
        full=RUN/f'cells/validation-full-{mode}/summary.json'
        full_done=full.exists() and (lambda s:s['errors']==0 and s['sample_complete'])(json.loads(full.read_text()))
        if quality_done and full_done: continue
        configure(mode,tag,checksum=True)
        if not quality_done:
            if destination.exists():
                destination.rename(destination.with_name(tag+'-interrupted-'+str(time.time_ns())))
            save_state(state='validating',mode=mode,current_cell=tag)
            text,error,rc=nv('python3','/workspace/benchmark/pd/validate_isolation.py','--model',MODEL,
                '--dataset',LIN+'/dataset.json','--mode',mode,'--output',LIN+'/preflight/'+tag,
                timeout=1800,check=False)
            (RUN/f'logs/{tag}-client.log').write_text(text+'\n'+error)
            if rc or not done.exists() or not json.loads(done.read_text()).get('passed'):
                raise RuntimeError(f'Concurrent distinct-answer gate failed: {mode}')
        if not full_done:
            cell(mode,'C',f'validation-full-{mode}',requests=1,duration=0,deadline=900,drain_timeout=600)

def disable_trace():
    # Restore only the exact diagnostic conn.py backups, leaving HIP/norm fixes.
    code=("from pathlib import Path; import hashlib; "
          "p=Path('/sgl-workspace/sglang/python/sglang/srt/disaggregation/mooncake/conn.py'); "
          "b=p.with_suffix('.original.py'); assert b.exists(); p.write_bytes(b.read_bytes()); "
          "print(hashlib.sha256(p.read_bytes()).hexdigest())")
    expected=nv('python3','-c',code)[0].strip()
    script='/opt/llm-serving/experiments/sglang-pd-pd-run/source/sglang/srt/disaggregation/mooncake/conn.py'
    # AMD was not traced originally. Accept a missing backup only when its
    # current source exactly matches the verified NVIDIA pristine baseline.
    code=(f"from pathlib import Path; import hashlib; p=Path('{script}'); "
          "b=p.with_suffix('.original.py'); data=b.read_bytes() if b.exists() else p.read_bytes(); "
          f"assert hashlib.sha256(data).hexdigest()=='{expected}', 'Unexpected AMD conn.py baseline'; "
          "p.write_bytes(data); print(hashlib.sha256(p.read_bytes()).hexdigest())")
    actual=amd('python3','-c',code)[0].strip()
    assert actual==expected
    (RUN/'performance-transport-source.json').write_text(json.dumps({
        'nvidia_conn_sha256':expected,'amd_conn_sha256':actual,
        'byte_count_trace_enabled':False,'checksum_cli_enabled':False},indent=2))

def screening():
    for mode in ['N','A','NP-AD','AP-ND']:
        tag='screen-'+mode
        checkpoints=[RUN/f'cells/screen-{mode}-{workload}-c{c}'
                     for workload in ['S','L','C'] for c in [1,2,4]]
        complete=all((d/'fingerprint').exists() and (d/'summary.json').exists()
                     and json.loads((d/'summary.json').read_text()).get('sample_complete')
                     for d in checkpoints)
        # Cached cell() calls below still verify fingerprints and acceptance.
        # A fully measured mode needs no new servers or warmup on resume.
        if not complete:
            configure(mode,tag)
            warm(mode,tag)
        for workload in ['S','L','C']:
            for c in [1,2,4]:
                cell(mode,workload,f'screen-{mode}-{workload}-c{c}',concurrency=c,
                     requests=10,duration=30,deadline={'S':900,'L':1200,'C':2400}[workload],
                     allow_deadline_errors=True)

def mixed_calibration():
    """Freeze shared SLOs and absolute rates from separate NVIDIA data."""
    frozen=RUN/'calibration.json'
    if frozen.exists():
        return json.loads(frozen.read_text())
    configure('N','calibration-N')
    warm('N','calibration-N')
    slo={}
    mean={}
    for shape in ['S','L','C','M']:
        s=cell('N',shape,f'calibration-N-{shape}',requests=10,duration=30,
               deadline={'S':900,'L':1200,'C':2400,'M':600}[shape])
        rs=[json.loads(f.read_text()) for f in (RUN/f'cells/calibration-N-{shape}').glob('*-result.json')]
        rs=[r for r in rs if r['success']]
        def quantile(xs,q):
            xs=sorted(xs);at=(len(xs)-1)*q;lo=int(at);hi=min(lo+1,len(xs)-1)
            return xs[lo]+(xs[hi]-xs[lo])*(at-lo)
        slo[shape]={'ttft_s':max(1,2*quantile([r['ttft_s'] for r in rs],.95)),
                    'tpot_s':1.5*quantile([r['tpot_s'] for r in rs],.95)}
        mean[shape]=sum(r['e2e_s'] for r in rs)/len(rs)
    data={'slo':slo,'trace_rates':{},'calibration_means_s':mean,
          'definition':'Synthetic SLO, frozen across deployments; rates calibrated separately per trace.'}
    for workload,average in [('M-code',.8*mean['C']+.2*mean['M']),
                              ('M-interference',.8*mean['S']+.2*mean['C'])]:
        # Start conservatively; use independent offered-load windows to find a sustainable level.
        sustainable=None
        trials=[]
        for level in [.5, .8, 1., 1.2, 1.6, 2.]:
            rate=level/average
            s=cell('N',workload,f'calibration-{workload}-{level}',requests=20,duration=120,
                   deadline=2400,rate=rate,allow_deadline_errors=True)
            rs=[json.loads(f.read_text()) for f in (RUN/f'cells/calibration-{workload}-{level}').glob('*-result.json')]
            passing=[]
            for r in rs:
                limits=slo[r['workload']]
                passing.append(r['success'] and r.get('ttft_s') is not None and r.get('tpot_s') is not None and r['ttft_s']<=limits['ttft_s']
                    and r['tpot_s']<=limits['tpot_s']
                    and r['e2e_s']<=limits['ttft_s']+(r['completion_tokens']-1)*limits['tpot_s'])
            # Excess post-arrival drain or growing TTFT rejects a rate, even when all requests eventually succeed.
            ordered=sorted(rs,key=lambda r:r['scheduled_s'])
            first=ordered[:max(1,len(ordered)//2)]
            last=ordered[len(ordered)//2:]
            qfirst=sum(r['ttft_s'] or r['e2e_s'] for r in first)/len(first)
            qlast=sum(r['ttft_s'] or r['e2e_s'] for r in last)/len(last)
            queue_growth=qlast>max(qfirst*1.25,qfirst+1)
            ok=s['sample_complete'] and sum(passing)/len(passing)>=.95 and not queue_growth
            trials.append({'rate':rate,'passed':ok,'queue_growth':queue_growth,
                           'slo_fraction':sum(passing)/len(passing)})
            (RUN/'calibration-progress.json').write_text(json.dumps({'data':data,
                'current_trace':workload,'current_trials':trials},indent=2))
            if ok: sustainable=rate
            elif sustainable is not None: break
        if sustainable is None:
            raise RuntimeError(f'No sustainable arrival rate calibrated for {workload}; preserve trials and inspect')
        data['trace_rates'][workload]={'lambda_N':sustainable,'trials':trials}
    frozen.write_text(json.dumps(data,indent=2))
    return data

def mixed_screen(calibration):
    for mode in ['N','A','NP-AD','AP-ND','REPLICAS']:
        configure(mode,'mixed-screen-'+mode)
        warm(mode,'mixed-screen-'+mode)
        for workload in ['M-code','M-interference']:
            baseline=calibration['trace_rates'][workload]['lambda_N']
            for factor in [.5,.8,1.,1.2]:
                cell(mode,workload,f'screen-{mode}-{workload}-r{factor}',requests=10,
                     duration=60,deadline=2400,rate=factor*baseline,allow_deadline_errors=True,
                     retries=3,continue_on_failure=True)

def formal(calibration):
    common={}
    for workload in ['L','C']:
        valid=[]
        for c in [1,2,4]:
            ss=[RUN/f'cells/screen-{mode}-{workload}-c{c}/summary.json'
                for mode in ['N','A','NP-AD','AP-ND']]
            if all(f.exists() and (lambda s:s['errors']==0 and s['sample_complete'])(json.loads(f.read_text())) for f in ss):
                valid.append(c)
        if not valid: raise RuntimeError(f'No completed common screening concurrency for {workload}')
        common[workload]=sorted(set([1,max(valid)]))
    (RUN/'formal-matrix.json').write_text(json.dumps({'concurrencies':common,'repetitions':3,
            'samples':50,'minimum_duration_s':120,'deadline_s':{'L':7200,'C':14400,'M-code':14400}},indent=2))
    orders=[['N','A','NP-AD','AP-ND','REPLICAS'],
            ['AP-ND','REPLICAS','A','NP-AD','N'],
            ['A','N','REPLICAS','AP-ND','NP-AD']]
    for rep,order in enumerate(orders,1):
        for mode in order:
            configure(mode,f'formal-r{rep}-{mode}')
            warm(mode,f'formal-r{rep}-{mode}')
            if mode!='REPLICAS':
                for workload in ['L','C']:
                    for c in common[workload]:
                        s=cell(mode,workload,f'formal-r{rep}-{mode}-{workload}-c{c}',concurrency=c,
                               requests=50,duration=120,deadline={'L':7200,'C':14400}[workload],
                               allow_deadline_errors=True)
                        if not s['sample_complete']: raise RuntimeError('Formal cell incomplete; budget not extended automatically')
            baseline=calibration['trace_rates']['M-code']['lambda_N']
            for factor in [.5,1.]:
                s=cell(mode,'M-code',f'formal-r{rep}-{mode}-M-code-r{factor}',requests=50,
                       duration=120,deadline=14400,rate=factor*baseline,allow_deadline_errors=True)
                if not s['sample_complete']: raise RuntimeError('Formal mixed cell incomplete')

def sessions():
    for mode in ['N','A','NP-AD','AP-ND','REPLICAS']:
        destination=RUN/'sessions'/mode
        done=destination/'session-summary.json'
        if done.exists() and json.loads(done.read_text()).get('passed'): continue
        configure(mode,'session-'+mode)
        warm(mode,'session-'+mode)
        if destination.exists():
            destination.rename(destination.with_name(mode+'-interrupted-'+str(time.time_ns())))
        save_state(state='running',mode=mode,current_cell='session-'+mode)
        text,error,rc=nv('python3','/workspace/benchmark/pd/session_measure.py',
            '--model',MODEL,'--dataset',LIN+'/dataset.json','--mode',mode,
            '--output',LIN+'/sessions/'+mode,'--trace',LIN+'/session-trace.json',timeout=14400,check=False)
        (RUN/f'logs/session-{mode}-client.log').write_text(text+'\n'+error)
        if rc or not done.exists() or not json.loads(done.read_text()).get('passed'):
            raise RuntimeError(f'Cold session replay failed: {mode}')
        plot()

def interference():
    for mode in ['N','A','NP-AD','AP-ND','REPLICAS']:
        destination=RUN/'interference'/mode
        done=destination/'interference-summary.json'
        if done.exists() and json.loads(done.read_text()).get('passed'): continue
        configure(mode,'interference-'+mode)
        warm(mode,'interference-'+mode)
        if destination.exists():
            destination.rename(destination.with_name(mode+'-interrupted-'+str(time.time_ns())))
        save_state(state='running',mode=mode,current_cell='interference-'+mode)
        text,error,rc=nv('python3','/workspace/benchmark/pd/interference_measure.py',
            '--dataset',LIN+'/dataset.json','--mode',mode,'--output',LIN+'/interference/'+mode,
            timeout=14400,check=False)
        (RUN/f'logs/interference-{mode}-client.log').write_text(text+'\n'+error)
        if rc or not done.exists() or not json.loads(done.read_text()).get('passed'):
            raise RuntimeError(f'Interference timeline failed: {mode}')
        plot()

def verify_production():
    before=json.loads((RUN/'production-hashes-before.json').read_text(encoding='utf-8-sig'))
    checks=[{'path':r['Path'],'expected_sha256':r['Hash'],
             'actual_sha256':hashlib.sha256(Path(r['Path']).read_bytes()).hexdigest().upper()}
            for r in before]
    passed=all(r['expected_sha256']==r['actual_sha256'] for r in checks)
    (RUN/'production-integrity.json').write_text(json.dumps({'passed':passed,'files':checks},indent=2))
    if not passed: raise RuntimeError('Production file hashes changed during benchmark')

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--stage',choices=['validate','network','screen','all'],default='validate')
    a=p.parse_args()
    RUN.mkdir(parents=True,exist_ok=True)
    (RUN/'logs').mkdir(exist_ok=True)
    lock=RUN/'RUNNING.lock'
    if lock.exists():
        old=json.loads(lock.read_text())
        # A stale lock is preserved for audit; never assume a live PID is stale.
        if process_exists(old['pid']):
            raise RuntimeError(f'Existing runner PID {old["pid"]}')
        lock.rename(RUN/f'RUNNING-stale-{time.time_ns()}.json')
    lock.write_text(json.dumps({'pid':os.getpid(),'stage':a.stage,'started':dt.datetime.now().astimezone().isoformat()}))
    code_manifest={str(f.relative_to(ROOT)):hashlib.sha256(f.read_bytes()).hexdigest()
                   for f in (ROOT/'benchmark/pd').glob('*.py')}
    (RUN/f'code-{os.getpid()}.json').write_text(json.dumps(code_manifest,indent=2))
    save_state(state='started',stage=a.stage)
    try:
        start_resources()
        if a.stage in ['network','all']:
            inventory()
            network()
        if a.stage=='network':
            save_state(state='stage_complete')
            return
        validate_all()
        if a.stage in ['screen','all']:
            validate_isolation()
        if a.stage in ['screen','all']:
            disable_trace()
            screening()
        if a.stage=='all':
            calibration=mixed_calibration()
            mixed_screen(calibration)
            interference()
            sessions()
            formal(calibration)
        save_state(state='stage_complete')
    except Exception as e:
        save_state(state='stopped',error=repr(e))
        raise
    finally:
        try:
            stop_workers()
            save_state(workers_stopped=True)
        finally:
            try:
                try: stop_resources()
                finally: verify_production()
            finally:
                lock.unlink(missing_ok=True)
                plot()

if __name__=='__main__':
    main()
