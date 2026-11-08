"""Sesiones de carga, recibos W=2 y publicación atómica de manifiestos."""
import hashlib
import json
import secrets
import time
import uuid
from fastapi import Depends, HTTPException, Header
from pydantic import BaseModel, Field
from .blocks import BLOCK_SIZE, encode, decode, encrypt, decrypt, placement, sign, verify

class Start(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    size: int = Field(ge=0, le=2 * 1024**3)
    expected_version: str | None = Field(default=None, pattern='^[0-9a-f]{32}$')
    changed: list[int] | None = Field(default=None, max_length=512)

class Block(BaseModel):
    block_id: str = Field(pattern='^[0-9a-f]{64}$')
    receipts: list[str] = Field(max_length=3)
    index: int | None = Field(default=None, ge=0, le=511)
    plain_hash: str | None = Field(default=None, pattern='^[0-9a-f]{64}$')

class Commit(BaseModel):
    blocks: list[Block] = Field(max_length=512)
    sha256: str = Field(pattern='^[0-9a-f]{64}$')

def install(app, store, current_user, namespace, canonical, secret):
    from .security import wrapping_key
    master = wrapping_key(secret)

    def session(sid, user):
        if len(sid) != 32 or any(c not in '0123456789abcdef' for c in sid):
            raise HTTPException(404, 'Carga inexistente')
        value, _ = store.get('/dfsha/uploads/' + sid)
        if not value or value['owner'] != user:
            raise HTTPException(404, 'Carga inexistente')
        if value['expires'] < time.time():
            raise HTTPException(410, 'La sesión de carga venció')
        return value

    @app.post('/files/uploads', status_code=201)
    def start(data: Start, user=Depends(current_user)):
        path = canonical(data.path)
        ns, _ = namespace(user)
        parent = path.rsplit('/', 1)[0] or '/'
        if ns['entries'].get(parent, {}).get('type') != 'directory':
            raise HTTPException(409, 'El directorio padre no existe')
        old = ns['entries'].get(path)
        if old and old['type'] != 'file':
            raise HTTPException(409, 'La ruta es un directorio')
        base_manifest = None
        if data.changed is not None:
            if not data.expected_version or not old:
                raise HTTPException(409, 'La actualización parcial requiere una versión base')
            base_manifest, _ = store.get('/dfsha/manifests/' + data.expected_version)
            if not base_manifest or base_manifest['owner'] != user:
                raise HTTPException(409, 'La versión base no está disponible')
            count = (data.size + BLOCK_SIZE - 1) // BLOCK_SIZE
            if len(set(data.changed)) != len(data.changed) or any(i < 0 or i >= count for i in data.changed):
                raise HTTPException(400, 'Índices inválidos')
            if data.size < base_manifest['size']:
                raise HTTPException(400, 'Truncar requiere reemplazo completo')
            # Al extender, hay que enviar todos los bloques cuyo tamaño/contenido cambia.
            if data.size > base_manifest['size']:
                required = set(range(base_manifest['size'] // BLOCK_SIZE, count))
                if not required.issubset(data.changed):
                    raise HTTPException(400, 'Faltan bloques de la extensión')
        elif 'expected_version' in data.model_fields_set:
            if (old.get('manifest') if old else None) != data.expected_version:
                raise HTTPException(409, 'La versión de apertura cambió')
        sid = uuid.uuid4().hex
        key = secrets.token_bytes(32)
        value = {'owner': user, 'path': path, 'size': data.size, 'version': sid,
                 'base': old, 'base_manifest': data.expected_version,
                 'changed': data.changed, 'created': time.time(), 'expires': int(time.time()) + 3600,
                 'wrapped_key': encode(encrypt(master, key, sid.encode()))}
        if not store.cas('/dfsha/uploads/' + sid, 0, value):
            raise HTTPException(409, 'Reintente iniciar la carga')
        return {'upload_id': sid, 'key': encode(key), 'block_size': BLOCK_SIZE}

    @app.get('/files/uploads/{sid}/blocks/{index}')
    def plan(sid: str, index: int, block_id: str, user=Depends(current_user)):
        value = session(sid, user)
        count = (value['size'] + BLOCK_SIZE - 1) // BLOCK_SIZE
        if value.get('changed') is not None and index not in value['changed']:
            raise HTTPException(400, 'Bloque no declarado en esta escritura')
        if not 0 <= index < count or len(block_id) != 64 or any(c not in '0123456789abcdef' for c in block_id):
            raise HTTPException(400, 'Bloque inválido')
        size = min(BLOCK_SIZE, value['size'] - index * BLOCK_SIZE) + 28
        nodes = placement(block_id)
        token = sign(secret, 'block-write', {'sid': sid, 'index': index, 'id': block_id,
                    'size': size, 'nodes': nodes})
        return {'token': token, 'urls': [f'/data/{n}/blocks/{block_id}' for n in nodes]}

    @app.post('/files/uploads/{sid}/commit')
    def commit(sid: str, data: Commit, user=Depends(current_user)):
        value = session(sid, user)
        request_digest=hashlib.sha256(json.dumps(data.model_dump(),sort_keys=True).encode()).hexdigest()
        if value.get('result'):
            if value.get('request_digest')!=request_digest:raise HTTPException(409,'La sesión ya fue confirmada con otro contenido')
            return value['result']
        count = (value['size'] + BLOCK_SIZE - 1) // BLOCK_SIZE
        indices = list(range(count)) if value.get('changed') is None else sorted(value['changed'])
        if len(data.blocks) != len(indices):
            raise HTTPException(400, 'Número de bloques incorrecto')
        descriptors = []
        for index, block in zip(indices, data.blocks):
            if block.index is not None and block.index != index:
                raise HTTPException(400, 'Orden de bloques inválido')
            expected = min(BLOCK_SIZE, value['size'] - index * BLOCK_SIZE) + 28
            nodes = set()
            for receipt in block.receipts:
                try:
                    c = verify(secret, 'block-receipt', receipt)
                    if not (c['sid'] == sid and c['index'] == index and c['id'] == block.block_id
                            and c['size'] == expected and c['node'] in placement(block.block_id)):
                        raise ValueError()
                    nodes.add(c['node'])
                except Exception:
                    raise HTTPException(400, 'Recibo inválido o vencido')
            if len(nodes) < 2:
                raise HTTPException(409, 'Cada bloque requiere dos réplicas persistidas distintas')
            descriptors.append({'id': block.block_id, 'nodes': sorted(nodes), 'plain_hash': block.plain_hash,
                'crypto_version': sid, 'wrapped_key': value['wrapped_key'], 'size': expected - 28})
        for _ in range(8):
            upload_state,upload_rev=store.get('/dfsha/uploads/'+sid)
            if upload_state.get('result'):
                if upload_state.get('request_digest')!=request_digest:raise HTTPException(409,'Contenido de reintento distinto')
                return upload_state['result']
            ns, rev = namespace(user)
            old = ns['entries'].get(value['path'])
            if old and old.get('operation') == sid:
                return {'version': old['manifest'], 'size': old['size']}
            merged = descriptors
            final_hash = data.sha256
            if value.get('changed') is not None:
                if not old or old['type'] != 'file':
                    raise HTTPException(409, 'El archivo fue eliminado')
                base, _ = store.get('/dfsha/manifests/' + value['base_manifest'])
                current, _ = store.get('/dfsha/manifests/' + old['manifest'])
                if not base or not current or current['size'] != base['size']:
                    raise HTTPException(409, 'Cambió el tamaño o desapareció la base')
                for i in indices:
                    before = base['blocks'][i]['id'] if i < len(base['blocks']) else None
                    now = current['blocks'][i]['id'] if i < len(current['blocks']) else None
                    if now != before:
                        raise HTTPException(409, 'Otro escritor modificó el mismo bloque')
                merged = [dict(b, crypto_version=b.get('crypto_version', current['version']),
                    wrapped_key=b.get('wrapped_key', current['wrapped_key']),
                    size=b.get('size', min(BLOCK_SIZE, current['size']-i*BLOCK_SIZE)))
                    for i,b in enumerate(current['blocks'])]
                for i, desc in zip(indices, descriptors):
                    if i < len(merged): merged[i] = desc
                    else: merged.append(desc)
                # Una fusión no permite reutilizar el hash del snapshot local del escritor.
                final_hash = data.sha256 if old['manifest'] == base['version'] else None
            elif old != value['base']:
                raise HTTPException(409, 'Otro cliente modificó el archivo')
            parent = value['path'].rsplit('/', 1)[0] or '/'
            if ns['entries'].get(parent, {}).get('type') != 'directory':
                raise HTTPException(409, 'El directorio padre fue eliminado')
            if old is None and len(ns['entries']) >= 1000:
                raise HTTPException(413, 'Límite de entradas alcanzado')
            version = uuid.uuid4().hex if value.get('changed') is not None else sid
            manifest = {'version': version, 'owner': user, 'size': value['size'], 'sha256': final_hash,
                'created': time.time(), 'wrapped_key': value['wrapped_key'], 'key_version': sid, 'blocks': merged}
            # El hash de la lista ordenada protege la composición incluso tras una fusión.
            manifest['content_root'] = hashlib.sha256(json.dumps([(b['id'], b['size']) for b in merged]).encode()).hexdigest()
            mkey = '/dfsha/manifests/' + version
            if not store.cas(mkey, 0, manifest):
                existing, _ = store.get(mkey)
                if not existing or any(existing.get(k) != manifest[k] for k in ('blocks','size','sha256','owner')):
                    raise HTTPException(409, 'La sesión ya preparó otro contenido')
            ns['entries'][value['path']] = {'type': 'file', 'size': value['size'], 'manifest': version, 'operation': sid}
            if len(json.dumps(ns).encode()) > 512_000:
                raise HTTPException(413, 'Espacio de nombres demasiado grande')
            result={'version':version,'size':value['size']}
            upload_state.update(result=result,request_digest=request_digest)
            if store.publish('/dfsha/users/'+user,rev,ns,'/dfsha/uploads/'+sid,upload_rev,upload_state):
                return result
        raise HTTPException(409, 'Contención concurrente')

    @app.get('/files/download')
    def download(path: str, user=Depends(current_user)):
        ns, _ = namespace(user)
        entry = ns['entries'].get(canonical(path))
        if not entry or entry['type'] != 'file' or 'manifest' not in entry:
            raise HTTPException(404, 'Archivo inexistente')
        m, _ = store.get('/dfsha/manifests/' + entry['manifest'])
        if not m:
            raise HTTPException(503, 'Manifiesto no disponible')
        ticket = sign(secret, 'download', {'sub': user, 'version': m['version']}, 3600)
        store.cas('/dfsha/reads/' + uuid.uuid4().hex, 0, {'version': m['version'], 'expires': time.time()+3600})
        return {'version': m['version'], 'size': m['size'], 'sha256': m['sha256'],
                'content_root': m.get('content_root'),
                'count': len(m['blocks']), 'ticket': ticket,
                'key': encode(decrypt(master, decode(m['wrapped_key']), m.get('key_version',m['version']).encode()))}

    @app.get('/files/download/blocks/{index}')
    def locate(index: int, ticket: str = Header(alias='X-Download-Ticket'), user=Depends(current_user)):
        try:
            c = verify(secret, 'download', ticket)
            if c['sub'] != user:
                raise ValueError()
        except Exception:
            raise HTTPException(403, 'Autorización de lectura inválida')
        m, _ = store.get('/dfsha/manifests/' + c['version'])
        if not m or not 0 <= index < len(m['blocks']):
            raise HTTPException(404, 'Bloque inexistente')
        block = m['blocks'][index]
        locations, _ = store.get('/dfsha/locations/' + block['id'])
        nodes = list(dict.fromkeys((locations or {}).get('nodes', []) + block['nodes']))
        token = sign(secret, 'block-read', {'id': block['id'], 'nodes': nodes})
        return {'id': block['id'], 'token': token,
                'size': block.get('size', min(BLOCK_SIZE, m['size']-index*BLOCK_SIZE)),
                'plain_hash': block.get('plain_hash'), 'crypto_version': block.get('crypto_version', m['version']),
                'key': encode(decrypt(master, decode(block.get('wrapped_key',m['wrapped_key'])), block.get('crypto_version',m['version']).encode())),
                'urls': [f'/data/{n}/blocks/{block["id"]}' for n in nodes]}
