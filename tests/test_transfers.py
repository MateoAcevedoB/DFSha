import hashlib
from pathlib import Path
from urllib.parse import urlparse
import httpx
import pytest
from fastapi.testclient import TestClient
from dfsha.api import create_app
from dfsha.datanode import create_app as data_app
from dfsha.client import Client
from dfsha.blocks import BLOCK_SIZE, placement, verify
from test_api import MemoryStore, SECRET

@pytest.fixture
def cluster(tmp_path):
    store = MemoryStore()
    control = TestClient(create_app(store, SECRET))
    nodes = {}
    offline = set()
    async def sender(url, data, token):
        parsed = urlparse(url)
        if parsed.hostname in offline:
            raise httpx.ConnectError('offline')
        return nodes[parsed.hostname].put(parsed.path + '?' + parsed.query, content=data,
                                           headers={'Authorization': 'Bearer ' + token})
    for i in range(1, 5):
        name = f'dn{i}'
        nodes[name] = TestClient(data_app(tmp_path / name, name, SECRET, sender))
    def route(request):
        path = request.url.path
        if path.startswith('/data/'):
            _, _, name, *tail = path.split('/')
            if name in offline:
                raise httpx.ConnectError('offline')
            target, path = nodes[name], '/' + '/'.join(tail)
        else:
            target = control
        return target.request(request.method, path, params=request.url.params,
                              headers=dict(request.headers), content=request.read())
    client = Client('http://testserver', 'alice', 'test-password-long', True, httpx.MockTransport(route))
    yield client, store, offline, tmp_path, control
    client.close()

def test_multiblock_roundtrip_and_replica_failover(cluster):
    client, store, offline, root, _ = cluster
    data = b'abcd' * (BLOCK_SIZE // 2 + 123)
    source, dest = root / 'source.bin', root / 'dest.bin'
    source.write_bytes(data)
    result = client.put(source, '/data.bin')
    assert result['blocks'] == 3
    manifest, _ = store.get('/dfsha/manifests/' + result['version'])
    for block in manifest['blocks']:
        assert len(block['nodes']) == 3
        assert all((root / n / block['id']).exists() for n in block['nodes'])
        assert (root / block['nodes'][0] / block['id']).read_bytes() != data[:BLOCK_SIZE]
    offline.add(manifest['blocks'][0]['nodes'][0])
    client.get('/data.bin', dest)
    assert dest.read_bytes() == data

def test_degraded_write_and_corrupt_replica(cluster):
    client, store, offline, root, _ = cluster
    offline.add('dn1')
    source = root / 'source'
    source.write_bytes(b'content' * 100)
    result = client.put(source, '/test')
    m, _ = store.get('/dfsha/manifests/' + result['version'])
    block = m['blocks'][0]
    assert len(block['nodes']) >= 2
    (root / block['nodes'][0] / block['id']).write_bytes(b'corrupt')
    client.get('/test', root / 'out')
    assert (root / 'out').read_bytes() == source.read_bytes()

def test_failed_upload_is_invisible(cluster):
    client, _, offline, root, control = cluster
    offline.update(['dn1', 'dn2', 'dn3'])
    source = root / 'source'
    source.write_bytes(b'test')
    with pytest.raises(RuntimeError):
        client.put(source, '/incomplete')
    result = client.http.get('/fs/ls')
    assert result.json()['entries'] == []

def test_empty_replace_delete_and_isolation(cluster):
    client, _, _, root, control = cluster
    source = root / 'empty'
    source.write_bytes(b'')
    assert client.put(source, '/empty')['blocks'] == 0
    client.get('/empty', root / 'out')
    assert (root / 'out').read_bytes() == b''
    source.write_bytes(b'new')
    client.put(source, '/empty')
    client.get('/empty', root / 'out')
    assert (root / 'out').read_bytes() == b'new'
    from test_api import account
    bob = account(control, 'bobby')
    assert control.get('/files/download?path=/empty', headers=bob).status_code == 404
    assert client.http.post('/fs/rm', json={'path': '/empty'}).status_code == 200
    assert client.http.get('/files/download?path=/empty').status_code == 404

def test_concurrent_commit_and_idempotence(cluster):
    client, _, _, _, _ = cluster
    body = {'path': '/race', 'size': 0}
    a = client.http.post('/files/uploads', json=body).json()['upload_id']
    b = client.http.post('/files/uploads', json=body).json()['upload_id']
    commit = {'blocks': [], 'sha256': hashlib.sha256(b'').hexdigest()}
    assert client.http.post(f'/files/uploads/{a}/commit', json=commit).status_code == 200
    assert client.http.post(f'/files/uploads/{a}/commit', json=commit).status_code == 200
    assert client.http.post(f'/files/uploads/{b}/commit', json=commit).status_code == 409

def test_duplicate_receipt_rejected(cluster):
    client, _, _, root, _ = cluster
    from dfsha.blocks import sign
    sid = client.http.post('/files/uploads', json={'path': '/x', 'size': 1}).json()['upload_id']
    bid = 'a' * 64
    receipt = sign(SECRET, 'block-receipt', {'sid': sid, 'index': 0, 'id': bid, 'size': 29, 'node': placement(bid)[0]})
    body = {'blocks': [{'block_id': bid, 'receipts': [receipt, receipt]}], 'sha256': 'a'*64}
    assert client.http.post(f'/files/uploads/{sid}/commit', json=body).status_code == 409

def test_failed_download_preserves_destination(cluster):
    client, _, offline, root, _ = cluster
    source, dest = root / 'src', root / 'dst'
    source.write_bytes(b'new')
    dest.write_bytes(b'previous')
    client.put(source, '/x')
    offline.update(['dn1', 'dn2', 'dn3', 'dn4'])
    with pytest.raises(RuntimeError):
        client.get('/x', dest)
    assert dest.read_bytes() == b'previous'
