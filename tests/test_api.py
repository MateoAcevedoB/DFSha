import copy
import threading
from concurrent.futures import ThreadPoolExecutor
import jwt
import pytest
from fastapi.testclient import TestClient
from dfsha.api import create_app
from dfsha.store import Unavailable

SECRET = 's' * 48

class MemoryStore:
    """Doble de prueba; nunca se usa en el despliegue."""
    def __init__(self):
        self.data = {}
        self.revision = 0
        self.lock = threading.Lock()

    def get(self, key):
        with self.lock:
            return copy.deepcopy(self.data.get(key, (None, 0)))

    def cas(self, key, revision, value):
        with self.lock:
            if self.data.get(key, (None, 0))[1] != revision:
                return False
            self.revision += 1
            self.data[key] = (copy.deepcopy(value), self.revision)
            return True

    def publish(self, nk, nr, namespace, uk, ur, upload):
        with self.lock:
            if self.data.get(nk,(None,0))[1]!=nr or self.data.get(uk,(None,0))[1]!=ur:
                return False
            self.revision+=1
            self.data[nk]=(copy.deepcopy(namespace),self.revision)
            self.data[uk]=(copy.deepcopy(upload),self.revision)
            return True

@pytest.fixture
def setup():
    store = MemoryStore()
    return TestClient(create_app(store, SECRET)), store

def account(client, name='alice'):
    body = {'username': name, 'password': 'Una-clave-larga-123'}
    assert client.post('/auth/register', json=body).status_code == 201
    token = client.post('/auth/login', json=body).json()['access_token']
    return {'Authorization': 'Bearer ' + token}

def test_isolation_and_directory_lifecycle(setup):
    client, _ = setup
    alice, bob = account(client), account(client, 'bobby')
    for headers in (alice, bob):
        assert client.post('/fs/mkdir', json={'path': '/datos'}, headers=headers).status_code == 201
    assert client.post('/fs/mkdir', json={'path': '/datos/privado'}, headers=alice).status_code == 201
    assert client.get('/fs/ls?path=/datos', headers=bob).json()['entries'] == []
    assert client.post('/fs/cd', json={'path': '/datos/privado'}, headers=bob).status_code == 404
    assert client.post('/fs/rmdir', json={'path': '/datos'}, headers=alice).status_code == 409
    assert client.post('/fs/rmdir', json={'path': '/datos/privado'}, headers=alice).status_code == 200
    assert client.post('/fs/rmdir', json={'path': '/datos'}, headers=alice).status_code == 200

def test_authentication_and_invalid_tokens(setup):
    client, _ = setup
    account(client)
    assert client.get('/fs/ls').status_code == 401
    assert client.post('/auth/login', json={'username': 'alice', 'password': 'wrong-password'}).status_code == 401
    assert client.post('/auth/register', json={'username': 'alice', 'password': 'another-long-password'}).status_code == 409
    for token in ['broken', jwt.encode({'sub': 'alice', 'iat': 1, 'exp': 2,
            'iss': 'dfsha', 'aud': 'dfsha-client'}, SECRET, algorithm='HS256')]:
        assert client.get('/fs/ls', headers={'Authorization': 'Bearer ' + token}).status_code == 401

@pytest.mark.parametrize('path', ['relative', '/../bobby', '/a/../b', '/a\\b', '/a\x00b'])
def test_invalid_paths(setup, path):
    client, _ = setup
    headers = account(client)
    assert client.post('/fs/mkdir', json={'path': path}, headers=headers).status_code == 400

def test_two_controllers_share_metadata(setup):
    first, store = setup
    headers = account(first)
    second = TestClient(create_app(store, SECRET))
    assert first.post('/fs/mkdir', json={'path': '/shared'}, headers=headers).status_code == 201
    assert second.get('/fs/ls', headers=headers).json()['entries'][0]['name'] == 'shared'

def test_concurrent_same_directory_has_one_winner(setup):
    client, _ = setup
    headers = account(client)
    with ThreadPoolExecutor(2) as pool:
        statuses = list(pool.map(lambda _: client.post('/fs/mkdir',
            json={'path': '/race'}, headers=headers).status_code, range(2)))
    assert sorted(statuses) == [201, 409]

def test_concurrent_distinct_directories_preserved(setup):
    client, _ = setup
    headers = account(client)
    with ThreadPoolExecutor(4) as pool:
        statuses = list(pool.map(lambda i: client.post('/fs/mkdir',
            json={'path': '/dir' + str(i)}, headers=headers).status_code, range(4)))
    assert statuses == [201] * 4
    assert len(client.get('/fs/ls', headers=headers).json()['entries']) == 4

def test_rm_only_removes_files(setup):
    client, store = setup
    headers = account(client)
    value, revision = store.get('/dfsha/users/alice')
    value['entries']['/example'] = {'type': 'file'}
    assert store.cas('/dfsha/users/alice', revision, value)
    assert client.post('/fs/rmdir', json={'path': '/example'}, headers=headers).status_code == 409
    assert client.post('/fs/rm', json={'path': '/example'}, headers=headers).status_code == 200
    assert client.post('/fs/rm', json={'path': '/'}, headers=headers).status_code == 409

def test_failure_is_not_success(setup):
    client, store = setup
    headers = account(client)
    def fail(key):
        raise Unavailable('No hay mayoría')
    store.get = fail
    assert client.get('/health/ready').status_code == 503
    assert client.get('/fs/ls', headers=headers).status_code == 503
