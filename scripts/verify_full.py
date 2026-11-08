"""Evidencia real TLS, RF3 y archivo grande. Ejecutar con el clúster activo."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import secrets
import ssl
import shutil
import tempfile
import time
import httpx
from dfsha.client import Client
from dfsha.blocks import BLOCK_SIZE
from dfsha.security import tls_context

p=argparse.ArgumentParser();p.add_argument('--large-mib',type=int,default=1024);a=p.parse_args()
url=os.getenv('DFSHA_URL','https://gateway')
if shutil.disk_usage('/tmp').free < a.large_mib*1024**2*8:
    raise RuntimeError('Espacio insuficiente: reservar al menos ocho veces el tamaño de prueba para copias, temporales y mediciones')
user='full_'+secrets.token_hex(6);password=secrets.token_urlsafe(24)
result={'large_mib':a.large_mib,'environment':'Docker local, red TLS/mTLS'}
c=Client(url,user,password,True)
try:
    for endpoint in ['https://cn1:8000/health/ready','https://dn1:8000/health/live','https://etcd1:2379/health']:
        context=ssl.create_default_context(cafile=os.environ['TLS_CA'])
        try: httpx.get(endpoint,verify=context,timeout=8).raise_for_status()
        except httpx.HTTPError: pass
        else: raise RuntimeError('Se aceptó conexión interna sin certificado: '+endpoint)
    no_auth=httpx.get(url+'/fs/ls',verify=tls_context())
    if no_auth.status_code!=401:raise RuntimeError('Acceso sin token no rechazado')
    result['mtls_without_certificate_rejected']=True
    with tempfile.TemporaryDirectory() as td:
        td=Path(td);source=td/'source';dest=td/'dest'
        source.write_bytes(b'A'*BLOCK_SIZE+b'B'*BLOCK_SIZE)
        c.put(source,'/concurrente')
        first,second=c.open('/concurrente','rw'),c.open('/concurrente','rw')
        first.write(b'X');second.seek(BLOCK_SIZE);second.write(b'Y')
        with ThreadPoolExecutor(2) as pool:list(pool.map(lambda f:f.close(),[first,second]))
        c.get('/concurrente',dest)
        data=dest.read_bytes()
        if data[0:1]!=b'X' or data[BLOCK_SIZE:BLOCK_SIZE+1]!=b'Y':raise RuntimeError('Se perdieron cambios')
        first,second=c.open('/concurrente','rw'),c.open('/concurrente','rw')
        first.write(b'1');second.write(b'2');first.close()
        try: second.close()
        except httpx.HTTPStatusError as e:
            if e.response.status_code!=409:raise
            second.save_local(td/'conflict-copy');second.abort()
        else:raise RuntimeError('No se detectó conflicto de bloque')
        result['rf3_disjoint_merge_and_same_block_conflict']=True
        with source.open('wb') as f:
            for _ in range(a.large_mib):f.write(os.urandom(1024*1024))
        print(f'Iniciando subida de {a.large_mib} MiB con TLS y replicas...',flush=True)
        start=time.perf_counter();sent=c.put(source,'/grande');uploaded=time.perf_counter()
        print(f'Subida completa en {uploaded-start:.2f} s; verificando descarga...',flush=True)
        received=c.get('/grande',dest);finished=time.perf_counter()
        if sent['sha256']!=received['sha256']:raise RuntimeError('Hash del archivo grande distinto')
        result.update({'bytes':source.stat().st_size,'blocks':sent['blocks'],'sha256':sent['sha256'],
            'put_s':uploaded-start,'get_s':finished-uploaded,'verified':True})
    target=Path('/tmp/dfsha-evidence');target.mkdir(exist_ok=True)
    (target/'full.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
finally:c.close()
