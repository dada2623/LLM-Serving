"""Replay fixed three-turn coding conversations; natural EOS, no tool execution."""
import argparse
import concurrent.futures as cf
import hashlib
import json
import time
from pathlib import Path
from transformers import AutoTokenizer
from measure import consume,HOSTS

p=argparse.ArgumentParser()
p.add_argument('--model',required=True)
p.add_argument('--dataset',required=True)
p.add_argument('--mode',choices=['N','A','NP-AD','AP-ND','REPLICAS'],required=True)
p.add_argument('--output',required=True)
p.add_argument('--trace',required=True)
a=p.parse_args()
out=Path(a.output);out.mkdir(parents=True,exist_ok=False)
trace=Path(a.trace)
if trace.exists():
    sessions=json.loads(trace.read_text())
else:
    tok=AutoTokenizer.from_pretrained(a.model,local_files_only=True)
    records=[r for r in json.loads(Path(a.dataset).read_text())['records'] if r['input_tokens']==14336]
    sessions=[]
    for i in range(10):
        ids=records[i]['input_ids'][:]
        turns=[]
        for turn in range(3):
            if turn:
                history=('The request uses input IDs, sampling parameters and streaming. '
                    'I will inspect token counts and error handling next.'
                    '<|im_end|>\n<|im_start|>user\n'
                    f'Case {i}, follow-up {turn}. Recorded read_file tool result:\n'
                    'HTTP status: 200; prompt_tokens: 14336; completion_tokens: 1024; '
                    'finish_reason: length; CPU weight offload: 0 GiB.\n'
                    'Explain what these fields prove and what further checks are needed.'
                    '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n')
                ids=ids+tok.encode(history,add_special_tokens=False)
            assert len(ids)+1024<=32768
            turns.append({'session':i,'turn':turn,'input_tokens':len(ids),'input_ids':ids[:]})
        sessions.append(turns)
    trace.write_text(json.dumps(sessions))
(out/'settings.json').write_text(json.dumps({'mode':a.mode,'cache':False,'allow_eos':True,
    'trace_sha256':hashlib.sha256(trace.read_bytes()).hexdigest(),
    'history':'Pre-recorded identical assistant/tool history; each current reply is measured, not fed into later inputs.'},indent=2))
origin=time.perf_counter();results=[]
for turns in sessions:
    for record in turns:
        i=record['session'];turn=record['turn'];stem=out/f's{i:02}-t{turn}'
        body={'input_ids':record['input_ids'],'sampling_params':{'temperature':0,'top_p':1,'max_new_tokens':1024},'stream':True}
        pd=a.mode in ['NP-AD','AP-ND']
        pside,dside=('N','A') if a.mode=='NP-AD' else ('A','N')
        side=('N' if i%2==0 else 'A') if a.mode=='REPLICAS' else a.mode
        scheduled=time.perf_counter()-origin
        if pd:
            body.update(bootstrap_host=HOSTS[pside],bootstrap_port=18998,bootstrap_room=time.time_ns()%(2**62))
            with cf.ThreadPoolExecutor(max_workers=2) as pool:
                future=pool.submit(consume,HOSTS[pside],body,stem.with_suffix('.prefill.jsonl.gz'),origin,scheduled)
                r=consume(HOSTS[dside],body,stem.with_suffix('.decode.jsonl.gz'),origin,scheduled)
                pr=future.result()
            r['prefill_error']=pr.get('error')
        else:
            r=consume(HOSTS[side],body,stem.with_suffix('.single.jsonl.gz'),origin,scheduled)
        reason=r.get('finish_reason') or {}
        r.update(session=i,turn=turn,mode=a.mode,input_tokens=record['input_tokens'],
            success=not r.get('error') and not r.get('prefill_error') and r['status']==200
            and r['prompt_tokens']==record['input_tokens'] and r['completion_tokens']>0
            and reason.get('type') in ['stop','length'])
        stem.with_suffix('.json').write_text(json.dumps(r))
        results.append(r)
        print(json.dumps({k:v for k,v in r.items() if k not in ['text','output_ids']}),flush=True)
        if not r['success']: break
    if not results[-1]['success']: break
(out/'session-summary.json').write_text(json.dumps({'mode':a.mode,'expected_requests':30,
    'completed':len(results),'errors':sum(not r['success'] for r in results),
    'passed':len(results)==30 and all(r['success'] for r in results),'results':results},indent=2))
raise SystemExit(0 if len(results)==30 and all(r['success'] for r in results) else 1)
