from gateway.prompt_envelope import build_prompt_envelope


def test_prompt_fingerprints_stable() -> None:
    ev = {"allowed_ids": [], "anchor": {}, "events": [], "transitions": {}}
    env1 = build_prompt_envelope("Why?", ev, snapshot_etag="etag123")
    env2 = build_prompt_envelope("Why?", ev, snapshot_etag="etag123")

    assert (
        env1["_fingerprints"]["prompt_fingerprint"]
        == env2["_fingerprints"]["prompt_fingerprint"]
    )
    assert (
        env1["_fingerprints"]["bundle_fingerprint"]
        == env2["_fingerprints"]["bundle_fingerprint"]
    )