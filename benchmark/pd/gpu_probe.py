"""Inspect standalone HIP visibility without starting a model."""
import os
import json
import subprocess
import sys
if len(sys.argv)==1:
    for rocr,hip in [('1','0'),('1',None),(None,'1'),(None,None),('0',None)]:
        env=os.environ.copy()
        for key,value in [('ROCR_VISIBLE_DEVICES',rocr),('HIP_VISIBLE_DEVICES',hip)]:
            if value is None: env.pop(key,None)
            else: env[key]=value
        r=subprocess.run([sys.executable,__file__,'child'],env=env,capture_output=True,text=True,timeout=30)
        print(r.stdout or r.stderr,flush=True)
else:
    if sys.argv[1]=='plugin': import vllm.platforms
    import torch
    row={key:os.environ.get(key) for key in ['ROCR_VISIBLE_DEVICES','HIP_VISIBLE_DEVICES','CUDA_VISIBLE_DEVICES']}
    row['count']=torch.cuda.device_count();row['devices']=[]
    for i in range(row['count']):
        try:
            p=torch.cuda.get_device_properties(i)
            row['devices'].append({'name':p.name,'total_memory':p.total_memory,
                'gcnArchName':getattr(p,'gcnArchName',None)})
        except Exception as e: row['error']=repr(e)
    print(json.dumps(row),flush=True)
