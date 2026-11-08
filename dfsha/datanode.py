"""Bloques persistidos y replicación S2S con recibos firmados."""
import hashlib
import os
from pathlib import Path
import tempfile
import time
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from .blocks import BLOCK_SIZE, NODES, sign, verify
from .security import tls_context, node_url, secret_value

def create_app(root=None, node=None, secret=None, sender=None):
    root = Path(root or os.getenv('DATA_DIR', '/data'))
    node = node or os.environ['NODE_ID']
    secret = secret or secret_value('CAPABILITY_SECRET', os.getenv('JWT_SECRET'))
    root.mkdir(parents=True, exist_ok=True)
    app = FastAPI(title='DFSha almacenamiento de bloques')

    def authorize(request, purpose, block_id):
        try:
            if len(block_id) != 64 or any(c not in '0123456789abcdef' for c in block_id):
                raise ValueError()
            token = request.headers.get('authorization', '').removeprefix('Bearer ')
            c = verify(secret, purpose, token)
            if c['id'] != block_id or node not in c['nodes'] or any(n not in NODES for n in c['nodes']):
                raise ValueError()
            return c, token
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning('Autorizacion de bloque rechazada: node=%s reason=%s', node, type(exc).__name__)
            raise HTTPException(403, 'Capacidad inválida para este bloque y nodo')

    @app.get('/health/live')
    def live():
        return {'node': node, 'status': 'alive', 'block_storage_implemented': True}

    @app.get('/admin/blocks')
    def inventory(request: Request, after: str = ''):
        try:
            c=verify(secret,'block-admin',request.headers.get('authorization','').removeprefix('Bearer '))
            if c.get('action') != 'inventory' or node not in c.get('nodes',[]): raise ValueError()
        except Exception: raise HTTPException(403,'Autorización de mantenimiento requerida')
        files=sorted(p for p in root.iterdir() if len(p.name)==64 and all(c in '0123456789abcdef' for c in p.name) and p.name>after)
        page=files[:500]
        return {'blocks':[{'id':p.name,'mtime':p.stat().st_mtime,'size':p.stat().st_size} for p in page],
                'next':page[-1].name if len(files)>500 else None}

    @app.delete('/blocks/{block_id}')
    def remove(block_id: str, request: Request):
        c,_=authorize(request,'block-admin',block_id)
        if c.get('action') != 'delete' or not isinstance(c.get('cutoff'),(int,float)):
            raise HTTPException(403,'Orden de borrado inválida')
        path=root/block_id
        if path.exists():
            if path.stat().st_mtime >= c['cutoff']:
                raise HTTPException(409,'Bloque demasiado reciente para recolectar')
            path.unlink()
        return {'deleted':block_id}

    @app.put('/blocks/{block_id}')
    async def put(block_id: str, request: Request, replica: bool = False):
        c, token = authorize(request, 'block-write', block_id)
        if not 28 < c['size'] <= BLOCK_SIZE + 28:
            raise HTTPException(400, 'Tamaño inválido')
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > c['size']:
                raise HTTPException(413, 'Bloque demasiado grande')
        if len(data) != c['size'] or hashlib.sha256(data).hexdigest() != block_id:
            raise HTTPException(400, 'Bloque incompleto o hash incorrecto')
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=root, delete=False) as f:
                temporary = f.name
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, root / block_id)
            if os.name != 'nt':
                fd = os.open(root, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
        receipts = [sign(secret, 'block-receipt', {'sid': c['sid'], 'index': c['index'],
                    'id': block_id, 'size': len(data), 'node': node}, 3600)]
        if not replica:
            async with httpx.AsyncClient(timeout=15, verify=tls_context()) as client:
                for target in c['nodes']:
                    if target == node:
                        continue
                    try:
                        url = node_url(target) + f'/blocks/{block_id}?replica=true'
                        if sender:
                            response = await sender(url, bytes(data), token)
                        else:
                            response = await client.put(url, content=bytes(data), headers={'Authorization': 'Bearer ' + token})
                        response.raise_for_status()
                        receipts.extend(response.json()['receipts'])
                    except (httpx.HTTPError, ValueError, KeyError):
                        continue
            if len(receipts) < 2:
                raise HTTPException(503, 'No se alcanzaron dos copias persistidas')
        return {'receipts': receipts}

    @app.get('/blocks/{block_id}')
    def get(block_id: str, request: Request):
        authorize(request, 'block-read', block_id)
        path = root / block_id
        if not path.exists():
            raise HTTPException(404, 'Bloque ausente')
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != block_id:
            raise HTTPException(503, 'Bloque corrupto; usar otra réplica')
        return Response(data, media_type='application/octet-stream')
    return app
