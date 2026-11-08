from dfsha.gc import retention,collect
from test_api import MemoryStore
import httpx
import pytest

def fixture_store():
    s=MemoryStore()
    s.scan=lambda prefix: ((k,*s.get(k)) for k in sorted(s.data) if k.startswith(prefix))
    s.delete=lambda k:s.data.pop(k,None)
    def put(k,v):s.cas(k,0,v)
    put('/dfsha/users/alice',{'entries':{'/f':{'manifest':'live'}}})
    for name in ['live','reader','upload','old','recent']:
        put('/dfsha/manifests/'+name,{'version':name,'created':9900 if name=='recent' else 1,'blocks':[{'id':name}]})
    put('/dfsha/reads/r',{'version':'reader','expires':11000})
    put('/dfsha/uploads/u',{'version':'upload','expires':11000,'base_manifest':'live'})
    return s

def test_retention_protects_readers_uploads_current_and_grace():
    s=fixture_store();keep,dead,_=retention(s,10000)
    assert keep=={'live','reader','upload','recent'}
    assert dead==['/dfsha/manifests/old']

def test_gc_dryrun_and_execute():
    s=fixture_store();deleted=[]
    def route(r):
        if r.method=='GET':return httpx.Response(200,json={'blocks':[{'id':'old','mtime':1,'size':50},{'id':'live','mtime':1,'size':20}],'next':None})
        deleted.append(str(r.url));return httpx.Response(200,json={'ok':True})
    with httpx.Client(transport=httpx.MockTransport(route)) as h:
        plan=collect(s,'x'*32,h,False,10000)
        assert plan['copies_to_delete']==4 and not deleted
        collect(s,'x'*32,h,True,10000)
        assert len(deleted)==4 and s.get('/dfsha/manifests/old')[0] is None
        assert s.get('/dfsha/manifests/live')[0]

def test_inventory_failure_aborts_before_any_delete():
    s=fixture_store()
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(503))) as h:
        with pytest.raises(httpx.HTTPStatusError):collect(s,'x'*32,h,True,10000)
    assert s.get('/dfsha/manifests/old')[0]

def test_dn_delete_requires_authorization_and_age(tmp_path):
    import hashlib,os,time
    from fastapi.testclient import TestClient
    from dfsha.datanode import create_app
    from dfsha.blocks import sign
    secret='z'*48;data=b'orphan';bid=hashlib.sha256(data).hexdigest()
    path=tmp_path/bid;path.write_bytes(data)
    c=TestClient(create_app(tmp_path,'dn1',secret))
    assert c.delete('/blocks/'+bid).status_code==403
    cutoff=time.time()-7200
    token=sign(secret,'block-admin',{'id':bid,'nodes':['dn1'],'action':'delete','cutoff':cutoff})
    headers={'Authorization':'Bearer '+token}
    assert c.delete('/blocks/'+bid,headers=headers).status_code==409
    os.utime(path,(cutoff-10,cutoff-10))
    assert c.delete('/blocks/'+bid,headers=headers).status_code==200
    assert not path.exists()
