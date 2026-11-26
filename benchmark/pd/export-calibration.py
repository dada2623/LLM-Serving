#!/usr/bin/env python3
"""Export verified EPP-Opt completion records without contacting the predictor."""

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sys

MARKER = 'EPP_OPT_CALIBRATION '
ROUTES = ('amd', 'nvidia', 'ap-nd', 'np-ad')


def integer(value, minimum):
    return type(value) is int and value >= minimum


def validate(record):
    sample = record['observation']
    features = sample['features']
    route = features['route']
    prefill, decode = record['prefill_endpoint'], record['decode_endpoint']
    if (not isinstance(sample['request_id'], str) or not sample['request_id']
            or route not in ROUTES
            or not all(isinstance(endpoint, str) and endpoint for endpoint in (prefill, decode))
            or (prefill == decode) != (route in ('amd', 'nvidia'))):
        raise ValueError('invalid request ID, route or execution endpoints')
    for field in ('model', 'runtime'):
        if not isinstance(features[field], str) or not features[field]:
            raise ValueError(f'invalid {field}')
    if (not integer(features['input_tokens'], 1)
            or not integer(features['output_budget'], 1)
            or not integer(sample['output_tokens'], 1)):
        raise ValueError('invalid token counts')
    for stage in ('prefill', 'decode'):
        load = features[stage]
        if (not all(integer(load[field], 0) for field in ('cached_tokens', 'running', 'waiting'))
                or load['cached_tokens'] > features['input_tokens']):
            raise ValueError('invalid endpoint load')
    if route in ('amd', 'nvidia') and features['prefill'] != features['decode']:
        raise ValueError('whole-request endpoint snapshots differ')
    ttft, e2e = sample['ttft_ms'], sample['e2e_ms']
    if (not all(type(value) in (int, float) and math.isfinite(value) and value > 0 for value in (ttft, e2e))
            or e2e < ttft or (sample['output_tokens'] > 1 and e2e == ttft)):
        raise ValueError('invalid latency measurements')
    return sample


def collect(logs, model, runtime, request_ids=None):
    records = {}
    for path in logs:
        with path.open() as log:
            for line_number, line in enumerate(log, 1):
                prefix, marker, payload = line.partition(MARKER)
                # Accept raw stdout or the timestamp prefix from kubectl logs --timestamps.
                if not marker or len(prefix.split()) > 1 or (prefix and 'T' not in prefix):
                    continue
                try:
                    record = json.loads(payload)
                    sample = validate(record)
                    features = sample['features']
                    if (features['model'], features['runtime']) != (model, runtime):
                        continue
                    if (request_ids is not None and sample['request_id'] not in request_ids
                            and record['client_request_id'] not in request_ids):
                        continue
                    request_id = sample['request_id']
                    if request_id in records and records[request_id] != record:
                        raise ValueError(f'conflicting observations for {request_id}')
                    records[request_id] = record
                except (TypeError, ValueError, KeyError) as exc:
                    raise ValueError(f'{path}:{line_number}: {exc}') from exc
    return [records[key]['observation'] for key in sorted(records)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('logs', type=Path, nargs='+', help='one or more EPP container logs scoped to the benchmark run')
    parser.add_argument('--model', required=True)
    parser.add_argument('--runtime', required=True)
    parser.add_argument('--request-ids', type=Path, help='optional accepted target request IDs, one per line')
    parser.add_argument('--require-all-routes', action='store_true', help='reject output if a route has no verified samples')
    args = parser.parse_args()
    try:
        request_ids = None if args.request_ids is None else set(args.request_ids.read_text().splitlines()) - {''}
        samples = collect(args.logs, args.model, args.runtime, request_ids)
        counts = Counter(sample['features']['route'] for sample in samples)
        if not samples:
            raise ValueError('no verified samples match the filters')
        if args.require_all_routes and any(not counts[route] for route in ROUTES):
            raise ValueError(f'missing verified samples for: {", ".join(route for route in ROUTES if not counts[route])}')
    except (OSError, ValueError) as exc:
        parser.exit(1, f'{exc}\n')
    print('Verified observations: ' + ', '.join(f'{route}={counts[route]}' for route in ROUTES), file=sys.stderr)
    for sample in samples:
        print(json.dumps(sample, separators=(',', ':')))


if __name__ == '__main__':
    main()
