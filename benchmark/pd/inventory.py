"""Hash model files and record public runtime versions without reading API keys."""
import argparse
import hashlib
import importlib.metadata as meta
import json
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument('model');p.add_argument('--output',required=True)
a=p.parse_args();model=Path(a.model)
rows=[]
for f in sorted(model.iterdir()):
    # Download-client manifests describe local caching, not inference inputs.
    if not f.name.startswith('.') and f.suffix in ['.safetensors','.json','.jinja','.txt'] and f.is_file():
        with f.open('rb') as stream:
            digest=hashlib.file_digest(stream,'sha256').hexdigest()
        rows.append({'name':f.name,'bytes':f.stat().st_size,'sha256':digest})
versions={}
for name in ['torch','sglang','transformers','mooncake-transfer-engine','triton']:
    try: versions[name]=meta.version(name)
    except meta.PackageNotFoundError: versions[name]=None
Path(a.output).write_text(json.dumps({'model':str(model),'files':rows,'versions':versions},indent=2))
print(json.dumps({'files':len(rows),'versions':versions}))
