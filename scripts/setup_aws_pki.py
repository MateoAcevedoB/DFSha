"""Genera PKI y certificados para despliegue en AWS Academy,
incluyendo en el Subject Alternative Name (SAN) del Gateway la IP pública,
IP privada y nombre DNS público de la instancia EC2, y las extensiones
SubjectKeyIdentifier y AuthorityKeyIdentifier requeridas por OpenSSL 3.x / Python 3.15+.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import ipaddress
import os
import secrets
import sys

from cryptography import x509
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

root = Path(__file__).resolve().parents[1] / 'runtime'
root.mkdir(exist_ok=True)
try:
    root.chmod(0o700)
except Exception:
    pass

def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('wb') as f:
        f.write(data)
    try:
        if path.suffix in ('.key',):
            path.chmod(0o600 if 'ca' in path.parts else 0o644)
    except Exception:
        pass

def key_bytes(key):
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()
    )

# 1. Argumentos de red para AWS
aws_ips_raw = os.environ.get('AWS_IPS', '')
if len(sys.argv) > 1:
    aws_ips_raw = sys.argv[1]

aws_dns_raw = os.environ.get('AWS_DNS', '')
if len(sys.argv) > 2:
    aws_dns_raw = sys.argv[2]

extra_san_names = []
if aws_ips_raw:
    for ip_str in aws_ips_raw.split(','):
        ip_clean = ip_str.strip()
        if ip_clean:
            try:
                extra_san_names.append(x509.IPAddress(ipaddress.ip_address(ip_clean)))
                print(f"Agregando SAN IP para Gateway: {ip_clean}")
            except ValueError:
                extra_san_names.append(x509.DNSName(ip_clean))

if aws_dns_raw:
    for dns_str in aws_dns_raw.split(','):
        dns_clean = dns_str.strip()
        if dns_clean:
            extra_san_names.append(x509.DNSName(dns_clean))
            print(f"Agregando SAN DNS para Gateway: {dns_clean}")

# 2. Generar nueva CA con SKI
ca_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'DFSha laboratorio CA')])
now = datetime.now(timezone.utc)
ca_ski = x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key())

ca = (x509.CertificateBuilder()
      .subject_name(name)
      .issuer_name(name)
      .public_key(ca_key.public_key())
      .serial_number(x509.random_serial_number())
      .not_valid_before(now - timedelta(minutes=5))
      .not_valid_after(now + timedelta(days=365))
      .add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
      .add_extension(ca_ski, False)
      .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, None, None), True)
      .sign(ca_key, hashes.SHA256()))

save(root / 'ca/ca.key', key_bytes(ca_key))
save(root / 'trust/ca.crt', ca.public_bytes(serialization.Encoding.PEM))
print("CA regenerada con SubjectKeyIdentifier exitosamente.")

# 3. Certificados por Servicio con AKI y SKI
roles = ['gateway', 'cn1', 'cn2', 'cn3', 'dn1', 'dn2', 'dn3', 'dn4',
         'repair', 'maintenance', 'client', 'etcd1', 'etcd2', 'etcd3']

for role in roles:
    key_file = root / f'tls/{role}/server.key'
    crt_file = root / f'tls/{role}/server.crt'

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    names = [x509.DNSName(role), x509.DNSName('localhost'), x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]
    
    if role.startswith('etcd'):
        names = [x509.DNSName(role), x509.DNSName('localhost')]
    if role.startswith('cn'):
        names.append(x509.DNSName('control'))
    if role == 'gateway' and extra_san_names:
        names.extend(extra_san_names)
        
    leaf_ski = x509.SubjectKeyIdentifier.from_public_key(key.public_key())
    leaf_aki = x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ca_ski)

    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, role)]))
            .issuer_name(ca.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=90))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
            .add_extension(leaf_ski, False)
            .add_extension(leaf_aki, False)
            .add_extension(x509.SubjectAlternativeName(names), False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH]), False)
            .sign(ca_key, hashes.SHA256()))
            
    save(key_file, key_bytes(key))
    save(crt_file, cert.public_bytes(serialization.Encoding.PEM))

# 4. Secretos Compartidos
for name in ['jwt', 'capability', 'master', 'receipt/dn1', 'receipt/dn2', 'receipt/dn3', 'receipt/dn4']:
    path = root / f'secrets/{name}.key'
    if not path.exists():
        save(path, secrets.token_hex(32).encode())

print('Certificados regenerados con éxito para AWS.')
