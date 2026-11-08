import os
import ssl
import hashlib
from pathlib import Path

def secret_value(name, fallback=None):
    path = os.getenv(name + '_FILE')
    return Path(path).read_text().strip() if path else os.getenv(name, fallback)

def wrapping_key(fallback):
    value = secret_value('FILE_MASTER_KEY')
    return bytes.fromhex(value) if value else hashlib.sha256(('dfsha-file-wrap-v1:'+fallback).encode()).digest()

def tls_context():
    ca = os.getenv('TLS_CA')
    if not ca: return True
    context = ssl.create_default_context(cafile=ca)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    cert, key = os.getenv('TLS_CERT'), os.getenv('TLS_KEY')
    if cert: context.load_cert_chain(cert,key)
    return context

def node_url(node):
    return ('https' if os.getenv('TLS_CA') else 'http') + f'://{node}:8000'
