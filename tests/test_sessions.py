import httpx
import pytest
from test_transfers import cluster


def seed(client, root, data=b'abcdef'):
    source = root / 'original'
    source.write_bytes(data)
    client.put(source, '/file')


def test_snapshot_visibility_and_partial_write(cluster):
    client, _, _, root, _ = cluster
    seed(client, root)
    reader = client.open('/file')
    writer = client.open('/file', 'rw')
    writer.seek(2)
    assert writer.write(b'XY') == 2
    with client.open('/file') as other:
        assert other.read() == b'abcdef'
    writer.close()
    assert reader.read() == b'abcdef'
    reader.close()
    with client.open('/file') as other:
        assert other.read() == b'abXYef'


def test_two_writers_conflict_without_lost_update(cluster):
    client, _, _, root, _ = cluster
    seed(client, root)
    a, b = client.open('/file', 'rw'), client.open('/file', 'rw')
    a.write(b'A')
    b.write(b'B')
    a.close()
    with pytest.raises(httpx.HTTPStatusError) as error:
        b.close()
    assert error.value.response.status_code == 409
    b.abort()
    with client.open('/file') as reader:
        assert reader.read() == b'Abcdef'


def test_create_race_and_abort(cluster):
    client, _, _, _, _ = cluster
    a, b = client.open('/new', 'w'), client.open('/new', 'w')
    a.write(b'first')
    b.write(b'second')
    a.close()
    with pytest.raises(httpx.HTTPStatusError):
        b.close()
    b.abort()
    with pytest.raises(RuntimeError):
        with client.open('/new', 'w') as f:
            f.write(b'never published')
            raise RuntimeError('interrupted')
    with client.open('/new') as f:
        assert f.read() == b'first'


def test_modes_append_no_holes_and_closed_handle(cluster):
    client, _, _, root, _ = cluster
    seed(client, root)
    with client.open('/file', 'a') as f:
        f.seek(0)
        f.write(b'!')
        with pytest.raises(ValueError):
            f.seek(999)
    with client.open('/file') as f:
        assert f.read() == b'abcdef!'
        with pytest.raises(ValueError):
            f.write(b'x')
    with pytest.raises(ValueError):
        f.read()
    with client.open('/file', 'w'):
        pass
    with client.open('/file') as f:
        assert f.read() == b''


def test_failed_close_keeps_published_version(cluster):
    client, _, offline, root, _ = cluster
    seed(client, root)
    f = client.open('/file', 'rw')
    f.write(b'changed')
    offline.update(['dn1', 'dn2', 'dn3', 'dn4'])
    with pytest.raises(RuntimeError):
        f.close()
    f.abort()
    offline.clear()
    with client.open('/file') as f:
        assert f.read() == b'abcdef'


def test_simultaneous_closes_have_one_winner(cluster):
    from concurrent.futures import ThreadPoolExecutor
    client, _, _, root, _ = cluster
    seed(client, root)
    sessions = [client.open('/file', 'rw') for _ in range(2)]
    for f, data in zip(sessions, (b'A', b'B')):
        f.write(data)
    def close(f):
        try:
            f.close()
            return 200
        except httpx.HTTPStatusError as error:
            f.abort()
            return error.response.status_code
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(close, sessions)) == [200, 409]
    with client.open('/file') as f:
        assert f.read() in (b'Abcdef', b'Bbcdef')
