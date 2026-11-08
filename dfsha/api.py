"""Primer incremento: autenticación y espacio de nombres RF1."""
import hashlib
import hmac
import os
import re
import secrets
import time
from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
import jwt
from .store import EtcdStore, Unavailable


class Credentials(BaseModel):
    username: str = Field(pattern=r'^[a-zA-Z0-9_-]{3,32}$')
    password: str = Field(min_length=12, max_length=128)


class PathRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1024)


def canonical(path):
    if not path.startswith('/') or '\\' in path or any(ord(c) < 32 for c in path):
        raise HTTPException(400, 'Se requiere una ruta absoluta válida')
    pieces = path.split('/')
    if '..' in pieces or '.' in pieces:
        raise HTTPException(400, 'La API no admite segmentos . o ..')
    return '/' + '/'.join(p for p in pieces if p)


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt),
                          n=16384, r=8, p=1).hex()


def create_app(store=None, secret=None):
    from .security import secret_value
    secret = secret or secret_value('JWT_SECRET', '')
    if len(secret) < 32:
        raise RuntimeError('JWT_SECRET debe contener al menos 32 caracteres aleatorios')
    store = store or EtcdStore(os.getenv('ETCD_ENDPOINTS',
        'http://etcd1:2379,http://etcd2:2379,http://etcd3:2379').split(','))
    app = FastAPI(title='DFSha — incremento 1', version='0.1.0')
    bearer = HTTPBearer(auto_error=False)

    @app.exception_handler(Unavailable)
    async def unavailable(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=503, content={'detail': str(exc)})

    def current_user(auth: HTTPAuthorizationCredentials = Depends(bearer)):
        try:
            if auth is None:
                raise ValueError()
            payload = jwt.decode(auth.credentials, secret, algorithms=['HS256'],
                                 issuer='dfsha', audience='dfsha-client',
                                 options={'require': ['exp', 'iat', 'sub', 'iss', 'aud']})
            username = payload['sub']
            if not re.fullmatch(r'[a-zA-Z0-9_-]{3,32}', username):
                raise ValueError()
            return username
        except (jwt.PyJWTError, ValueError, TypeError):
            raise HTTPException(401, 'Token inválido o vencido')

    def namespace(user):
        value, rev = store.get('/dfsha/users/' + user)
        if value is None:
            raise HTTPException(401, 'Usuario inexistente')
        return value, rev

    @app.get('/health/live')
    def live():
        return {'status': 'alive', 'node': os.getenv('NODE_ID', 'cn')}

    @app.get('/health/ready')
    def ready():
        store.get('/dfsha/health')
        return {'status': 'ready'}

    @app.post('/auth/register', status_code=201)
    def register(data: Credentials):
        salt = secrets.token_hex(16)
        value = {'salt': salt, 'hash': password_hash(data.password, salt),
                 'entries': {'/': {'type': 'directory'}}}
        if not store.cas('/dfsha/users/' + data.username, 0, value):
            raise HTTPException(409, 'El usuario ya existe')
        return {'username': data.username}

    @app.post('/auth/login')
    def login(data: Credentials):
        value, _ = store.get('/dfsha/users/' + data.username)
        salt = value['salt'] if value else '00' * 16
        calculated = password_hash(data.password, salt)
        if not value or not hmac.compare_digest(calculated, value['hash']):
            raise HTTPException(401, 'Credenciales incorrectas')
        now = int(time.time())
        token = jwt.encode({'sub': data.username, 'iat': now, 'exp': now + 3600,
                            'iss': 'dfsha', 'aud': 'dfsha-client'}, secret, algorithm='HS256')
        return {'access_token': token, 'token_type': 'bearer', 'expires_in': 3600}

    @app.get('/fs/ls')
    def ls(path: str = '/', user=Depends(current_user)):
        path = canonical(path)
        value, _ = namespace(user)
        entries = value['entries']
        if path not in entries:
            raise HTTPException(404, 'Ruta inexistente')
        if entries[path]['type'] != 'directory':
            raise HTTPException(409, 'La ruta no es un directorio')
        prefix = path.rstrip('/') + '/'
        children = [{'name': p[len(prefix):], **v} for p, v in entries.items()
                    if p != path and p.startswith(prefix) and '/' not in p[len(prefix):]]
        return {'path': path, 'entries': sorted(children, key=lambda x: x['name'])}

    @app.post('/fs/cd')
    def cd(data: PathRequest, user=Depends(current_user)):
        ls(data.path, user)
        return {'path': canonical(data.path)}

    def mutate(operation, data, user):
        path = canonical(data.path)
        if path == '/':
            raise HTTPException(409, 'No se puede modificar la raíz')
        for _ in range(8):
            value, revision = namespace(user)
            entries = value['entries']
            if operation == 'mkdir':
                if path in entries:
                    raise HTTPException(409, 'La ruta ya existe')
                parent = path.rsplit('/', 1)[0] or '/'
                if parent not in entries:
                    raise HTTPException(404, 'El directorio padre no existe')
                if entries[parent]['type'] != 'directory':
                    raise HTTPException(409, 'El padre no es un directorio')
                if len(entries) >= 1000:
                    raise HTTPException(413, 'Límite de 1000 entradas del incremento 1')
                entries[path] = {'type': 'directory'}
            else:
                if path not in entries:
                    raise HTTPException(404, 'Ruta inexistente')
                expected = 'directory' if operation == 'rmdir' else 'file'
                if entries[path]['type'] != expected:
                    raise HTTPException(409, 'Tipo de entrada incorrecto')
                if any(p.startswith(path + '/') for p in entries):
                    raise HTTPException(409, 'El directorio no está vacío')
                del entries[path]
            # Evita valores demasiado grandes para etcd incluso con rutas largas.
            import json
            if len(json.dumps(value).encode()) > 512_000:
                raise HTTPException(413, 'Espacio de nombres demasiado grande para este incremento')
            if store.cas('/dfsha/users/' + user, revision, value):
                return {'path': path, 'operation': operation}
        raise HTTPException(409, 'Contención concurrente; vuelva a intentar')

    @app.post('/fs/mkdir', status_code=201)
    def mkdir(data: PathRequest, user=Depends(current_user)):
        return mutate('mkdir', data, user)

    @app.post('/fs/rmdir')
    def rmdir(data: PathRequest, user=Depends(current_user)):
        return mutate('rmdir', data, user)

    @app.post('/fs/rm')
    def rm(data: PathRequest, user=Depends(current_user)):
        return mutate('rm', data, user)

    from .transfers import install
    install(app, store, current_user, namespace, canonical, secret)
    app.title = 'DFSha — incremento 2'
    app.version = '0.2.0'
    return app
