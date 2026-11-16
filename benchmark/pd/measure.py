"""SGLang native SSE measurement. One decode response counted per PD request.

The load generator runs in the NVIDIA carrier for every configuration.
Output is cumulative in /generate; token deltas come from completion_tokens.
Each request and every received chunk is flushed to its own file.
"""
import argparse
import concurrent.futures as cf
import hashlib
import gzip
import json
import random
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

HOSTS = {'N': 'nvidia-pd-host', 'A': 'amd-pd-host'}
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def clock():
    return time.perf_counter()

def consume(host, body, destination, origin, scheduled, on_content=None, cancel=None):
    start = clock()
    row = {'host': host, 'scheduled_s': scheduled, 'submitted_s': start-origin,
           'status': None, 'completion_tokens': 0, 'prompt_tokens': None}
    first = last = None
    first_token = last_token = None
    previous_count = 0
    previous_text = ''
    req = urllib.request.Request(f'http://{host}:18100/generate',
          data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    with gzip.open(destination, 'wt', encoding='utf-8') as raw:
        try:
            if cancel is not None and cancel.is_set(): raise TimeoutError('Drain deadline exceeded before submission')
            with opener.open(req, timeout=600) as response:
                row['status'] = response.status
                finished=threading.Event()
                def interrupt_read():
                    while not finished.wait(.25):
                        if cancel.is_set():
                            try: response.fp.raw._sock.shutdown(socket.SHUT_RDWR)
                            except (AttributeError,OSError): pass
                            return
                if cancel is not None:
                    threading.Thread(target=interrupt_read,daemon=True).start()
                for line in response:
                    at = clock()
                    line = line.decode('utf-8').strip()
                    if not line.startswith('data:'):
                        continue
                    data = line[5:].strip()
                    if data == '[DONE]':
                        row['done_marker'] = True
                        continue
                    event = json.loads(data)
                    raw.write(json.dumps({'at_s': at-origin, 'event': event})+'\n')
                    raw.flush()
                    if 'error' in event:
                        raise RuntimeError(str(event['error']))
                    meta = event.get('meta_info') or {}
                    count = meta.get('completion_tokens', previous_count)
                    text = event.get('text') or ''
                    if count > previous_count:
                        if first_token is None:
                            first_token = at
                        last_token = at
                    if text and text != previous_text:
                        if first is None:
                            first = at
                            if on_content is not None: on_content()
                        last = at
                    previous_count, previous_text = count, text
                    row.update(completion_tokens=count, prompt_tokens=meta.get('prompt_tokens'),
                               finish_reason=meta.get('finish_reason'), cached_tokens=meta.get('cached_tokens'))
                    if event.get('output_ids') is not None:
                        row['output_ids'] = event['output_ids']
                row['text'] = previous_text
                finished.set()
                if cancel is not None and cancel.is_set(): raise TimeoutError('Drain deadline exceeded')
        except Exception as exc:
            row['error'] = repr(exc)
        finally:
            if 'finished' in locals(): finished.set()
        row.update(finished_s=clock()-origin, ttft_s=None if first is None else first-origin-scheduled,
                   service_ttft_s=None if first is None else first-start,
                   first_content_s=None if first is None else first-origin,
                   last_content_s=None if last is None else last-origin,
                   first_token_s=None if first_token is None else first_token-origin,
                   last_token_s=None if last_token is None else last_token-origin)
        row['e2e_s'] = row['finished_s']-scheduled
        row['submission_delay_s'] = row['submitted_s']-scheduled
        row['tpot_s'] = ((last_token-first_token)/(row['completion_tokens']-1)
                         if first_token is not None and last_token is not None and row['completion_tokens'] > 1 else None)
        raw.write(json.dumps({'result': row})+'\n')
        raw.flush()
    return row

def main(a):
    HOSTS.update(N=a.nvidia_host, A=a.amd_host)
    out = Path(a.output)
    out.mkdir(parents=True, exist_ok=False)
    dataset_path = Path(a.dataset)
    dataset = json.loads(dataset_path.read_text())['records']
    by_length = {n: [r for r in dataset if r['input_tokens'] == n] for n in [128,2048,14336]}
    (out/'settings.json').write_text(json.dumps(vars(a), indent=2))
    (out/'dataset.sha256').write_text(hashlib.sha256(dataset_path.read_bytes()).hexdigest())
    origin = clock()
    lock = threading.Lock()
    active = {'N': 0, 'A': 0}
    tie = 0
    stop = threading.Event()
    cancel=threading.Event()
    drain_timer=None
    def begin_drain():
        nonlocal drain_timer
        if drain_timer is None:
            drain_timer=threading.Timer(a.drain_timeout,cancel.set)
            drain_timer.daemon=True;drain_timer.start()
    completed = []

    def request(index, workload, scheduled):
        nonlocal tie
        length, output = {'S':(128,512), 'L':(14336,256), 'C':(14336,1024), 'M':(2048,256)}[workload]
        if a.output_tokens:
            output = a.output_tokens
        record = by_length[length][index % len(by_length[length])]
        body = {'input_ids': record['input_ids'], 'sampling_params': {
                'temperature':0, 'top_p':1, 'max_new_tokens':output,
                'ignore_eos':not a.allow_eos}, 'stream':True, 'return_logprob':False}
        pd = a.mode in ['NP-AD','AP-ND']
        pside, dside = ('N','A') if a.mode == 'NP-AD' else ('A','N')
        if a.mode == 'REPLICAS':
            with lock:
                side = min(active, key=lambda k:(active[k], (0 if k == ('N' if tie%2==0 else 'A') else 1)))
                tie += 1
                active[side] += 1
        else:
            side = dside if pd else a.mode
        if pd:
            body.update(bootstrap_host=HOSTS[pside], bootstrap_port=18998,
                        bootstrap_room=(time.time_ns()+index) % (2**62))
        try:
            if pd:
                with cf.ThreadPoolExecutor(max_workers=2) as pair:
                    pf = pair.submit(consume, HOSTS[pside], body, out/f'{index:06}-prefill.jsonl.gz', origin, scheduled,cancel=cancel)
                    result = consume(HOSTS[dside], body, out/f'{index:06}-decode.jsonl.gz', origin, scheduled,cancel=cancel)
                    p_result = pf.result()
                result['prefill'] = {k:v for k,v in p_result.items() if k not in ['text','output_ids']}
                if p_result.get('error'):
                    result['error'] = 'prefill: '+p_result['error']
            else:
                result = consume(HOSTS[side], body, out/f'{index:06}-single.jsonl.gz', origin, scheduled,cancel=cancel)
            result.update(index=index, workload=workload, input_id=record['id'],
                          expected_input_tokens=length, expected_output_tokens=output,
                          side=side, mode=a.mode)
            reason=result.get('finish_reason') or {}
            reason_type=reason.get('type') if isinstance(reason,dict) else reason
            result['success'] = (not result.get('error') and result['status']==200
                and result['prompt_tokens']==length and result['completion_tokens']>0
                and (a.allow_eos or result['completion_tokens']==output)
                and reason_type in ['stop','length'])
            if not result['success']:
                stop.set()
            (out/f'{index:06}-result.json').write_text(json.dumps(result), encoding='utf-8')
            with lock:
                completed.append(result)
                with (out/'progress.jsonl').open('a') as f:
                    f.write(json.dumps({k:v for k,v in result.items() if k not in ['text','output_ids']})+'\n')
            return result
        finally:
            if a.mode == 'REPLICAS':
                with lock:
                    active[side] -= 1

    rng = random.Random(a.seed)
    block = []
    def shape(i):
        if a.workload in ['S','L','C','M']:
            return a.workload
        if not block:
            block.extend(['C']*8+['M']*2 if a.workload=='M-code' else ['S']*8+['C']*2)
            rng.shuffle(block)
        return block.pop()

    count = 0
    with cf.ThreadPoolExecutor(max_workers=max(32,a.concurrency)) as pool:
        if a.rate:
            scheduled = 0.0
            futures=[]
            while not stop.is_set():
                if count>=a.requests and scheduled>=a.duration or scheduled>a.deadline:
                    break
                delay=origin+scheduled-clock()
                if delay>0:
                    time.sleep(delay)
                futures.append(pool.submit(request,count,shape(count),scheduled))
                count+=1
                scheduled+=rng.expovariate(a.rate)
                if len([f for f in futures if not f.done()])>256:
                    stop.set()
                    break
            begin_drain()
            for f in futures:
                f.result()
        else:
            pending={}
            while True:
                elapsed=clock()-origin
                enough=count>=a.requests and elapsed>=a.duration
                if enough or stop.is_set() or elapsed>=a.deadline: begin_drain()
                if not stop.is_set() and not enough and elapsed<a.deadline:
                    while len(pending)<a.concurrency:
                        f=pool.submit(request,count,shape(count),clock()-origin)
                        pending[f]=count
                        count+=1
                        if count>=a.requests and a.duration==0:
                            break
                if not pending:
                    break
                done,_=cf.wait(pending,timeout=1,return_when=cf.FIRST_COMPLETED)
                for f in done:
                    f.result()
                    del pending[f]
    end=clock()-origin
    if drain_timer is not None: drain_timer.cancel()
    summary={'mode':a.mode,'workload':a.workload,'submitted':count,'completed':len(completed),
             'successes':sum(r['success'] for r in completed),'errors':sum(not r['success'] for r in completed),
             'elapsed_s':end,'sample_complete':len(completed)>=a.requests and end>=a.duration,
             'output_tokens':sum(r['completion_tokens'] for r in completed if r['success']),
             'settings':vars(a)}
    (out/'summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary),flush=True)
    return 0 if summary['errors']==0 else 1

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--dataset',required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--mode',choices=['N','A','NP-AD','AP-ND','REPLICAS'],required=True)
    p.add_argument('--nvidia-host',default='nvidia-pd-host')
    p.add_argument('--amd-host',default='amd-pd-host')
    p.add_argument('--workload',choices=['S','L','C','M','M-code','M-interference'],required=True)
    p.add_argument('--concurrency',type=int,default=1)
    p.add_argument('--requests',type=int,default=10)
    p.add_argument('--duration',type=float,default=30)
    p.add_argument('--deadline',type=float,default=180)
    p.add_argument('--rate',type=float)
    p.add_argument('--seed',type=int,default=pd-run)
    p.add_argument('--output-tokens',type=int)
    p.add_argument('--allow-eos',action='store_true')
    p.add_argument('--drain-timeout',type=float,default=600,
                   help='Validation default; the benchmark coordinator explicitly freezes 120 seconds.')
    raise SystemExit(main(p.parse_args()))
