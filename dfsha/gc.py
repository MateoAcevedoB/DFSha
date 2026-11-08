"""GC conservador en ventana de mantenimiento; nunca ejecutar con escritores activos."""
import argparse
import json
import os
import time
import httpx
from .store import EtcdStore
from .security import tls_context, node_url, secret_value
from .blocks import NODES, sign

def retention(store, now, grace=7200):
    if grace < 7200: raise ValueError('La gracia debe cubrir sesiones y capacidades')
    roots=set()
    for _, ns, _ in store.scan('/dfsha/users/'):
        roots.update(e['manifest'] for e in ns['entries'].values() if 'manifest' in e)
    expired=[]
    for key,pin,_ in store.scan('/dfsha/reads/'):
        if pin['expires'] >= now: roots.add(pin['version'])
        else: expired.append(key)
    for key,u,_ in store.scan('/dfsha/uploads/'):
        if u['expires'] >= now:
            roots.add(u['version'])
            if u.get('base_manifest'): roots.add(u['base_manifest'])
            if u.get('base') and u['base'].get('manifest'): roots.add(u['base']['manifest'])
        else: expired.append(key)
    keep=set(); obsolete=[]
    for key,m,_ in store.scan('/dfsha/manifests/'):
        if m['version'] in roots or m.get('created',now) >= now-grace:
            keep.update(b['id'] for b in m['blocks'])
        else: obsolete.append(key)
    return keep,obsolete,expired

def collect(store, secret, http, execute=False, now=None):
    now=time.time() if now is None else now
    keep, obsolete, expired=retention(store,now)
    candidates=[]
    for node in NODES:
        after=''
        while True:
            token=sign(secret,'block-admin',{'nodes':[node],'action':'inventory'})
            r=http.get(node_url(node)+'/admin/blocks',params={'after':after},headers={'Authorization':'Bearer '+token})
            r.raise_for_status()
            page=r.json()
            for b in page['blocks']:
                if b['id'] not in keep and b['mtime'] < now-7200:
                    candidates.append({'node':node,**b})
            after=page.get('next')
            if not after:break
    report={'mode':'execute' if execute else 'dry-run','retained_blocks':len(keep),
        'obsolete_manifests':len(obsolete),'expired_records':len(expired),
        'copies_to_delete':len(candidates),'bytes_to_free':sum(b['size'] for b in candidates)}
    if execute:
        for key in obsolete+expired:store.delete(key)
        for b in candidates:
            token=sign(secret,'block-admin',{'nodes':[b['node']],'id':b['id'],'action':'delete','cutoff':now-7200})
            r=http.delete(node_url(b['node'])+'/blocks/'+b['id'],headers={'Authorization':'Bearer '+token})
            r.raise_for_status()
        for bid in {b['id'] for b in candidates}:store.delete('/dfsha/locations/'+bid)
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--writers-stopped',action='store_true')
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    if not args.writers_stopped:parser.error('Usar exclusivamente scripts/limpiar_bloques.ps1, que detiene escritores y reparación')
    store=EtcdStore(os.environ['ETCD_ENDPOINTS'].split(','))
    with httpx.Client(verify=tls_context(),timeout=30) as http:
        print(json.dumps(collect(store,secret_value('CAPABILITY_SECRET'),http,args.execute),indent=2))
