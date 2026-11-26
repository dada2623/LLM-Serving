#!/usr/bin/env python3
"""Render a separate epp-opt Deployment and Service from the running router template."""
import argparse
import copy
import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent
NAME = 'epp-opt'
CALIBRATION = 'epp-opt-calibration'
CALIBRATION_ROUTE = 'x-epp-opt-calibration-route'
CALIBRATION_ACCEPTED = 'x-epp-opt-calibration-accepted'
CALIBRATION_PORTS = {'amd': 18081, 'nvidia': 18082, 'ap-nd': 18083, 'np-ad': 18084}


def configure_envoy(text, calibration_enabled=False):
    def insert_before(pattern, lines):
        nonlocal text
        matches = list(re.finditer(pattern, text, re.M))
        if len(matches) != 1:
            raise ValueError(f'Unexpected llm-serving-epp Envoy layout for {pattern}')
        match = matches[0]
        indentation = match.group(1)
        block = ''.join(f'{indentation}{line}\n' for line in lines.splitlines())
        text = text[:match.start()] + block + text[match.start():]

    insert_before(r'^( +)- remove: x-gateway-destination-endpoint$', f"""- remove: {CALIBRATION_ROUTE}
- remove: {CALIBRATION_ACCEPTED}
- remove: x-llm-d-prefiller-host-port
- remove: x-prefiller-host-port
- remove: x-epp-opt-decoder""")
    insert_before(r'^( +)- match:\n +prefix: "/"(?:\n +# [^\n]*)*\n(?=(?:(?!\n\s*- match:)[\s\S])*?cluster: original_destination_cluster)',
                  """- match:
    prefix: "/"
    headers:
      - name: x-gateway-destination-endpoint
        present_match: true
      - name: x-llm-d-prefiller-host-port
        present_match: true
      - name: x-epp-opt-decoder
        present_match: true
  route:
    cluster: epp_opt_pd
    timeout: 86400s
    idle_timeout: 86400s
  typed_per_filter_config:
    envoy.filters.http.ext_proc:
      "@type": type.googleapis.com/envoy.config.route.v3.FilterConfig
      config: {}""")
    text = re.sub(r'(?m)^( +failure_mode_allow: )true$', r'\g<1>false', text)
    insert_before(r'^( +)- name: original_destination_cluster$', """- name: epp_opt_pd
  type: STATIC
  connect_timeout: 1s
  load_assignment:
    cluster_name: epp_opt_pd
    endpoints:
      - lb_endpoints:
          - endpoint:
              address:
                socket_address:
                  address: 127.0.0.1
                  port_value: 18085""")
    if calibration_enabled:
        listener = re.search(r'(?ms)^    - name: vllm\n.*?(?=^    - name: |^  clusters:)', text)
        if listener is None:
            raise ValueError('Expected the vllm listener in the Envoy template')
        listeners = []
        for route, port in CALIBRATION_PORTS.items():
            arm = listener[0].replace('vllm', f'calibration-{route}').replace('8081', str(port))
            arm = arm.replace(f'- remove: {CALIBRATION_ROUTE}', f'''- append:
                            header:
                              key: {CALIBRATION_ROUTE}
                              value: {route}
                            append_action: OVERWRITE_IF_EXISTS_OR_ADD''')
            # Only a successful handler invocation can acknowledge the fixed route.
            # This also prevents EPP's generic bodyless-request fallback from forwarding.
            arm, count = re.subn(
                r'(?m)^( +)- name: x-gateway-destination-endpoint$',
                rf'\1- name: {CALIBRATION_ACCEPTED}\n\1  string_match:\n\1    exact: {route}\n\1- name: x-gateway-destination-endpoint',
                arm,
            )
            if count != 2:
                raise ValueError('Expected the direct and P/D destination matches')
            arm, count = re.subn(
                r'(?m)^( +)route:\n\1  cluster: inference_pool_fallback\n(?:.*\n)*?\1typed_per_filter_config:',
                r'\1direct_response:\n\1  status: 503\n\1typed_per_filter_config:',
                arm,
            )
            if count != 1:
                raise ValueError('Expected the inference pool fallback route')
            arm = arm.replace('''                        # Fail-open: EPP was unreachable and set no destination, so load-balance
                        # across the model servers without endpoint picking.''',
                              '                        # No EPP acknowledgement means no calibration dispatch.')
            arm = arm.replace('                use_remote_address: true\n',
                              '                always_set_request_id_in_response: true\n'
                              '                use_remote_address: true\n')
            arm = arm.replace('domains: ["*"]\n', f'''domains: ["*"]
                      request_headers_to_remove:
                        - {CALIBRATION_ROUTE}
                        - {CALIBRATION_ACCEPTED}
''')
            listeners.append(arm)
        text = text[:listener.end()] + ''.join(listeners) + text[listener.end():]
    return text


def render(source, envoy, calibration, image, model, runtime, verified_pd, state_size,
           calibration_namespace=None, calibration_pvc=None):
    namespace = source['metadata']['namespace']
    label = {'app.kubernetes.io/name': NAME}
    def resource(kind, version, name):
        return dict(apiVersion=version, kind=kind, metadata=dict(name=name, namespace=namespace))
    spec = copy.deepcopy(source['spec'])
    spec['selector'] = {'matchLabels': label}
    spec['strategy'] = {'type': 'RollingUpdate', 'rollingUpdate': {'maxSurge': 1, 'maxUnavailable': 0}}
    spec['template']['metadata'] = {'labels': label}
    pod = spec['template']['spec']
    if calibration_namespace and pod.get('hostNetwork'):
        raise ValueError('Calibration requires pod networking for NetworkPolicy enforcement')
    for volume in pod['volumes']:
        if volume['name'] == 'config':
            volume['configMap']['name'] = 'epp-opt-envoy'
        elif volume['name'] == 'plugins-config-volume':
            volume['configMap']['name'] = 'epp-opt-config'
    epp = next(c for c in pod['containers'] if c['name'] == 'epp')
    epp.update(image=image, imagePullPolicy='Never')
    epp['args'].append('--allow-experimental-plugins')
    epp['args'][epp['args'].index('--config-file') + 1] = '/config/epp-opt-benefit-plugins.yaml'
    pod['containers'].append({
        'name': 'pd-proxy', 'image': image, 'imagePullPolicy': 'Never',
        'command': ['/app/pd-sidecar'], 'args': [
            '--port=18085', '--kv-connector=nixlv2', '--secure-serving=false',
            '--enable-ssrf-protection', f'--inference-pool={namespace}/llm-serving',
        ],
        'env': [{'name': 'EPP_OPT_DYNAMIC_DECODER', 'value': 'true'}],
        'readinessProbe': {'tcpSocket': {'port': 18085}, 'periodSeconds': 2},
        'resources': {'requests': {'cpu': '100m', 'memory': '64Mi'}, 'limits': {'cpu': '1', 'memory': '512Mi'}},
    })
    config = (ROOT / 'epp-opt-benefit-plugins.yaml').read_text()
    values = {'model': model, 'runtime': runtime, 'verifiedPD': verified_pd,
              'calibrationEnabled': bool(calibration_namespace)}
    for key, value in values.items():
        config, matches = re.subn(rf'^    {key}: .+$', lambda _: f'    {key}: {json.dumps(value)}', config, flags=re.M)
        if matches != 1:
            raise ValueError(f'Expected exactly one EPP config entry for {key}')

    predictor = 'epp-opt-predictor'
    predictor_label = {'app.kubernetes.io/name': predictor}
    volumes = [{'name': 'state', 'persistentVolumeClaim': {'claimName': predictor}}]
    mounts = [{'name': 'state', 'mountPath': '/state'}]
    args = ['--state-dir=/state']
    resources = []
    if calibration is not None:
        if len(calibration.encode()) > 1 << 20:
            raise ValueError('Calibration seed exceeds 1 MiB. Use --calibration-pvc with a seed volume.')
        resources.append({**resource('ConfigMap', 'v1', CALIBRATION), 'data': {'observations.jsonl': calibration}})
        volumes.append({'name': 'calibration', 'configMap': {'name': CALIBRATION}})
    elif calibration_pvc:
        volumes.append({'name': 'calibration', 'persistentVolumeClaim': {
            'claimName': calibration_pvc, 'readOnly': True,
        }})
    if calibration is not None or calibration_pvc:
        mounts.append({'name': 'calibration', 'mountPath': '/calibration', 'readOnly': True})
        args.append('--calibration=/calibration/observations.jsonl')
    predictor_pod = {
        'securityContext': {'runAsNonRoot': True, 'runAsUser': 65532, 'runAsGroup': 65532, 'fsGroup': 65532},
        'volumes': volumes,
        'containers': [{
            'name': 'predictor', 'image': image, 'imagePullPolicy': 'Never',
            'command': ['/app/predictor'], 'args': args, 'volumeMounts': mounts,
            'ports': [{'name': 'http', 'containerPort': 8080}],
            'readinessProbe': {'httpGet': {'path': '/healthz', 'port': 'http'}},
            'resources': {'requests': {'cpu': '100m', 'memory': '64Mi'}, 'limits': {'cpu': '1', 'memory': '512Mi'}},
        }],
    }
    rendered_envoy = configure_envoy(envoy, bool(calibration_namespace))
    spec['template']['metadata']['annotations'] = {
        'epp-opt/serving-config-sha256': hashlib.sha256((rendered_envoy + config).encode()).hexdigest(),
    }
    predictor_metadata = {'labels': predictor_label}
    if calibration is not None:
        predictor_metadata['annotations'] = {
            'epp-opt/calibration-seed-sha256': hashlib.sha256(calibration.encode()).hexdigest(),
        }
    if calibration_namespace:
        envoy_container = next(c for c in pod['containers'] if c['name'] == 'envoy-proxy')
        envoy_container.setdefault('ports', []).extend(
            {'name': route, 'containerPort': port, 'protocol': 'TCP'}
            for route, port in CALIBRATION_PORTS.items()
        )
        resources.extend([
            {**resource('NetworkPolicy', 'networking.k8s.io/v1', CALIBRATION), 'spec': {
                'podSelector': {'matchLabels': label},
                'policyTypes': ['Ingress'],
                'ingress': [
                    {'ports': [{'protocol': 'TCP', 'port': port} for port in (8081, 19001, 9003, 9090)]},
                    {'from': [{
                        'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': calibration_namespace}},
                        'podSelector': {'matchLabels': {'epp-opt-calibration-client': 'true'}},
                    }], 'ports': [{'protocol': 'TCP', 'port': port} for port in CALIBRATION_PORTS.values()]},
                ],
            }},
            {**resource('Service', 'v1', CALIBRATION), 'spec': {
                'type': 'ClusterIP', 'selector': label,
                'ports': [{'name': route, 'port': port, 'targetPort': port}
                          for route, port in CALIBRATION_PORTS.items()],
            }},
        ])
    resources.extend([
        {**resource('ConfigMap', 'v1', 'epp-opt-envoy'), 'data': {'envoy.yaml': rendered_envoy}},
        {**resource('ConfigMap', 'v1', 'epp-opt-config'), 'data': {'epp-opt-benefit-plugins.yaml': config}},
        {**resource('PersistentVolumeClaim', 'v1', predictor), 'spec': {
            'accessModes': ['ReadWriteOnce'], 'resources': {'requests': {'storage': state_size}}}},
        {**resource('Deployment', 'apps/v1', predictor), 'spec': {
            'replicas': 1, 'strategy': {'type': 'Recreate'}, 'selector': {'matchLabels': predictor_label},
            'template': {'metadata': predictor_metadata, 'spec': predictor_pod}}},
        {**resource('Service', 'v1', predictor), 'spec': {
            'selector': predictor_label, 'ports': [{'name': 'http', 'port': 8080, 'targetPort': 'http'}]}},
        {**resource('Deployment', 'apps/v1', NAME), 'spec': spec},
        {**resource('Service', 'v1', NAME), 'spec': {'selector': label, 'ports': [{'name': 'http', 'port': 80, 'targetPort': 8081}]}},
    ])
    return dict(apiVersion='v1', kind='List', items=resources)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--deployment', type=Path, required=True)
    parser.add_argument('--envoy', type=Path, required=True)
    seed = parser.add_mutually_exclusive_group()
    seed.add_argument('--calibration', type=Path, help='optional observation JSONL stored in a ConfigMap')
    seed.add_argument('--calibration-pvc', help='existing PVC with observations.jsonl at the root')
    parser.add_argument('--calibration-namespace', help='enable fixed calibration ports for labeled clients in this namespace')
    parser.add_argument('--model', required=True)
    parser.add_argument('--runtime', required=True, help='identity of this model/backend configuration')
    parser.add_argument('--verified-pd', action='append', choices=('ap-nd', 'np-ad'), default=[])
    parser.add_argument('--state-size', default='1Gi')
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    calibration = args.calibration.read_text() if args.calibration else None
    print(json.dumps(render(json.loads(args.deployment.read_text()), args.envoy.read_text(), calibration,
                            args.image, args.model, args.runtime, args.verified_pd, args.state_size,
                            args.calibration_namespace, args.calibration_pvc), indent=2))


if __name__ == '__main__':
    main()
