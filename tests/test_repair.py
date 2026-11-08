import httpx
import pytest
from dfsha.repair import Repairer
from dfsha.blocks import placement
from test_transfers import cluster
from test_api import SECRET

def worker_for(cluster, delay=60):
    client, store, offline, root, control = cluster
    def scan(prefix):
        for key in sorted(store.data):
            if key.startswith(prefix):
                value, rev = store.get(key)
                yield key, value, rev
    store.scan = scan
    def route(request):
        url = f'/data/{request.url.host}{request.url.path}'
        return client.http.request(request.method, url, params=request.url.params,
            headers=dict(request.headers), content=request.read())
    clock = [0]
    worker = Repairer(store, SECRET, httpx.Client(transport=httpx.MockTransport(route)), delay, lambda: clock[0])
    return worker, clock

def upload(cluster):
    client, store, _, root, _ = cluster
    source = root / 'repair-source'
    source.write_bytes(b'repair-test' * 100)
    result = client.put(source, '/repair')
    manifest, _ = store.get('/dfsha/manifests/' + result['version'])
    return manifest, manifest['blocks'][0]['id']

def test_repair_to_spare_after_delay_and_download(cluster):
    client, store, offline, root, _ = cluster
    manifest, bid = upload(cluster)
    before = store.get('/dfsha/manifests/' + manifest['version'])
    failed = manifest['blocks'][0]['nodes'][0]
    offline.add(failed)
    worker, clock = worker_for(cluster)
    assert worker.cycle()['repaired'] == 0
    clock[0] = 59
    assert worker.cycle()['repaired'] == 0
    clock[0] = 61
    assert worker.cycle()['repaired'] == 1
    locations, _ = store.get('/dfsha/locations/' + bid)
    assert len(locations['nodes']) == 3 and failed not in locations['nodes']
    spare = next(n for n in locations['nodes'] if n not in placement(bid))
    assert (root / spare / bid).exists()
    assert store.get('/dfsha/manifests/' + manifest['version']) == before
    offline.update(n for n in ['dn1','dn2','dn3','dn4'] if n != spare)
    client.get('/repair', root / 'restored')
    assert (root / 'restored').read_bytes() == (root / 'repair-source').read_bytes()

def test_corrupt_copy_repaired_and_repeat_is_idempotent(cluster):
    _, store, _, root, _ = cluster
    m, bid = upload(cluster)
    corrupt = root / m['blocks'][0]['nodes'][0] / bid
    corrupt.write_bytes(b'bad')
    worker, clock = worker_for(cluster, delay=0)
    assert worker.cycle()['repaired'] == 1
    assert worker.cycle()['repaired'] == 0

def test_no_source_never_claims_recovery(cluster):
    _, store, offline, root, _ = cluster
    _, bid = upload(cluster)
    offline.update(['dn1','dn2','dn3','dn4'])
    worker, _ = worker_for(cluster, delay=0)
    result = worker.cycle()
    assert result['unrecoverable'] == 1 and result['repaired'] == 0
    assert store.get('/dfsha/locations/' + bid)[0]['nodes'] == []

def test_short_outage_does_not_copy(cluster):
    _, _, offline, _, _ = cluster
    m, _ = upload(cluster)
    offline.add(m['blocks'][0]['nodes'][0])
    worker, clock = worker_for(cluster)
    assert worker.cycle()['waiting'] == 1
    offline.clear()
    clock[0] = 30
    assert worker.cycle()['repaired'] == 0
    assert worker.missing_since == {}
