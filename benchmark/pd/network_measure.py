"""GPU-to-GPU Mooncake TCP wall time, verified outside each timed interval."""
import argparse
import hashlib
import json
import os
import socket
import struct
import time
from pathlib import Path
import numpy as np
import torch

def send(sock,row):
    data=json.dumps(row).encode()
    sock.sendall(struct.pack('!I',len(data))+data)

def receive(sock):
    def exact(n):
        data=bytearray()
        while len(data)<n:
            part=sock.recv(n-len(data))
            if not part: raise EOFError('Peer closed control connection')
            data.extend(part)
        return bytes(data)
    n=struct.unpack('!I',exact(4))[0]
    assert 0<n<65536
    return json.loads(exact(n))

p=argparse.ArgumentParser()
p.add_argument('role',choices=['target','initiator'])
p.add_argument('--local',required=True)
p.add_argument('--remote')
p.add_argument('--output',required=True)
p.add_argument('--transfer-timeout',type=int,default=180)
p.add_argument('--sizes',type=int,nargs='+',default=[1,16,64,128,512])
p.add_argument('--repeats',type=int,default=10)
a=p.parse_args()
os.environ['MC_FORCE_TCP']='1'
os.environ['MC_TRANSFER_TIMEOUT']=str(a.transfer_timeout)
maximum=512*1024*1024
tensor=torch.zeros(maximum,dtype=torch.uint8,device='cuda')
torch.cuda.synchronize()
# Match SGLang's order: initialize the selected HIP device before Mooncake.
from mooncake.engine import TransferEngine
engine=TransferEngine()
assert engine.initialize(a.local,'P2PHANDSHAKE','tcp','')==0
assert engine.register_memory(tensor.data_ptr(),maximum)==0
if a.role=='target':
    listener=socket.socket();listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    listener.bind((a.local,35556));listener.listen(1);listener.settimeout(180)
    sock,_=listener.accept();listener.close()
else:
    deadline=time.monotonic()+120
    while True:
        try:
            sock=socket.create_connection((a.remote,35556),timeout=10)
            break
        except OSError:
            if time.monotonic()>deadline: raise
            time.sleep(2)
sock.settimeout(300)
out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
with sock,out.open('w') as f:
    send(sock,{'session':f'{a.local}:{engine.get_rpc_port()}','pointer':tensor.data_ptr()})
    remote=receive(sock)
    rtt=[]
    for rep in range(20):
        if a.role=='initiator':
            begin=time.perf_counter();send(sock,{'ping':rep})
            assert receive(sock)['pong']==rep
            rtt.append(time.perf_counter()-begin)
        else:
            assert receive(sock)['ping']==rep;send(sock,{'pong':rep})
    if rtt:
        out.with_suffix('.rtt.json').write_text(json.dumps({'round_trip_s':rtt}))
    for mib in a.sizes:
        assert 0<mib<=512
        n=mib*1024*1024
        for sender in ['initiator','target']:
            for rep in range(a.repeats):
                value=(rep+11) if sender==a.role else 0
                tensor[:n].fill_(value);torch.cuda.synchronize()
                send(sock,{'ready':True});assert receive(sock)['ready']
                if sender==a.role:
                    begin=time.perf_counter()
                    rc=engine.transfer_sync_write(remote['session'],tensor.data_ptr(),remote['pointer'],n)
                    send(sock,{'rc':rc})
                    assert receive(sock)['synchronized']
                    elapsed=time.perf_counter()-begin
                    actual=receive(sock)['sha256']
                    expected=hashlib.sha256(bytes([rep+11])*n).hexdigest()
                    passed=rc==0 and actual==expected
                    row={'mib':mib,'bytes':n,'sender':sender,'repeat':rep,'elapsed_s':elapsed,
                         'mib_s':mib/elapsed if passed else None,'passed':passed,
                         'rc':rc,'expected_sha256':expected,'actual_sha256':actual,
                         'transfer_timeout_s':a.transfer_timeout}
                    f.write(json.dumps(row)+'\n');f.flush();print(json.dumps(row),flush=True)
                    send(sock,{'passed':passed})
                else:
                    rc=receive(sock)['rc'];torch.cuda.synchronize()
                    send(sock,{'synchronized':True})
                    actual=hashlib.sha256(tensor[:n].cpu().numpy().tobytes()).hexdigest()
                    send(sock,{'sha256':actual})
                    passed=receive(sock)['passed']
                assert passed
engine.unregister_memory(tensor.data_ptr())
