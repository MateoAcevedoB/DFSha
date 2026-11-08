"""Monitor conservador: verifica copias cifradas y restaura R=3 tras 60 s."""
import hashlib
import json
import logging
import os
import time
import httpx
from .blocks import BLOCK_SIZE, NODES, placement, sign, verify
from .store import EtcdStore
from .security import tls_context, node_url, secret_value

log = logging.getLogger('dfsha.repair')

class Repairer:
    def __init__(self, store, secret, client=None, delay=60, clock=time.monotonic):
        self.store, self.secret = store, secret
        self.client = client or httpx.Client(timeout=10, verify=tls_context())
        self.delay, self.clock = delay, clock
        self.missing_since = {}

    def headers(self, purpose, claims):
        return {'Authorization': 'Bearer ' + sign(self.secret, purpose, claims)}

    def cycle(self):
        stats = {'checked': 0, 'repaired': 0, 'unrecoverable': 0, 'waiting': 0}
        visited = set()
        for _, manifest, _ in self.store.scan('/dfsha/manifests/'):
            for index, block in enumerate(manifest['blocks']):
                bid = block['id']
                if bid in visited:
                    continue
                visited.add(bid)
                stats['checked'] += 1
                expected = min(BLOCK_SIZE, manifest['size'] - index * BLOCK_SIZE) + 28
                good, data = [], None
                for node in NODES:
                    try:
                        r = self.client.get(node_url(node) + f'/blocks/{bid}',
                            headers=self.headers('block-read', {'id': bid, 'nodes': [node]}))
                        r.raise_for_status()
                        if len(r.content) == expected and hashlib.sha256(r.content).hexdigest() == bid:
                            good.append(node)
                            data = r.content
                    except httpx.HTTPError:
                        continue
                if len(good) < 3:
                    first = self.missing_since.setdefault(bid, self.clock())
                    if data is None:
                        stats['unrecoverable'] += 1
                        log.error('No quedan copias validas del bloque %s', bid)
                    elif self.clock() - first < self.delay:
                        stats['waiting'] += 1
                    else:
                        targets = placement(bid) + [n for n in NODES if n not in placement(bid)]
                        for node in targets:
                            if node in good or len(good) >= 3:
                                continue
                            claims = {'sid': manifest['version'], 'index': index, 'id': bid,
                                      'size': expected, 'nodes': [node]}
                            try:
                                r = self.client.put(node_url(node) + f'/blocks/{bid}?replica=true',
                                    content=data, headers=self.headers('block-write', claims))
                                r.raise_for_status()
                                receipt = verify(self.secret, 'block-receipt', r.json()['receipts'][0])
                                if any(receipt.get(k) != v for k, v in claims.items() if k != 'nodes') or receipt.get('node') != node:
                                    raise ValueError('Recibo no corresponde a la copia')
                                good.append(node)
                                stats['repaired'] += 1
                                log.info('Copia reparada bloque=%s destino=%s', bid, node)
                            except (httpx.HTTPError, ValueError, KeyError, IndexError):
                                continue
                if len(good) >= 3:
                    self.missing_since.pop(bid, None)
                # El manifiesto no cambia. Las ubicaciones son un índice independiente.
                key = '/dfsha/locations/' + bid
                previous, revision = self.store.get(key)
                updated = {'nodes': sorted(good)}
                if updated != previous:
                    self.store.cas(key, revision, updated)
        self.missing_since = {k: v for k, v in self.missing_since.items() if k in visited}
        return stats

def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    store = EtcdStore(os.environ['ETCD_ENDPOINTS'].split(','))
    worker = Repairer(store, secret_value('CAPABILITY_SECRET',os.getenv('JWT_SECRET')), delay=float(os.getenv('REPAIR_DELAY', '60')))
    while True:
        try:
            log.info('Ciclo %s', json.dumps(worker.cycle()))
        except Exception:
            log.exception('Ciclo interrumpido; se reintentara sin confirmar copias no verificadas')
        time.sleep(float(os.getenv('REPAIR_INTERVAL', '10')))

if __name__ == '__main__':
    main()
