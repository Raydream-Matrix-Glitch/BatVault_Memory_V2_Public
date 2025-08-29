from core_utils.fingerprints import ensure_sha256_prefix

def test_ensure_sha256_prefix():
    assert ensure_sha256_prefix("abc") == "sha256:abc"
    assert ensure_sha256_prefix("sha256:def") == "sha256:def"