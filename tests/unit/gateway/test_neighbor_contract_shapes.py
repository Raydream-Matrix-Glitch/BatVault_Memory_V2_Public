from gateway.evidence import _collect_allowed_ids  # or public helper if exposed

def test_neighbor_shape_normalisation():
    """_collect_allowed_ids must handle both flat and nested neighbor shapes."""
    from gateway.evidence import _collect_allowed_ids
    from gateway.evidence import WhyDecisionAnchor

    # Two response variants: flat list vs nested dict
    shapes = [
        {"neighbors": [
            {"type": "event",      "id": "e1"},
            {"type": "transition", "id": "t1"},
        ]},
        {"neighbors": {
            "events": [
                {"type": "event",      "id": "e2"},
            ],
            "transitions": [
                {"type": "transition", "id": "t2"},
            ],
        }},
    ]

    for shape in shapes:
        anchor = WhyDecisionAnchor(id="d1")
        allowed_ids = _collect_allowed_ids(shape, anchor)
        # must always include the anchor
        assert "d1" in allowed_ids
        # must include at least one event and one transition
        assert any(i.startswith("e") for i in allowed_ids), f"no event in {allowed_ids}"
        assert any(i.startswith("t") for i in allowed_ids), f"no transition in {allowed_ids}"
