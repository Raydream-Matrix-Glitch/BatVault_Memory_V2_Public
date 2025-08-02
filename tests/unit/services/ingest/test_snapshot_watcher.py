import shutil, time
from pathlib import Path
from types import SimpleNamespace

from services.ingest.src.ingest.watcher import SnapshotWatcher, _dummy_app
from packages.core_utils.snapshot import compute_snapshot_etag_for_files


def _freeze(monkeypatch, ts: int = 1_694_976_000):
    """Freeze time.time() so hashes are deterministic."""
    monkeypatch.setattr(time, "time", lambda: ts)


def _seed(tmp_path: Path, fixtures_dir: Path):
    for src in fixtures_dir.glob("*.json"):
        shutil.copy(src, tmp_path / src.name)


def test_etag_deterministic(monkeypatch, tmp_path):
    _freeze(monkeypatch)
    _seed(tmp_path, Path("memory/fixtures/events"))

    app = _dummy_app()
    w = SnapshotWatcher(app, root_dir=tmp_path)
    etag = w.compute_etag()

    assert etag is not None
    assert etag == compute_snapshot_etag_for_files([str(p) for p in tmp_path.glob("*.json")])


def test_tick_sets_app_state(monkeypatch, tmp_path):
    _freeze(monkeypatch)
    _seed(tmp_path, Path("memory/fixtures/events"))

    app = _dummy_app()
    w = SnapshotWatcher(app, root_dir=tmp_path)

    assert not hasattr(app.state, "snapshot_etag")
    first = w.tick()
    assert app.state.snapshot_etag == first

    # mutating a file should change the hash
    target = next(tmp_path.glob("*.json"))
    target.write_text(target.read_text() + " ", encoding="utf-8")
    _freeze(monkeypatch, ts=1_694_976_001)  # advance virtual clock
    second = w.tick()

    assert first != second
    assert app.state.snapshot_etag == second
