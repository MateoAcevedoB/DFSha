"""Objeto sintético para probar el GC. Se ejecuta dentro del contenedor de un DN.

  python scripts/gc_probe.py create   -> crea un bloque huérfano "viejo" e imprime su id
  python scripts/gc_probe.py check ID -> falla si el bloque sigue existiendo

Reemplaza el `python -c` incrustado en PowerShell, que dependía del escape de comillas
y ocultaba el error real. No toca datos de usuarios.
"""
import hashlib
import os
import sys
import time
from pathlib import Path

AGE_SECONDS = 7300  # mayor que la gracia de 7200 s del GC


def root():
    return Path(os.getenv('DATA_DIR', '/data'))


def create(base=None):
    base = base or root()
    data = os.urandom(64)
    bid = hashlib.sha256(data).hexdigest()
    path = base / bid
    with path.open('xb') as f:
        f.write(data)
    old = time.time() - AGE_SECONDS
    os.utime(path, (old, old))
    return bid


def check(bid, base=None):
    base = base or root()
    if len(bid) != 64 or any(c not in '0123456789abcdef' for c in bid):
        raise SystemExit('Identificador de prueba inválido')
    if (base / bid).exists():
        raise SystemExit('El objeto huérfano no fue eliminado')
    print('OK: GC eliminó el objeto sintético sin referencias')


if __name__ == '__main__':
    if len(sys.argv) == 2 and sys.argv[1] == 'create':
        print(create())
    elif len(sys.argv) == 3 and sys.argv[1] == 'check':
        check(sys.argv[2])
    else:
        raise SystemExit(__doc__)
