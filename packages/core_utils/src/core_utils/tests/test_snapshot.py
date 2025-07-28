from core_utils import compute_snapshot_etag_for_files
import os, tempfile, time

def test_snapshot_etag_changes_with_time(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("hello", encoding="utf-8")
    e1 = compute_snapshot_etag_for_files([str(p)])
    time.sleep(1)
    e2 = compute_snapshot_etag_for_files([str(p)])
    assert e1 != e2
