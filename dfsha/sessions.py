"""RF3: instantánea local y fusión optimista por bloques al cerrar."""
import tempfile
from pathlib import Path
from .blocks import BLOCK_SIZE


class FileSession:
    def __init__(self, client, remote, mode='r'):
        if mode not in ('r', 'w', 'a', 'rw'):
            raise ValueError('Modos admitidos: r, w, a, rw')
        self.client, self.remote, self.mode = client, remote, mode
        self.closed = False
        self.dirty = mode == 'w'
        self.result = None
        self.changed = set()
        response = client.http.get('/files/download', params={'path': remote})
        if response.status_code == 404 and mode in ('w', 'a'):
            snapshot = None
            self.dirty = True
        else:
            response.raise_for_status()
            snapshot = response.json()
        self.base = snapshot['version'] if snapshot else None
        self.base_size = snapshot['size'] if snapshot else 0
        self._temp = tempfile.TemporaryDirectory(prefix='dfsha-session-')
        self._path = Path(self._temp.name) / 'content'
        try:
            if snapshot and mode != 'w':
                client.get(remote, self._path, snapshot=snapshot)
            else:
                self._path.write_bytes(b'')
            self._file = self._path.open('r+b')
            if mode == 'a':
                self._file.seek(0, 2)
        except BaseException:
            self._temp.cleanup()
            raise

    def _check(self):
        if self.closed:
            raise ValueError('Sesión cerrada')

    def read(self, size=-1):
        self._check()
        if self.mode not in ('r', 'rw'):
            raise ValueError('El modo no permite lectura')
        return self._file.read(size)

    def seek(self, offset):
        self._check()
        size = self._path.stat().st_size
        # Los buffers se vacían tras cada write; no hay huecos.
        if not 0 <= offset <= size:
            raise ValueError('Desplazamiento fuera del archivo; no se admiten huecos')
        return self._file.seek(offset)

    def write(self, data):
        self._check()
        if self.mode == 'r':
            raise ValueError('Sesión de solo lectura')
        if self.mode == 'a':
            self._file.seek(0, 2)
        offset = self._file.tell()
        result = self._file.write(data)
        if result:
            self.changed.update(range(offset//BLOCK_SIZE,(offset+result-1)//BLOCK_SIZE+1))
        self._file.flush()
        self.dirty = True
        return result

    def close(self):
        if self.closed:
            return self.result
        self._file.flush()
        if self.dirty:
            self.result = self.client.put(self._path, self.remote, expected_version=self.base,
                changed=self.changed if self.base and self.mode != 'w' else None)
        self._file.close()
        self.closed = True
        self._temp.cleanup()
        return self.result

    def save_local(self, destination):
        """Exporta cambios después de un conflicto, antes de abortar."""
        self._check()
        self._file.flush()
        import shutil
        shutil.copyfile(self._path, destination)

    def abort(self):
        if not self.closed:
            self._file.close()
            self.closed = True
            self._temp.cleanup()

    def __del__(self):
        # Cerrar primero el descriptor evita errores de limpieza en Windows.
        try:
            self.abort()
        except Exception:
            pass

    def __enter__(self):
        self._check()
        return self

    def __exit__(self, kind, value, traceback):
        if kind is None:
            self.close()
        else:
            self.abort()
