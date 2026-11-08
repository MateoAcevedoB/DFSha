"""Cliente RF2: cifra por bloques y conserva el destino si falla la descarga."""
import argparse
import getpass
import hashlib
import os
from pathlib import Path
import tempfile
import json
import httpx
from cryptography.exceptions import InvalidTag
from .blocks import BLOCK_SIZE, aad, encrypt, decrypt, decode

class Client:
    def __init__(self, url, username, password, register=False, transport=None):
        from .security import tls_context
        self.http = httpx.Client(base_url=url, timeout=60, transport=transport, verify=tls_context())
        credentials = {'username': username, 'password': password}
        if register:
            self.http.post('/auth/register', json=credentials).raise_for_status()
        result = self.http.post('/auth/login', json=credentials)
        result.raise_for_status()
        self.http.headers['Authorization'] = 'Bearer ' + result.json()['access_token']

    def close(self):
        self.http.close()

    def open(self, remote, mode="r"):
        from .sessions import FileSession
        return FileSession(self, remote, mode)

    def put(self, local, remote, *, expected_version=..., changed=None):
        source = Path(local)
        size = source.stat().st_size
        body = {'path': remote, 'size': size}
        if expected_version is not ...:
            body['expected_version'] = expected_version
        if changed is not None:
            body['changed'] = sorted(changed)
        response = self.http.post('/files/uploads', json=body)
        response.raise_for_status()
        upload = response.json()
        sid, key = upload['upload_id'], decode(upload['key'])
        blocks, digest, total = [], hashlib.sha256(), 0
        with source.open('rb') as f:
            for index in range((size + BLOCK_SIZE - 1) // BLOCK_SIZE):
                plain = f.read(BLOCK_SIZE)
                total += len(plain)
                digest.update(plain)
                if changed is not None and index not in changed:
                    continue
                data = encrypt(key, plain, aad(sid, index))
                block_id = hashlib.sha256(data).hexdigest()
                response = self.http.get(f'/files/uploads/{sid}/blocks/{index}', params={'block_id': block_id})
                response.raise_for_status()
                plan = response.json()
                receipt = None
                for url in plan['urls']:
                    try:
                        response = self.http.put(url, content=data, headers={'Authorization': 'Bearer ' + plan['token']})
                        response.raise_for_status()
                        receipt = response.json()['receipts']
                        break
                    except httpx.HTTPError:
                        continue
                if receipt is None:
                    raise RuntimeError('No se pudo replicar el bloque; archivo no publicado')
                blocks.append({'block_id': block_id, 'receipts': receipt, 'index': index,
                               'plain_hash': hashlib.sha256(plain).hexdigest()})
            if f.read(1) or total != size:
                raise RuntimeError('El tamaño del archivo cambió durante la carga')
        response = self.http.post(f'/files/uploads/{sid}/commit', json={'blocks': blocks, 'sha256': digest.hexdigest()})
        response.raise_for_status()
        return {**response.json(), 'sha256': digest.hexdigest(), 'blocks': len(blocks)}

    def get(self, remote, local, *, snapshot=None):
        if snapshot is None:
            response = self.http.get('/files/download', params={'path': remote})
            response.raise_for_status()
            snapshot = response.json()
        m = snapshot
        key = decode(m['key'])
        destination = Path(local)
        temporary = None
        digest, total = hashlib.sha256(), 0
        composition = []
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as f:
                temporary = f.name
                for index in range(m['count']):
                    response = self.http.get(f'/files/download/blocks/{index}', headers={'X-Download-Ticket': m['ticket']})
                    response.raise_for_status()
                    plan = response.json()
                    composition.append((plan['id'], plan.get('size', min(BLOCK_SIZE,m['size']-index*BLOCK_SIZE))))
                    plain = None
                    for url in plan['urls']:
                        try:
                            response = self.http.get(url, headers={'Authorization': 'Bearer ' + plan['token']})
                            response.raise_for_status()
                            if hashlib.sha256(response.content).hexdigest() != plan['id']:
                                continue
                            plain = decrypt(decode(plan['key']) if 'key' in plan else key, response.content,
                                            aad(plan.get('crypto_version',m['version']), index))
                            if plan.get('plain_hash') and hashlib.sha256(plain).hexdigest() != plan['plain_hash']:
                                plain = None
                                continue
                            break
                        except (httpx.HTTPError, InvalidTag, ValueError):
                            continue
                    if plain is None:
                        raise RuntimeError('No hay réplica válida para el bloque')
                    f.write(plain)
                    digest.update(plain)
                    total += len(plain)
                root = hashlib.sha256(json.dumps(composition).encode()).hexdigest()
                if (total != m['size'] or (m['sha256'] and digest.hexdigest() != m['sha256'])
                        or (m.get('content_root') and root != m['content_root'])):
                    raise RuntimeError('El archivo descargado no supera la verificación')
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
        return {'size': total, 'sha256': digest.hexdigest(), 'blocks': m['count']}

def main():
    parser = argparse.ArgumentParser(description='Cliente DFSha')
    parser.add_argument('--url', default=os.getenv('DFSHA_URL', 'https://127.0.0.1:18443'))
    parser.add_argument('--user', required=True)
    parser.add_argument('--register', action='store_true')
    parser.add_argument('operation', choices=['put', 'get'])
    parser.add_argument('source')
    parser.add_argument('destination')
    args = parser.parse_args()
    password = getpass.getpass('Contraseña: ')
    client = Client(args.url, args.user, password, args.register)
    try:
        print(getattr(client, args.operation)(args.source, args.destination))
    finally:
        client.close()

if __name__ == '__main__':
    main()
