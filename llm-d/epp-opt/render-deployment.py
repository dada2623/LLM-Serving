#!/usr/bin/env python3
"""Render a separate epp-opt Deployment and Service from the running router template."""
import argparse
import copy
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
NAME = 'epp-opt'


def configure_envoy(text):
    strip = '                        - remove: x-gateway-destination-endpoint\n'
    route_start = '''                      routes:
                        - match:
                            prefix: "/"
                            # EPP sets this header'''
    cluster_start = '    - name: original_destination_cluster\n'
    for anchor in (strip, route_start, cluster_start):
        if text.count(anchor) != 1:
            raise ValueError('Unexpected Envoy layout. Use the llm-serving-epp router template.')
    text = text.replace(strip, strip + '''                        - remove: x-llm-d-prefiller-host-port
                        - remove: x-prefiller-host-port
''')
    route = '''                      routes:
                        - match:
                            prefix: "/"
                            headers:
                              - name: x-gateway-destination-endpoint
                                present_match: true
                              - name: x-llm-d-prefiller-host-port
                                present_match: true
                          route:
                            cluster: epp_opt_pd
                            timeout: 86400s
                            idle_timeout: 86400s
                          typed_per_filter_config:
                            envoy.filters.http.ext_proc:
                              "@type": type.googleapis.com/envoy.config.route.v3.FilterConfig
                              config: {}
'''
    text = text.replace(route_start, route + route_start.split('\n', 1)[1])
    text = text.replace('failure_mode_allow: true', 'failure_mode_allow: false')
    cluster = '''    - name: epp_opt_pd
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
                      port_value: 18085
'''
    return text.replace(cluster_start, cluster + cluster_start)


def render(source, envoy, profiles, image):
    namespace = source['metadata']['namespace']
    label = {'app.kubernetes.io/name': NAME}
    def resource(kind, version, name):
        return dict(apiVersion=version, kind=kind, metadata=dict(name=name, namespace=namespace))
    spec = copy.deepcopy(source['spec'])
    spec['selector'] = {'matchLabels': label}
    spec['strategy'] = {'type': 'RollingUpdate', 'rollingUpdate': {'maxSurge': 1, 'maxUnavailable': 0}}
    spec['template']['metadata'] = {'labels': label}
    pod = spec['template']['spec']
    for volume in pod['volumes']:
        if volume['name'] == 'config':
            volume['configMap']['name'] = 'epp-opt-envoy'
        elif volume['name'] == 'plugins-config-volume':
            volume['configMap']['name'] = 'epp-opt-config'
    pod['volumes'].append({'name': 'calibration', 'configMap': {'name': 'epp-opt-calibration'}})
    epp = next(c for c in pod['containers'] if c['name'] == 'epp')
    epp.update(image=image, imagePullPolicy='Never')
    epp['args'].append('--allow-experimental-plugins')
    epp['args'][epp['args'].index('--config-file') + 1] = '/config/epp-opt-plugins.yaml'
    epp['volumeMounts'].append({'name': 'calibration', 'mountPath': '/calibration', 'readOnly': True})
    pod['containers'].append({
        'name': 'pd-proxy', 'image': image, 'imagePullPolicy': 'Never',
        'command': ['/app/pd-sidecar'], 'args': [
            '--port=18085', '--kv-connector=nixlv2', '--secure-serving=false',
            '--enable-ssrf-protection', f'--inference-pool={namespace}/llm-serving',
        ],
        'env': [{'name': 'EPP_OPT_DECODER_URL', 'value': f'http://epp-opt-nvidia.{namespace}.svc.cluster.local:8000'}],
        'readinessProbe': {'tcpSocket': {'port': 18085}, 'periodSeconds': 2},
        'resources': {'requests': {'cpu': '100m', 'memory': '64Mi'}, 'limits': {'cpu': '1', 'memory': '512Mi'}},
    })
    return dict(apiVersion='v1', kind='List', items=[
        {**resource('ConfigMap', 'v1', 'epp-opt-envoy'), 'data': {'envoy.yaml': configure_envoy(envoy)}},
        {**resource('ConfigMap', 'v1', 'epp-opt-config'), 'data': {'epp-opt-plugins.yaml': (ROOT / 'epp-opt-plugins.yaml').read_text()}},
        {**resource('ConfigMap', 'v1', 'epp-opt-calibration'), 'data': {'profiles.json': profiles}},
        {**resource('Service', 'v1', 'epp-opt-nvidia'), 'spec': {
            'selector': {'app.kubernetes.io/name': 'vllm-nvidia'}, 'ports': [{'port': 8000}]}},
        {**resource('Deployment', 'apps/v1', NAME), 'spec': spec},
        {**resource('Service', 'v1', NAME), 'spec': {'selector': label, 'ports': [{'name': 'http', 'port': 80, 'targetPort': 8081}]}},
    ])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--deployment', type=Path, required=True)
    parser.add_argument('--envoy', type=Path, required=True)
    parser.add_argument('--profiles', type=Path, required=True)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    print(json.dumps(render(json.loads(args.deployment.read_text()), args.envoy.read_text(), args.profiles.read_text(), args.image), indent=2))


if __name__ == '__main__':
    main()
