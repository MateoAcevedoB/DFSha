"""Mediciones reales de put/get; no contiene resultados precalculados.

PYTHONPATH=. python scripts/benchmark.py --sizes-mib 4 16 --clients 1 2
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
import os
from pathlib import Path
import secrets
import tempfile
import time
from dfsha.client import Client


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--url', default=os.getenv('DFSHA_URL', 'http://127.0.0.1:18080'))
    p.add_argument('--sizes-mib', type=int, nargs='+', default=[4, 16])
    p.add_argument('--clients', type=int, nargs='+', default=[1, 2])
    p.add_argument('--repeats', type=int, default=3)
    p.add_argument('--output', default='evidencias/benchmark.csv')
    p.add_argument('--condition',default='normal')
    a = p.parse_args()
    if a.repeats < 1 or any(v < 1 for v in a.clients + a.sizes_mib):
        p.error('Los tamaños, clientes y repeticiones deben ser positivos')
    output = Path(a.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='dfsha-bench-') as td, output.open('w', newline='') as report:
        fields = ['condition','size_mib', 'clients', 'repeat', 'worker', 'put_s', 'get_s',
                  'put_mib_s', 'get_mib_s', 'round_wall_s', 'sha256', 'verified']
        writer = csv.DictWriter(report, fieldnames=fields)
        writer.writeheader()
        for size in a.sizes_mib:
            source = Path(td) / 'source'
            with source.open('wb') as f:
                for _ in range(size):
                    f.write(os.urandom(1024 * 1024))
            for count in a.clients:
                # Autenticación fuera del tiempo de transferencia.
                clients = [Client(a.url, 'bench_' + secrets.token_hex(6), secrets.token_urlsafe(24), True)
                           for _ in range(count)]
                try:
                    for repeat in range(a.repeats):
                        def run(i):
                            client = clients[i]
                            t = time.perf_counter()
                            sent = client.put(source, f'/run{repeat}')
                            uploaded = time.perf_counter()
                            received = client.get(f'/run{repeat}', Path(td) / f'out{i}')
                            end = time.perf_counter()
                            if sent['sha256'] != received['sha256']:
                                raise RuntimeError('Fallo de integridad')
                            put, get = uploaded - t, end - uploaded
                            return dict(condition=a.condition,size_mib=size, clients=count, repeat=repeat, worker=i,
                                        put_s=put, get_s=get, put_mib_s=size/put, get_mib_s=size/get,
                                        sha256=sent['sha256'], verified=True)
                        start = time.perf_counter()
                        with ThreadPoolExecutor(count) as pool:
                            rows = list(pool.map(run, range(count)))
                        wall = time.perf_counter() - start
                        for row in rows:
                            writer.writerow({**row, 'round_wall_s': wall})
                        report.flush()
                        print(f'{size} MiB, {count} clientes, repetición {repeat}: {wall:.3f} s', flush=True)
                finally:
                    for client in clients:
                        client.close()
    print(f'Resultados: {output}')

if __name__ == '__main__':
    main()
