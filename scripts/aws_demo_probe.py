"""Record an integrity comparison before and after stopping EC2 B."""

import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile

import httpx

from dfsha.client import Client
from dfsha.security import tls_context


url = 'https://gateway'
mode = sys.argv[1]
state_path = Path('/tmp/dfsha-two-ec2-proof.json')
with tempfile.TemporaryDirectory() as directory:
    directory = Path(directory)
    if mode == 'before':
        response = httpx.get(url + '/data/dn4/health/live', verify=tls_context(), timeout=10)
        response.raise_for_status()
        if response.json()['node'] != 'dn4':
            raise RuntimeError('Unexpected storage node identity')
        print('DN4_HEALTH_BEFORE', response.json())
        username, password = 'demo_' + secrets.token_hex(6), secrets.token_urlsafe(24)
        source = directory / 'original.bin'
        source.write_bytes(os.urandom(32 * 1024**2))
        expected = hashlib.file_digest(source.open('rb'), 'sha256').hexdigest()
        with httpx.Client(verify=tls_context()):
            client = Client(url, username, password, register=True)
            try:
                for attempt in range(1, 4):
                    path = f'/two-ec2-{attempt}.bin'
                    client.put(source, path)
                    manifest = client.http.get('/files/download', params={'path': path})
                    manifest.raise_for_status()
                    snapshot = manifest.json()
                    remote_blocks = []
                    for index in range(snapshot['count']):
                        plan = client.http.get(f'/files/download/blocks/{index}',
                                               headers={'X-Download-Ticket': snapshot['ticket']})
                        plan.raise_for_status()
                        block = plan.json()
                        if any(link.startswith('/data/dn4/') for link in block['urls']):
                            remote_blocks.append(block['id'])
                    if remote_blocks:
                        break
                else:
                    raise RuntimeError('No confirmed blocks were found on DN4')
                result = client.get(path, directory / 'download-before.bin')
                if result['sha256'] != expected:
                    raise RuntimeError('Before-failure hash mismatch')
            finally:
                client.close()
        descriptor = os.open(state_path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, 'w') as state:
            json.dump({'username': username, 'password': password, 'path': path,
                       'sha256': expected}, state)
        print('FILE', path, 'BYTES', result['size'], 'BLOCKS', result['blocks'])
        print('SHA256_ORIGINAL', expected)
        print('SHA256_BEFORE', result['sha256'])
        print('BLOCKS_CONFIRMED_ON_DN4', ' '.join(remote_blocks))
    elif mode == 'after':
        state = json.loads(state_path.read_text())
        client = Client(url, state['username'], state['password'])
        try:
            result = client.get(state['path'], directory / 'download-after.bin')
            if result['sha256'] != state['sha256']:
                raise RuntimeError('After-failure hash mismatch')
            print('FILE', state['path'], 'BYTES', result['size'], 'BLOCKS', result['blocks'])
            print('SHA256_AFTER', result['sha256'])
            print('MATCH', result['sha256'] == state['sha256'])
        finally:
            client.close()
    else:
        raise ValueError(f'Unknown probe phase {mode}')
