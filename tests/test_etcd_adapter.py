import base64
import json
import httpx
from dfsha.store import EtcdStore

def test_failover_and_base64(monkeypatch):
    calls = []
    def post(url, json, timeout, **kwargs):
        calls.append(url)
        if 'down' in url:
            raise httpx.ConnectError('offline')
        assert json['key'] == base64.b64encode(b'/test').decode()
        return httpx.Response(200, request=httpx.Request('POST', url), json={'kvs': [{
            'value': base64.b64encode(b'{"ok": true}').decode(), 'mod_revision': '7'}]})
    monkeypatch.setattr(httpx, 'post', post)
    assert EtcdStore(['http://down', 'http://up']).get('/test') == ({'ok': True}, 7)
    assert len(calls) == 2

def test_cas_request(monkeypatch):
    def post(url, json, timeout, **kwargs):
        assert json['compare'][0]['mod_revision'] == '7'
        assert json['compare'][0]['target'] == 'MOD'
        assert 'request_put' in json['success'][0]
        return httpx.Response(200, request=httpx.Request('POST', url), json={'succeeded': True})
    monkeypatch.setattr(httpx, 'post', post)
    assert EtcdStore(['http://up']).cas('/test', 7, {'ok': True})

def test_scan_paginates_without_repeating_keys(monkeypatch):
    store = EtcdStore(['http://up'])
    calls = []
    def request(operation, body):
        calls.append(body)
        key = '/dfsha/manifests/a' if len(calls) == 1 else '/dfsha/manifests/b'
        return {'more': len(calls) == 1, 'kvs': [{
            'key': base64.b64encode(key.encode()).decode(),
            'value': base64.b64encode(b'{"blocks": []}').decode(), 'mod_revision': '4'}]}
    monkeypatch.setattr(store, 'request', request)
    rows = list(store.scan('/dfsha/manifests/'))
    assert [row[0] for row in rows] == ['/dfsha/manifests/a', '/dfsha/manifests/b']
    assert base64.b64decode(calls[1]['key']).decode() == '/dfsha/manifests/a\0'
    assert calls[0]['range_end'] == calls[1]['range_end']
