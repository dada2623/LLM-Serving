"""Submit a 14K prefill after a short request begins streaming decode."""
import argparse
import concurrent.futures as cf
import gzip
import json
import threading
import time
from pathlib import Path
from measure import consume,HOSTS

p=argparse.ArgumentParser()
p.add_argument('--dataset',required=True)
p.add_argument('--mode',choices=['N','A','NP-AD','AP-ND','REPLICAS'],required=True)
p.add_argument('--output',required=True)
a=p.parse_args()
out=Path(a.output);out.mkdir(parents=True,exist_ok=False)
data=json.loads(Path(a.dataset).read_text())['records']
short=[r for r in data if r['input_tokens']==128]
long=[r for r in data if r['input_tokens']==14336]
origin=time.perf_counter()
pd=a.mode in ['NP-AD','AP-ND']
pside,dside=('N','A') if a.mode=='NP-AD' else ('A','N')

def request(record,output,stem,on_content=None,side=None):
    body={'input_ids':record['input_ids'],'sampling_params':{'temperature':0,'top_p':1,
        'max_new_tokens':output,'ignore_eos':True},'stream':True}
    scheduled=time.perf_counter()-origin
    if pd:
        body.update(bootstrap_host=HOSTS[pside],bootstrap_port=18998,bootstrap_room=time.time_ns()%(2**62))
        with cf.ThreadPoolExecutor(max_workers=2) as pool:
            f=pool.submit(consume,HOSTS[pside],body,stem.with_suffix('.prefill.jsonl.gz'),origin,scheduled)
            r=consume(HOSTS[dside],body,stem.with_suffix('.decode.jsonl.gz'),origin,scheduled,on_content)
            pr=f.result()
        r['prefill_error']=pr.get('error')
    else:
        r=consume(HOSTS[side or a.mode],body,stem.with_suffix('.single.jsonl.gz'),origin,scheduled,on_content)
    r['success']=not r.get('error') and not r.get('prefill_error') and r['completion_tokens']==output
    stem.with_suffix('.json').write_text(json.dumps(r))
    return r

results=[]
for i in range(10):
    ready=threading.Event()
    sside=('N' if i%2==0 else 'A') if a.mode=='REPLICAS' else None
    lside=('A' if sside=='N' else 'N') if a.mode=='REPLICAS' else None
    with cf.ThreadPoolExecutor(max_workers=2) as pool:
        s=pool.submit(request,short[i],512,out/f'pair{i:02}-S',ready.set,sside)
        if not ready.wait(timeout=180): raise TimeoutError('Short request never started visible decode')
        time.sleep(.25)
        arrival=time.perf_counter()-origin
        l=pool.submit(request,long[i],1024,out/f'pair{i:02}-C',None,lside)
        sr,lr=s.result(),l.result()
    suffix='.decode.jsonl.gz' if pd else '.single.jsonl.gz'
    with gzip.open(str(out/f'pair{i:02}-S')+suffix,'rt') as f:
        events=[json.loads(line) for line in f]
    chunks=[];last_text=''
    for event in events:
        text=event.get('event',{}).get('text','')
        if text and text!=last_text: chunks.append(event['at_s']);last_text=text
    gaps=[{'start_s':left,'end_s':right,'gap_s':right-left,'after_long_arrival':right>=arrival}
          for left,right in zip(chunks,chunks[1:])]
    row={'pair':i,'mode':a.mode,'long_arrival_s':arrival,'short':sr,'long':lr,'chunk_gaps':gaps,
         'success':sr['success'] and lr['success']}
    (out/f'pair{i:02}-summary.json').write_text(json.dumps(row))
    print(json.dumps({'pair':i,'success':row['success']}),flush=True)
    results.append(row)
    if not row['success']: break
(out/'interference-summary.json').write_text(json.dumps({'mode':a.mode,'pairs':len(results),
    'passed':len(results)==10 and all(r['success'] for r in results)},indent=2))
raise SystemExit(0 if len(results)==10 and all(r['success'] for r in results) else 1)
