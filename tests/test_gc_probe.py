import os, time, importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('gc_probe', Path(__file__).parent.parent / 'scripts' / 'gc_probe.py')
gc_probe = importlib.util.module_from_spec(spec); spec.loader.exec_module(gc_probe)


def test_probe_creates_old_orphan_and_check_detects_deletion(tmp_path):
    bid = gc_probe.create(tmp_path)
    assert len(bid) == 64
    p = tmp_path / bid
    assert p.exists() and p.stat().st_mtime < time.time() - 7200
    try:
        gc_probe.check(bid, tmp_path)
        assert False, 'debía fallar mientras el objeto existe'
    except SystemExit:
        pass
    p.unlink()
    gc_probe.check(bid, tmp_path)
