"""Generate Compose configurations for an AWS Academy deployment on two hosts."""

import argparse
import ipaddress
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('control_ip', type=ipaddress.IPv4Address)
    parser.add_argument('storage_ip', type=ipaddress.IPv4Address)
    args = parser.parse_args()
    if args.control_ip == args.storage_ip:
        parser.error('Supply the private IPs of two different EC2 instances')

    root = Path(__file__).resolve().parents[1]
    compose = json.loads((root / 'compose.yaml').read_text(encoding='utf-8'))
    services = compose['services']
    for service in services.values():
        if 'build' in service:
            service['image'] = 'dfsha-demo:local'
    remote_node = services.pop('dn4')
    local_nodes = {'dn1': 8101, 'dn2': 8102, 'dn3': 8103}
    for name in ('cn1', 'cn2', 'cn3', *local_nodes, 'repair', 'maintenance'):
        service = services[name]
        service['networks'].append('access')
        service['extra_hosts'] = {'dn4': str(args.storage_ip)}
    for name, port in local_nodes.items():
        services[name]['ports'] = [f'{args.control_ip}:{port}:8000']

    gateway = services['gateway']
    gateway['ports'] = ['127.0.0.1:19443:443']
    gateway['volumes'] = [
        volume.replace('./nginx.conf:', './runtime/nginx.aws-control.conf:')
        for volume in gateway['volumes']
    ]
    del compose['volumes']['dn4']

    remote_node.pop('depends_on')
    remote_node['environment'].pop('ETCD_ENDPOINTS')
    remote_node['environment'].update({
        f'DFSHA_PORT_{name.upper()}': str(port)
        for name, port in local_nodes.items()
    })
    remote_node['networks'] = ['access']
    remote_node['extra_hosts'] = dict.fromkeys(local_nodes, str(args.control_ip))
    remote_node['ports'] = [f'{args.storage_ip}:8000:8000']
    remote_node['restart'] = 'unless-stopped'
    storage = {
        'services': {'dn4': remote_node},
        'networks': {'access': {}},
        'volumes': {'dn4': {}},
    }

    runtime = root / 'runtime'
    runtime.mkdir(exist_ok=True)
    nginx = (root / 'nginx.conf').read_text(encoding='utf-8')
    nginx = nginx.replace('set $target_dn dn4:8000;', f'set $target_dn {args.storage_ip}:8000;')
    (runtime / 'nginx.aws-control.conf').write_text(nginx, encoding='utf-8')
    for name, config in (('control', compose), ('storage', storage)):
        output = runtime / f'compose.aws-{name}.json'
        output.write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
        print(f'Created {output.relative_to(root)}')


if __name__ == '__main__':
    main()
