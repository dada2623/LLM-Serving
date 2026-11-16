"""Manage only the process group recorded in this experiment's PID file."""
import argparse
import json
import os
import signal
import sys
import subprocess
import time
from pathlib import Path

p=argparse.ArgumentParser()
p.add_argument('action',choices=['start','stop','status'])
p.add_argument('--pidfile',required=True)
p.add_argument('--log')
args=sys.argv[1:]
split=args.index('--') if '--' in args else len(args)
command=args[split+1:]
a=p.parse_args(args[:split])
pidfile=Path(a.pidfile)
pid=int(pidfile.read_text()) if pidfile.exists() else None
def alive():
    if pid is None: return False
    proc=Path(f'/proc/{pid}')
    if not proc.exists(): return False
    cmd=(proc/'cmdline').read_bytes()
    if not cmd and ') Z ' in (proc/'stat').read_text():
        return False
    if b'launch_server' not in cmd and b'sglang.launch' not in cmd:
        raise RuntimeError(f'PID {pid} no longer belongs to this worker')
    return True
if a.action=='status':
    print(json.dumps({'pid':pid,'running':alive()}))
elif a.action=='stop':
    if alive():
        os.killpg(pid,signal.SIGTERM)
        deadline=time.monotonic()+30
        while alive() and time.monotonic()<deadline:
            time.sleep(.5)
        if alive(): os.killpg(pid,signal.SIGKILL)
    if pidfile.exists(): pidfile.unlink()
    print(json.dumps({'stopped_pid':pid}))
else:
    if alive(): raise RuntimeError('Worker already running')
    if not command or not a.log: raise ValueError('Need --log and a command after --')
    pidfile.parent.mkdir(parents=True,exist_ok=True)
    with open(a.log,'wb') as f:
        proc=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
    pidfile.write_text(str(proc.pid))
    print(json.dumps({'started_pid':proc.pid,'log':a.log}))
