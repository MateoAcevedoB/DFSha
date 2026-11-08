"""Genera PKI y secretos de laboratorio, sin sobrescribir claves existentes."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import ipaddress
import secrets
from cryptography import x509
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

root = Path(__file__).resolve().parents[1] / 'runtime'
root.mkdir(exist_ok=True)
root.chmod(0o700)
def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as f: f.write(data)
    if path.suffix in ('.key',): path.chmod(0o600 if 'ca' in path.parts else 0o644)
def key_bytes(key):
    return key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
ca_path = root/'ca/ca.key'
if ca_path.exists():
    ca_key = serialization.load_pem_private_key(ca_path.read_bytes(),None)
    ca = x509.load_pem_x509_certificate((root/'trust/ca.crt').read_bytes())
else:
    ca_key = rsa.generate_private_key(public_exponent=65537,key_size=3072)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'DFSha laboratorio CA')])
    now=datetime.now(timezone.utc)
    ca=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5))
        .not_valid_after(now+timedelta(days=365)).add_extension(x509.BasicConstraints(ca=True,path_length=0),True)
        .add_extension(x509.KeyUsage(False,False,False,False,False,True,True,None,None),True)
        .sign(ca_key,hashes.SHA256()))
    save(ca_path,key_bytes(ca_key)); save(root/'trust/ca.crt',ca.public_bytes(serialization.Encoding.PEM))
for role in ['gateway','cn1','cn2','cn3','dn1','dn2','dn3','dn4','repair','maintenance','client','etcd1','etcd2','etcd3']:
    if (root/f'tls/{role}/server.key').exists(): continue
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    names=[x509.DNSName(role),x509.DNSName('localhost'),x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]
    # etcd comprueba también la IP remota de peers: usar DNS del servicio,
    # no una SAN IP loopback que rechazaría las direcciones dinámicas de Docker.
    if role.startswith('etcd'): names=[x509.DNSName(role),x509.DNSName('localhost')]
    if role.startswith('cn'): names.append(x509.DNSName('control'))
    now=datetime.now(timezone.utc)
    cert=(x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,role)]))
        .issuer_name(ca.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=90))
        .add_extension(x509.BasicConstraints(ca=False,path_length=None),True)
        .add_extension(x509.SubjectAlternativeName(names),False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH,ExtendedKeyUsageOID.CLIENT_AUTH]),False)
        .sign(ca_key,hashes.SHA256()))
    save(root/f'tls/{role}/server.key',key_bytes(key)); save(root/f'tls/{role}/server.crt',cert.public_bytes(serialization.Encoding.PEM))
for name in ['jwt','capability','master','receipt/dn1','receipt/dn2','receipt/dn3','receipt/dn4']:
    path=root/f'secrets/{name}.key'
    if not path.exists(): save(path,secrets.token_hex(32).encode())
print('PKI preparada. Conservar runtime; no compartir claves ni subirlas a Git.')
