"""Read tensor storage metadata after startup methods, never in inference forwards.

Used only by the benchmark Compose override. Production images are unchanged.
The import hook is installed in multiprocessing children as well as the parent.
"""
import functools
import importlib.abc
import importlib.machinery
import json
import os
import runpy
import sys


def tensor_ledger(value):
    import torch
    seen_objects=set(); seen_storage=set(); devices={}; tensor_count=0
    def visit(obj,depth):
        nonlocal tensor_count
        if id(obj) in seen_objects or depth>6:
            return
        seen_objects.add(id(obj))
        if isinstance(obj,torch.Tensor):
            tensor_count+=1
            if obj.device.type=='meta':
                return
            storage=obj.untyped_storage()
            key=(str(obj.device),storage.data_ptr(),storage.nbytes())
            if key not in seen_storage:
                seen_storage.add(key)
                devices[str(obj.device)]=devices.get(str(obj.device),0)+storage.nbytes()
        elif isinstance(obj,dict):
            for item in obj.values():
                visit(item,depth+1)
        elif isinstance(obj,(list,tuple)):
            for item in obj:
                visit(item,depth+1)
        elif hasattr(obj,'__dict__') and not isinstance(obj,torch.nn.Module):
            for item in vars(obj).values():
                visit(item,depth+1)
    visit(value,0)
    return {'unique_storage_bytes_by_device':devices,'tensors_seen':tensor_count,
            'unique_storages':len(seen_storage)}


def record(instance,kind,module,method):
    import torch
    body={'service':os.environ.get('P2_PROBE_SERVICE'),'pid':os.getpid(),
          'module':module,'method':method,'kind':kind,'class':type(instance).__name__}
    if kind=='weights':
        model=getattr(instance,'model',None)
        if model is None:
            return
        params=list(model.named_parameters())
        buffers=list(model.named_buffers())
        body['parameters']=tensor_ledger([t for _,t in params])
        body['buffers']=tensor_ledger([t for _,t in buffers])
        body['combined']=tensor_ledger([t for _,t in params+buffers])
        body['parameter_count']=len(params)
        body['buffer_count']=len(buffers)
        body['cpu_parameter_names']=[name for name,t in params if t.device.type=='cpu']
    elif kind=='pool':
        body['pool']=tensor_ledger(vars(instance))
    else:
        selected={k:v for k,v in vars(instance).items()
                  if ('cache' in k or 'pool' in k) and k!='model'}
        body['cache_attribute_names']=list(selected)
        body['pool']=tensor_ledger(selected)
    if torch.cuda.is_initialized():
        body['torch_allocated_bytes']=torch.cuda.memory_allocated()
        body['torch_reserved_bytes']=torch.cuda.memory_reserved()
    print('P2_STORAGE_JSON '+json.dumps(body,sort_keys=True),flush=True)


def wrap(cls,name,kind,module):
    original=cls.__dict__.get(name)
    if not callable(original) or getattr(original,'_p2_probe_wrapped',False):
        return
    @functools.wraps(original)
    def wrapped(self,*args,**kwargs):
        result=original(self,*args,**kwargs)
        try:
            record(self,kind,module,name)
        except Exception as exc:
            print('P2_STORAGE_ERROR '+json.dumps({'kind':kind,'module':module,'error':str(exc)}),flush=True)
        return result
    wrapped._p2_probe_wrapped=True
    setattr(cls,name,wrapped)


class Loader(importlib.abc.Loader):
    def __init__(self,original,name):
        self.original,self.name=original,name
    def create_module(self,spec):
        return self.original.create_module(spec) if hasattr(self.original,'create_module') else None
    def exec_module(self,module):
        self.original.exec_module(module)
        for name,cls in list(vars(module).items()):
            if not isinstance(cls,type) or cls.__module__!=self.name:
                continue
            if name.endswith('ModelRunner'):
                wrap(cls,'load_model','weights',self.name)
                wrap(cls,'initialize_kv_cache','runner_caches',self.name)
                wrap(cls,'init_memory_pool','runner_caches',self.name)
            if name in ('MambaPool','MHATokenToKVPool'):
                wrap(cls,'__init__','pool',self.name)


class Finder(importlib.abc.MetaPathFinder):
    targets={'vllm.v1.worker.gpu.model_runner','vllm.v1.worker.gpu_model_runner',
             'sglang.srt.model_executor.model_runner','sglang.srt.mem_cache.memory_pool'}
    def find_spec(self,fullname,path=None,target=None):
        if fullname not in self.targets:
            return None
        spec=importlib.machinery.PathFinder.find_spec(fullname,path)
        if spec and spec.loader:
            spec.loader=Loader(spec.loader,fullname)
        return spec


sys.meta_path.insert(0,Finder())

if __name__=='__main__':
    service=os.environ['P2_PROBE_SERVICE']
    if service=='vllm':
        sys.argv=['vllm','serve']+sys.argv[1:]
        from vllm.entrypoints.cli.main import main
        main()
    elif service=='sglang':
        runpy.run_module('sglang.launch_server',run_name='__main__')
    else:
        raise ValueError('Unknown benchmark service')
