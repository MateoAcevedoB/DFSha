from concurrent.futures import ThreadPoolExecutor
import httpx
import pytest
from dfsha.blocks import BLOCK_SIZE
from test_transfers import cluster

def test_different_blocks_merge_and_only_changes_uploaded(cluster):
    c,store,_,root,_=cluster
    source=root/'large';source.write_bytes(b'A'*BLOCK_SIZE+b'B'*BLOCK_SIZE+b'C'*5)
    first=c.put(source,'/file')
    a,b=c.open('/file','rw'),c.open('/file','rw')
    a.write(b'X');b.seek(BLOCK_SIZE);b.write(b'Y')
    with ThreadPoolExecutor(2) as p:
        results=list(p.map(lambda s:s.close(),[a,b]))
    assert all(r['blocks']==1 for r in results)
    c.get('/file',root/'out')
    actual=(root/'out').read_bytes()
    assert actual[0:1]==b'X' and actual[BLOCK_SIZE:BLOCK_SIZE+1]==b'Y'
    current=c.http.get('/files/download',params={'path':'/file'}).json()
    manifest,_=store.get('/dfsha/manifests/'+current['version'])
    original,_=store.get('/dfsha/manifests/'+first['version'])
    assert manifest['blocks'][2]['id']==original['blocks'][2]['id']

def test_failed_close_can_export_and_retry(cluster):
    c,_,offline,root,_=cluster
    src=root/'src';src.write_bytes(b'abcdef');c.put(src,'/f')
    session=c.open('/f','rw');session.write(b'new')
    offline.update(['dn1','dn2','dn3','dn4'])
    with pytest.raises(RuntimeError):session.close()
    session.save_local(root/'saved')
    assert (root/'saved').read_bytes()==b'newdef'
    offline.clear();session.close()
    c.get('/f',root/'result');assert (root/'result').read_bytes()==b'newdef'

def test_append_conflicts_when_size_changed(cluster):
    c,_,_,root,_=cluster
    src=root/'src';src.write_bytes(b'x');c.put(src,'/f')
    a,b=c.open('/f','a'),c.open('/f','a')
    a.write(b'a');b.write(b'b');a.close()
    with pytest.raises(httpx.HTTPStatusError) as e:b.close()
    assert e.value.response.status_code==409
    b.abort()

def test_commit_replay_does_not_resurrect_deleted_file(cluster):
    import hashlib
    c,_,_,_,_=cluster
    sid=c.http.post('/files/uploads',json={'path':'/replay','size':0}).json()['upload_id']
    body={'blocks':[],'sha256':hashlib.sha256(b'').hexdigest()}
    assert c.http.post(f'/files/uploads/{sid}/commit',json=body).status_code==200
    c.http.post('/fs/rm',json={'path':'/replay'}).raise_for_status()
    assert c.http.post(f'/files/uploads/{sid}/commit',json=body).status_code==200
    assert c.http.get('/files/download',params={'path':'/replay'}).status_code==404
