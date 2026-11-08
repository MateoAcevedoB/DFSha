import base64
import hashlib
import os
import time
import jwt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

BLOCK_SIZE = 4 * 1024 * 1024
NODES = ['dn1', 'dn2', 'dn3', 'dn4']

def encode(data):
    return base64.b64encode(data).decode()

def decode(data):
    return base64.b64decode(data, validate=True)

def aad(version, index):
    return f'dfsha:v1:{version}:{index}'.encode()

def encrypt(key, data, context):
    nonce = os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, data, context)

def decrypt(key, data, context):
    return AESGCM(key).decrypt(data[:12], data[12:], context)

def placement(block_id):
    return sorted(NODES, key=lambda n: hashlib.sha256((block_id+n).encode()).digest(), reverse=True)[:3]

def sign(secret, purpose, values, ttl=60):
    secret = purpose_key(secret, purpose, values)
    now = int(time.time())
    return jwt.encode({**values, 'iat': now, 'exp': now+ttl, 'iss': 'dfsha', 'aud': purpose}, secret, algorithm='HS256')

def verify(secret, purpose, token):
    hints = jwt.decode(token, options={'verify_signature': False})
    secret = purpose_key(secret, purpose, hints)
    # Tolerancia acotada al desfase de reloj entre emisor y receptor.
    return jwt.decode(token, secret, algorithms=['HS256'], audience=purpose, issuer='dfsha', leeway=5,
                      options={'require': ['iat', 'exp', 'iss', 'aud']})

def purpose_key(fallback, purpose, claims):
    from .security import secret_value
    from pathlib import Path
    if purpose == 'block-receipt' and os.getenv('RECEIPT_KEYS_DIR'):
        node = claims.get('node')
        if node not in NODES: raise ValueError('Nodo inválido')
        return (Path(os.environ['RECEIPT_KEYS_DIR']) / (node+'.key')).read_text().strip()
    if purpose in ('block-read','block-write','block-admin'):
        return secret_value('CAPABILITY_SECRET', fallback)
    return fallback
