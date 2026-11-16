"""Read-only Linux resource sampling. Missing driver counters stay unknown."""
import argparse
import datetime as dt
import json
from pathlib import Path
import subprocess
import time

p=argparse.ArgumentParser()
p.add_argument('--output',required=True)
p.add_argument('--side',choices=['N','A'],required=True)
p.add_argument('--interval',type=float,default=5)
a=p.parse_args()
out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
with out.open('a') as f:
    while not Path(str(out)+'.STOP').exists():
        row={'time':dt.datetime.now(dt.timezone.utc).isoformat(),'monotonic_s':time.monotonic(),'side':a.side}
        try:
            row['meminfo']={line.split(':')[0]:int(line.split()[1])*1024
                for line in Path('/proc/meminfo').read_text().splitlines() if len(line.split())>=2}
            row['cpu_ticks']=Path('/proc/stat').read_text().splitlines()[0]
            row['network']=Path('/proc/net/dev').read_text()
            row['loadavg']=Path('/proc/loadavg').read_text().strip()
            row['processes']=[]
            for proc in Path('/proc').iterdir():
                if not proc.name.isdigit(): continue
                try:
                    name=(proc/'comm').read_text().strip()
                    if any(x in name.lower() for x in ['sglang','python','sshd','tun_link']):
                        row['processes'].append({'pid':int(proc.name),'name':name,
                            'stat':(proc/'stat').read_text(),'io':(proc/'io').read_text()})
                except (FileNotFoundError,PermissionError,ProcessLookupError): pass
            row['drm']={str(x):x.read_text().strip() for pattern in
                ['card*/device/mem_info_vram_used','card*/device/mem_info_vram_total','card*/device/gpu_busy_percent']
                for x in Path('/sys/class/drm').glob(pattern)}
            if a.side=='N':
                r=subprocess.run(['nvidia-smi','--query-gpu=memory.used,memory.total,utilization.gpu,temperature.gpu,power.draw',
                    '--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=4)
                row['nvidia_smi']=r.stdout.strip()
        except Exception as e: row['error']=repr(e)
        f.write(json.dumps(row)+'\n');f.flush()
        time.sleep(a.interval)
