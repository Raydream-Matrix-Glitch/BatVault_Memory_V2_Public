from link_utils.derive_links import derive_links


def test_based_on_transitions_reciprocity():
    decisions = {
        "d1": {"id": "d1", "transitions": []},
        "d2": {"id": "d2", "based_on": ["d1"], "transitions": ["T2"]},
    }

    # events / transitions dicts not required for this rule
    derive_links(decisions, {}, {})

    # The transition from the successor should now appear in the prior decision
    assert "T2" in decisions["d1"]["transitions"]

    # Idempotency: running again must not create duplicates
    derive_links(decisions, {}, {})
    assert decisions["d1"]["transitions"].count("T2") == 1