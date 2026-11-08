"""Adaptador etcd v3. Lecturas linealizables y escrituras condicionales."""
import base64
import json
import httpx


class Unavailable(Exception):
    pass


def b64(value):
    return base64.b64encode(value.encode()).decode()


class EtcdStore:
    def __init__(self, endpoints):
        self.endpoints = endpoints

    def request(self, operation, body):
        for endpoint in self.endpoints:
            try:
                from .security import tls_context
                options = {'verify': tls_context()} if __import__('os').getenv('TLS_CA') else {}
                response = httpx.post(endpoint + '/v3/kv/' + operation,
                                      json=body, timeout=3, **options)
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError):
                continue
        raise Unavailable('No hay mayoría de metadatos accesible')

    def get(self, key):
        result = self.request('range', {'key': b64(key)})
        rows = result.get('kvs', [])
        if not rows:
            return None, 0
        row = rows[0]
        return json.loads(base64.b64decode(row['value'])), int(row['mod_revision'])

    def cas(self, key, revision, value):
        compare = {'key': b64(key), 'result': 'EQUAL'}
        compare.update({'target': 'MOD', 'mod_revision': str(revision)} if revision
                       else {'target': 'VERSION', 'version': '0'})
        result = self.request('txn', {
            'compare': [compare],
            'success': [{'request_put': {'key': b64(key),
                         'value': b64(json.dumps(value, ensure_ascii=False))}}],
            'failure': []})
        return result.get('succeeded', False)

    def scan(self, prefix):
        """Páginas acotadas del prefijo ASCII; sin cargar todos los valores."""
        end = prefix[:-1] + chr(ord(prefix[-1]) + 1)
        start = prefix
        while True:
            result = self.request('range', {'key': b64(start), 'range_end': b64(end),
                'limit': '100', 'sort_order': 'ASCEND', 'sort_target': 'KEY'})
            rows = result.get('kvs', [])
            for row in rows:
                key = base64.b64decode(row['key']).decode()
                yield key, json.loads(base64.b64decode(row['value'])), int(row['mod_revision'])
            if not result.get('more') or not rows:
                return
            start = base64.b64decode(rows[-1]['key']).decode() + '\0'

    def delete(self, key):
        return self.request('deleterange', {'key': b64(key)})

    def publish(self, namespace_key, namespace_revision, namespace, upload_key, upload_revision, upload):
        """Publicación y resultado idempotente se confirman en una sola transacción."""
        pairs=[(namespace_key,namespace_revision,namespace),(upload_key,upload_revision,upload)]
        response=self.request('txn',{
            'compare':[{'key':b64(k),'result':'EQUAL','target':'MOD','mod_revision':str(r)} for k,r,_ in pairs],
            'success':[{'request_put':{'key':b64(k),'value':b64(json.dumps(v,ensure_ascii=False))}} for k,_,v in pairs],
            'failure':[]})
        return response.get('succeeded',False)
