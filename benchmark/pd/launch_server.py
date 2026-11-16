"""Launch an isolated SGLang benchmark worker; no production configuration reads."""
import argparse
import os
import sys

p = argparse.ArgumentParser()
p.add_argument('--host', required=True)
p.add_argument('--model', required=True)
p.add_argument('--role', choices=['single', 'prefill', 'decode'], default='single')
p.add_argument('--pool', type=int, default=32768)
p.add_argument('--concurrency', type=int, default=4)
p.add_argument('--slots', type=int, default=8)
p.add_argument('--memory-fraction', type=float, default=0.90)
p.add_argument('--checksum', action='store_true')
p.add_argument('--cache', action='store_true')
a = p.parse_args()
os.environ.update(HF_HUB_OFFLINE='1', SGLANG_HOST_IP=a.host,
                  MC_FORCE_TCP='1', MOONCAKE_PROTOCOL='tcp', OMP_NUM_THREADS='4')
argv = [sys.executable, '-m', 'sglang.launch_server',
        '--model-path', a.model, '--host', a.host, '--port', '18100',
        '--served-model-name', 'Qwen3.5-9B-AWQ', '--dtype', 'float16', '--quantization', 'awq',
        '--context-length', '32768', '--mem-fraction-static', str(a.memory_fraction),
        '--max-running-requests', str(a.concurrency), '--max-total-tokens', str(a.pool),
        '--max-mamba-cache-size', str(a.slots), '--chunked-prefill-size', '256',
        '--cpu-offload-gb', '0', '--attention-backend', 'triton',
        '--linear-attn-backend', 'triton', '--mamba-backend', 'triton',
        '--sampling-backend', 'pytorch', '--disable-cuda-graph',
        '--disable-custom-all-reduce', '--disable-overlap-schedule', '--skip-server-warmup',
        '--language-only']
if not a.cache:
    argv += ['--disable-radix-cache']
if a.role != 'single':
    argv += ['--disaggregation-mode', a.role, '--disaggregation-transfer-backend',
             'mooncake_tcp', '--disaggregation-bootstrap-port', '18998']
    if a.checksum:
        argv += ['--disaggregation-enable-kv-checksum']
os.execv(sys.executable, argv)
