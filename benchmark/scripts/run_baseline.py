"""P3 baseline supplement; frozen Control settings, independent restart checkpoints."""
import argparse
import json
import os
from pathlib import Path
import shutil
import time
import traceback

import run_p2 as p2

p = p2.p
SOURCE = p.BENCH / 'p2/local'
PHASES = ('no_service_60s', 'warm_idle_60s', 'post_request_60s', 'post_stop_60s')


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def states():
    return {s: p.inspect('llm-deployment-' + s + '-1')['State'] for s in ('vllm', 'sglang')}


def prepare(root):
    cfg = load(SOURCE / 'config.json')
    for name, field in (('.env', 'production_env_sha256'), ('compose.yaml', 'production_compose_sha256')):
        if p2.sha(p.ROOT / name) != cfg[field]:
            raise RuntimeError('Production file changed since Control freeze: ' + name)
    if any(v['Running'] for v in states().values()):
        raise RuntimeError('A model service is running; baseline expects both stopped')
    root.mkdir(parents=True, exist_ok=False)
    cfg.update(stage='P3-baseline', created_at_utc=p.utc(),
               baseline_seconds=60, baseline_repeats=3, baseline_input=512, baseline_output=256,
               source_control=str(SOURCE.relative_to(p.ROOT)),
               source_control_config_sha256=p2.sha(SOURCE / 'config.json'),
               baseline_scope='startup, no-service, warm idle, post-request and post-stop; no offload ablation',
               sampling_gate='>=50 host/GPU samples, >=55s span, <=5s maximum gap per 60s window')
    p.save(root / 'config.json', cfg)
    shutil.copy2(SOURCE / 'dataset.json', root / 'dataset.json')
    if p2.sha(root / 'dataset.json') != cfg['dataset_sha256']:
        raise RuntimeError('Frozen dataset hash mismatch')
    p.save(root / 'initial-state.json', {'utc': p.utc(), 'containers': states(),
           'gpu': p.shell(['nvidia-smi']), 'docker_version': p.shell(['docker', 'version'])})
    sources = root / 'sources'; sources.mkdir()
    for name in ('run_baseline.py', 'report_baseline.py', 'run_p2.py', 'run_p0.py',
                 'p2_startup_probe.py', 'container_probe.py', 'report_p2.py'):
        shutil.copy2(Path(__file__).parent / name, sources / name)
    p.save(root / 'sources.json', {x.name: p2.sha(x) for x in sources.iterdir()})
    p.save(root / 'images.json', {s: p.shell(['docker', 'image', 'inspect',
           'llm-deployment/' + s + ':local', '--format', '{{.Id}}'])['stdout'].strip()
           for s in ('vllm', 'sglang')})


def progress(root, session, repeat, phase):
    state = {'utc': p.utc(), 'pid': os.getpid(), 'session': session.folder.name,
             'service': session.service, 'repeat': repeat, 'phase': phase}
    p.save(root / 'progress.json', state)
    p.append(root / 'events.jsonl', state)
    p2.log(f"PHASE {session.service} r{repeat} {phase}")


def observe(root, session, repeat, phase):
    progress(root, session, repeat, phase)
    session.collector.phase = phase
    start = time.perf_counter_ns()
    before = p.utc()
    deadline = time.perf_counter() + session.cfg['baseline_seconds']
    while time.perf_counter() < deadline:
        time.sleep(min(1, max(0, deadline - time.perf_counter())))
    window = {'phase': phase, 'begin_utc': before, 'end_utc': p.utc(),
              'start_ns': start, 'end_ns': time.perf_counter_ns()}
    window['seconds'] = (window['end_ns'] - start) / 1e9
    p.append(session.folder / 'windows.jsonl', window)
    return window


def counter_keys(service):
    return (['vllm:request_success_total', 'vllm:prompt_tokens_total', 'vllm:generation_tokens_total']
            if service == 'vllm' else
            ['sglang:num_requests_total', 'sglang:prompt_tokens_total', 'sglang:generation_tokens_total'])


def run_one(root, cfg, service, repeat):
    s = p2.Session(root, service, cfg, f'baseline-r{repeat:02}')
    for name in ('run_baseline.py', 'report_baseline.py'):
        shutil.copy2(Path(__file__).parent / name, s.folder / 'sources' / name)
    error = None
    windows = []
    result = None
    try:
        p.shell(p.COMPOSE + ['stop', 'vllm', 'sglang'], timeout=90)
        if any(v['Running'] for v in states().values()):
            raise RuntimeError('No-service observation requires both containers stopped')
        s.collector.phase = PHASES[0]
        s.collector.launch()
        # Session.start normally launches the same collector; reuse our pre-start sampler.
        s.collector.launch = lambda: None
        windows.append(observe(root, s, repeat, PHASES[0]))
        progress(root, s, repeat, 'startup_and_excluded_warmup')
        s.start()
        idle_before = p.get(s.base, '/metrics', timeout=10)
        (s.folder / 'idle-before.prom').write_text(p.clean(idle_before), encoding='utf-8')
        windows.append(observe(root, s, repeat, PHASES[1]))
        before = p.get(s.base, '/metrics', timeout=10)
        (s.folder / 'request-before.prom').write_text(p.clean(before), encoding='utf-8')
        progress(root, s, repeat, 'request_512_256')
        s.collector.phase = 'request_512_256'
        folder = s.folder / 'measured-request'; folder.mkdir()
        case = load(root / 'dataset.json')['512'] | {'output_tokens': 256}
        request = p2.request(s.base, case, 0, folder)
        if request['status'] != 'success':
            raise RuntimeError('Measured request failed: ' + str(request))
        keys = counter_keys(service)
        start_values = p2.prometheus_values(before)
        idle_values = p2.prometheus_values(idle_before)
        for _ in range(30):
            after = p.get(s.base, '/metrics', timeout=10)
            end_values = p2.prometheus_values(after)
            deltas = {k: end_values.get(k, 0) - start_values.get(k, 0) for k in keys}
            if all(k in end_values and abs(deltas[k]-n)<0.1 for k,n in zip(keys,(1,512,256))):
                break
            time.sleep(1)
        (s.folder / 'request-after.prom').write_text(p.clean(after), encoding='utf-8')
        native = {'deltas': deltas, 'expected': dict(zip(keys,(1,512,256))),
                  'matches': all(k in end_values and abs(deltas[k]-n)<.1 for k,n in zip(keys,(1,512,256))),
                  'warm_idle_counters_unchanged': all(start_values.get(k,0)==idle_values.get(k,0) for k in keys)}
        p.save(s.folder / 'native-counts.json', native)
        windows.append(observe(root, s, repeat, PHASES[2]))
        final_values = p2.prometheus_values(p.get(s.base, '/metrics', timeout=10))
        native['post_request_counters_unchanged'] = all(final_values.get(k,0)==end_values.get(k,0) for k in keys)
        p.save(s.folder / 'native-counts.json', native)
        progress(root, s, repeat, 'stopping')
        s.collector.phase = 'stopping'
        start = time.perf_counter_ns()
        stopped = p.shell(p.COMPOSE + ['stop', service], timeout=90)
        stop_seconds = (time.perf_counter_ns()-start)/1e9
        stop_states = states()
        p.save(s.folder / 'stop.json', {'utc': p.utc(), 'seconds': stop_seconds,
               'result': stopped, 'containers': stop_states})
        if any(v['Running'] for v in stop_states.values()):
            raise RuntimeError('Service still running after stop')
        windows.append(observe(root, s, repeat, PHASES[3]))
        result = {'service': service, 'repeat': repeat, 'session': s.folder.name,
                  'windows': windows, 'request': request, 'native': native,
                  'ready_seconds': s.manifest['ready_seconds'], 'stop_seconds': stop_seconds}
    except BaseException as exc:
        error = p.clean(str(exc))
        p.save(s.folder / 'failure.json', {'error': error, 'traceback': traceback.format_exc()})
        raise
    finally:
        s.close(error)
    import report_baseline
    result.update(report_baseline.analyze_session(s.folder, result))
    p.save(s.folder / 'baseline-summary.json', result)
    if not result['checks_pass']:
        raise RuntimeError('Baseline gates failed: ' + str(result['checks']))
    p2.log(f'DONE {service} r{repeat}: all baseline gates passed')


def execute(root):
    cfg = load(root / 'config.json')
    for filename, field in (('.env', 'production_env_sha256'), ('compose.yaml', 'production_compose_sha256')):
        if p2.sha(p.ROOT / filename) != cfg[field]:
            raise RuntimeError('Production config changed: ' + filename)
    if p2.sha(root / 'dataset.json') != cfg['dataset_sha256']:
        raise RuntimeError('Dataset changed')
    for name, expected in load(root / 'sources.json').items():
        if p2.sha(Path(__file__).parent / name) != expected:
            raise RuntimeError('Frozen source changed before resume: ' + name)
    for service, expected in load(root / 'images.json').items():
        actual = p.shell(['docker', 'image', 'inspect', 'llm-deployment/'+service+':local', '--format', '{{.Id}}'])['stdout'].strip()
        if actual != expected:
            raise RuntimeError('Frozen image changed: ' + service)
    lock = root / 'RUNNING.lock'
    with lock.open('x', encoding='utf-8') as f:
        json.dump({'pid': os.getpid(), 'utc': p.utc(), 'script': str(Path(__file__).resolve())}, f)
    state = {'pid': os.getpid(), 'started_at_utc': p.utc(), 'status': 'running', 'target_sessions': 6}
    p.save(root / 'execution.json', state)
    p.ctypes.windll.kernel32.SetThreadExecutionState(0x80000003)
    okay = False
    try:
        for repeat in range(1,4):
            for service in (('vllm','sglang') if repeat%2 else ('sglang','vllm')):
                done = [load(x) for x in (root / 'sessions').glob('*/baseline-summary.json')]
                if any(x['service']==service and x['repeat']==repeat and x['checks_pass'] for x in done):
                    continue
                run_one(root, cfg, service, repeat)
                if (root / 'STOP_AFTER_SESSION').exists():
                    raise RuntimeError('Stop requested at completed session boundary')
        state['status'] = 'measurements_completed'
        okay = True
    except BaseException as exc:
        state.update(status='failed_or_interrupted', error=p.clean(str(exc)), traceback=traceback.format_exc())
        p2.log('FAILED ' + state['error'])
    finally:
        try:
            p2.restore(root)
        except Exception as exc:
            state['restoration_error'] = p.clean(str(exc)); okay = False
        try:
            import report_baseline
            report_baseline.report(root)
            validation = load(root / 'validation.json')
            if okay and validation['all_checks_pass']:
                state['status'] = 'completed'
            else:
                okay = False
                if state['status']=='measurements_completed':
                    state['status'] = 'validation_failed'
        except Exception as exc:
            state['report_error'] = p.clean(str(exc)); okay = False
        state['completed_at_utc'] = p.utc()
        p.save(root / 'execution.json', state)
        p.ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        lock.unlink(missing_ok=True)
    return okay


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action', choices=('prepare','run','report'))
    ap.add_argument('--root', required=True, type=Path)
    args = ap.parse_args()
    if args.action=='prepare':
        prepare(args.root)
    elif args.action=='report':
        import report_baseline
        report_baseline.report(args.root)
    elif not execute(args.root):
        raise SystemExit(1)
