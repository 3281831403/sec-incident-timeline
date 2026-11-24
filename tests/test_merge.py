import pytest

from timeline.merge import (
    causal_edge,
    correct_time,
    detect_gap,
    estimate_skew,
    parse_event,
    timeline_batch,
    topo_order,
    validate_timeline,
)


def make_event(event_id, source, time_value, seq, caused_by=()):
    """Build an already-normalized event for the pure merge functions."""
    return {
        "id": event_id,
        "source": source,
        "local_time": time_value,
        "corrected_time": time_value,
        "seq": seq,
        "type": "auth",
        "caused_by": list(caused_by),
    }


def make_raw(event_id, source, local_time, seq, caused_by=()):
    """Build a raw event accepted by parse_event and timeline_batch."""
    return {
        "id": event_id,
        "source": source,
        "local_time": local_time,
        "seq": seq,
        "type": "auth",
        "caused_by": list(caused_by),
    }


def test_skew_uses_min_rtt_sample_not_mean():
    samples = [
        {"source": "web-01", "offset": 120, "rtt": 8},
        {"source": "web-01", "offset": 400, "rtt": 300},
        {"source": "web-01", "offset": 180, "rtt": 9},
        {"source": "db-01", "offset": -50, "rtt": 12},
        {"source": "db-01", "offset": 150, "rtt": 90},
    ]

    skew = estimate_skew(samples)

    assert skew == {"web-01": 120, "db-01": -50}
    assert skew["web-01"] != 233


def test_correct_time_applies_skew_once():
    event = make_event("web-01:7", "web-01", 10000, 7)
    event["corrected_time"] = None
    skew = {"web-01": 250}

    assert correct_time(event, skew) == 10250
    assert correct_time(event, skew) == 10250
    assert event["local_time"] == 10000
    assert event["corrected_time"] is None

    already_corrected = make_event("web-01:8", "web-01", 10250, 8)
    already_corrected["local_time"] = 10000
    assert correct_time(already_corrected, skew) == 10250


def test_topo_order_puts_cause_before_earlier_effect():
    events = [
        make_event("db-01:2", "db-01", 1000, 2, caused_by=["db-01:1"]),
        make_event("api-01:9", "api-01", 3000, 9),
        make_event("db-01:1", "db-01", 9000, 1),
    ]
    expected = ["api-01:9", "db-01:1", "db-01:2"]

    assert topo_order(events) == expected
    assert topo_order(list(reversed(events))) == expected


def test_causal_cycle_is_reported_and_topo_raises():
    events = [
        make_event("web:1", "web", 100, 1, caused_by=["web:3"]),
        make_event("web:2", "web", 200, 2, caused_by=["web:1"]),
        make_event("web:3", "web", 300, 3, caused_by=["web:2"]),
    ]

    assert validate_timeline(events) == ["causal_cycle:web:1,web:2,web:3"]
    with pytest.raises(ValueError, match="causal cycle"):
        topo_order(events)


def test_tie_break_is_source_then_seq_not_input_order():
    events = [
        make_event("z:2", "z", 1000, 2),
        make_event("a:3", "a", 1000, 3),
        make_event("z:1", "z", 1000, 1),
        make_event("a:1", "a", 1000, 1),
    ]
    expected = ["a:1", "a:3", "z:1", "z:2"]

    assert topo_order(events) == expected
    assert topo_order(list(reversed(events))) == expected


def test_causal_edge_drops_self_and_cross_source():
    events = [
        make_event("web:a", "web", 100, 1, caused_by=["web:a"]),
        make_event("db:b", "db", 200, 1, caused_by=["web:a"]),
        make_event("web:c", "web", 300, 2, caused_by=["web:a"]),
    ]

    assert causal_edge(events) == [("web:a", "web:c")]
    assert topo_order(events) == ["web:a", "db:b", "web:c"]


def test_gap_detection_resets_per_source():
    events = [
        make_event("web:1", "web", 100, 1),
        make_event("api:1", "api", 110, 1),
        make_event("db:1", "db", 120, 1),
        make_event("web:2", "web", 200, 2),
        make_event("api:3", "api", 210, 3),
        make_event("db:3", "db", 230, 3),
        make_event("api:4", "api", 240, 4),
    ]

    assert detect_gap(events, ["web", "db", "api"]) == [
        {"source": "db", "missing": [2]},
        {"source": "api", "missing": [2]},
    ]


def test_missing_time_sorts_last_not_zero():
    events = [
        make_event("web:late", "web", None, 5),
        make_event("web:1", "web", 1000, 1),
        make_event("web:2", "web", 2000, 2),
    ]

    assert topo_order(events) == ["web:1", "web:2", "web:late"]
    assert validate_timeline(events) == ["missing_time:web:late"]


def test_duplicate_sequence_is_reported_once():
    events = [
        make_event("web:1a", "web", 1000, 1),
        make_event("web:1b", "web", 1000, 1),
        make_event("web:2", "web", 2000, 2),
    ]

    assert validate_timeline(events) == ["duplicate_seq:web:1"]
    order = topo_order(events)
    assert len(order) == 3
    assert set(order) == {"web:1a", "web:1b", "web:2"}


def test_batch_does_not_reuse_first_skew():
    first = {
        "batch_id": "b1",
        "sources": ["web-01"],
        "samples": [
            {"source": "web-01", "offset": 100, "rtt": 4},
            {"source": "web-01", "offset": 900, "rtt": 400},
        ],
        "events": [
            make_raw("web-01:1", "web-01", 1000, 1),
            make_raw("web-01:2", "web-01", 2000, 2, ["web-01:1"]),
        ],
    }
    second = {
        "batch_id": "b2",
        "sources": ["web-01"],
        "samples": [
            {"source": "web-01", "offset": -200, "rtt": 5},
            {"source": "web-01", "offset": 700, "rtt": 500},
        ],
        "events": [
            make_raw("web-01:1", "web-01", 1000, 1),
            make_raw("web-01:2", "web-01", 1200, 2, ["web-01:1"]),
        ],
    }

    result = timeline_batch([first, second], {"skew": "min_rtt"})

    assert result["b1"]["skew"] == {"web-01": 100}
    assert result["b2"]["skew"] == {"web-01": -200}
    assert result["b1"]["events"][0]["corrected_time"] == 1100
    assert result["b2"]["events"][0]["corrected_time"] == 800


def test_single_source_orders_by_corrected_time():
    events = [
        make_event("s:1", "s", 3000, 1),
        make_event("s:3", "s", 2000, 3),
        make_event("s:2", "s", 1000, 2),
    ]

    assert topo_order(events) == ["s:2", "s:3", "s:1"]


def test_causal_pair_order_cannot_reverse():
    root = make_event("host:root", "host", 5000, 1)
    impact = make_event("host:impact", "host", 500, 2, caused_by=["host:root"])

    for events in ([impact, root], [root, impact]):
        order = topo_order(events)
        assert order == ["host:root", "host:impact"]
        assert order.index("host:root") < order.index("host:impact")


def test_tie_break_stable_by_source_and_seq():
    events = [
        make_event("web:2", "web", 1000, 2),
        make_event("web:1", "web", 1000, 1),
        make_event("db:9", "db", 1000, 9),
    ]
    expected = ["db:9", "web:1", "web:2"]

    assert topo_order(events) == expected
    assert topo_order(list(reversed(events))) == expected


def test_gap_is_reported():
    events = [
        make_event("web:4", "web", 4000, 4),
        make_event("web:1", "web", 1000, 1),
        make_event("web:2", "web", 2000, 2),
        make_event("web:6", "web", 6000, 6),
    ]

    assert detect_gap(events, ["web"]) == [
        {"source": "web", "missing": [3, 5]}
    ]


def test_two_batches_match_individual_merges():
    first = {
        "batch_id": "b1",
        "sources": ["web-01"],
        "samples": [
            {"source": "web-01", "offset": 100, "rtt": 5},
            {"source": "web-01", "offset": 400, "rtt": 600},
        ],
        "events": [
            make_raw("web-01:1", "web-01", 1000, 1),
            make_raw("web-01:2", "web-01", 900, 2, ["web-01:1"]),
        ],
    }
    second = {
        "batch_id": "b2",
        "sources": ["web-01"],
        "samples": [
            {"source": "web-01", "offset": -250, "rtt": 7},
            {"source": "web-01", "offset": 700, "rtt": 500},
        ],
        "events": [
            make_raw("web-01:1", "web-01", 3000, 1),
            make_raw("web-01:2", "web-01", 2500, 2, ["web-01:1"]),
        ],
    }
    policy = {"skew": "min_rtt"}

    combined = timeline_batch([first, second], policy)
    alone_first = timeline_batch([first], policy)["b1"]
    alone_second = timeline_batch([second], policy)["b2"]

    assert combined["b1"] == alone_first
    assert combined["b2"] == alone_second
    assert combined["b1"]["order"] == ["web-01:1", "web-01:2"]
    assert combined["b2"]["order"] == ["web-01:1", "web-01:2"]
    assert combined["b1"]["events"][1]["corrected_time"] == 1000
    assert combined["b2"]["events"][1]["corrected_time"] == 2250
    assert combined["b1"]["gaps"] == []
    assert combined["b2"]["gaps"] == []
    assert combined["b1"]["issues"] == []
    assert combined["b2"]["issues"] == []
    parsed = combined["b1"]["events"][0]
    assert parsed["type"] == "auth"
    assert parsed["caused_by"] == []

    raw = first["events"][0]
    parsed_direct = parse_event(raw)
    assert parsed_direct["id"] == "web-01:1"
    assert parsed_direct["source"] == "web-01"
    assert parsed_direct["local_time"] == 1000
    assert parsed_direct["seq"] == 1
    assert parsed_direct["caused_by"] == []
    assert parsed_direct["corrected_time"] is None
    assert "corrected_time" not in raw

    missing = parse_event(make_raw("x:9", "x", None, 9))
    assert missing["local_time"] is None
    assert missing["seq"] == 9
    assert missing["corrected_time"] is None