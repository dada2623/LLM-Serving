#!/usr/bin/env python3
"""Package the EPP-Opt scratch image as an OCI image archive without a daemon."""

import gzip
import hashlib
import json
from pathlib import Path
import sys
import tarfile
import tempfile


OCI = 'application/vnd.oci.image.'
EXECUTABLES = ('epp', 'pd-sidecar', 'predictor')


def sha256(data):
    return 'sha256:' + hashlib.sha256(data).hexdigest()


def add_blob(layout, data, media_type):
    digest = sha256(data)
    (layout / 'blobs' / 'sha256' / digest.split(':', 1)[1]).write_bytes(data)
    return {'mediaType': OCI + media_type, 'digest': digest, 'size': len(data)}


def encode(value):
    return json.dumps(value, separators=(',', ':'), sort_keys=True).encode()


def package(output, staging, image, revision):
    with tempfile.TemporaryDirectory(prefix='epp-opt-oci-') as work:
        root = Path(work)
        layout = root / 'image'
        (layout / 'blobs' / 'sha256').mkdir(parents=True)
        layer_path = root / 'rootfs.tar'
        with tarfile.open(layer_path, 'w') as layer:
            for name in EXECUTABLES:
                binary = staging / name
                entry = tarfile.TarInfo(f'app/{name}')
                entry.mode = 0o555
                entry.size = binary.stat().st_size
                with binary.open('rb') as src:
                    layer.addfile(entry, src)
        layer = layer_path.read_bytes()
        config = add_blob(layout, encode({
            'architecture': 'amd64', 'os': 'linux',
            'config': {
                'Entrypoint': ['/app/epp'], 'User': '65532:65532',
                'ExposedPorts': {f'{port}/tcp': {} for port in (9002, 9003, 9090, 8080)},
                'Labels': {
                    'org.opencontainers.image.title': 'EPP-Opt',
                    'org.opencontainers.image.source': 'https://github.com/llm-d/llm-d-router',
                    'org.opencontainers.image.revision': revision,
                },
            },
            'rootfs': {'type': 'layers', 'diff_ids': [sha256(layer)]},
        }), 'config.v1+json')
        manifest = add_blob(layout, encode({
            'schemaVersion': 2,
            'mediaType': OCI + 'manifest.v1+json',
            'config': config,
            'layers': [add_blob(layout, gzip.compress(layer, mtime=0), 'layer.v1.tar+gzip')],
        }), 'manifest.v1+json')
        manifest['annotations'] = {
            'org.opencontainers.image.ref.name': image.rsplit(':', 1)[-1],
            'io.containerd.image.name': image,
        }
        (layout / 'oci-layout').write_bytes(encode({'imageLayoutVersion': '1.0.0'}))
        (layout / 'index.json').write_bytes(encode({'schemaVersion': 2, 'manifests': [manifest]}))
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=output.name + '.', delete=False) as pending:
            temporary = Path(pending.name)
        try:
            with tarfile.open(temporary, 'w') as archive:
                for entry in sorted(layout.rglob('*')):
                    if entry.is_file():
                        archive.add(entry, arcname=str(entry.relative_to(layout)))
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)
    print(f'{image} {manifest["digest"]} {output}')


if __name__ == '__main__':
    archive, binaries, reference, upstream = sys.argv[1:]
    package(Path(archive), Path(binaries), reference, upstream)
