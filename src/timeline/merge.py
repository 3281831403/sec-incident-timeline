"""Core merge logic for multi-source security incident timelines.

Every public callable in this module is an unimplemented stub.  The tests in
tests/test_merge.py define the required behaviour; see README.md for the data
contract.  Implementations must not mutate caller-owned input.
"""


def parse_event(raw: dict) -> dict:
    """Normalize one raw event mapping.

    The result has id, source, local_time, seq, type, caused_by and
    corrected_time keys.  corrected_time starts as None.  Missing local_time
    or seq becomes None, and caused_by is copied.  id and source are required.
    """
    raise NotImplementedError("parse_event is not implemented")


def estimate_skew(samples: list[dict]) -> dict:
    """Return one clock offset per source using its minimum-RTT sample."""
    raise NotImplementedError("estimate_skew is not implemented")


def correct_time(event: dict, skew: dict) -> int:
    """Return local_time plus the source offset, applying the offset once.

    An existing corrected_time is returned unchanged.  The event is not
    mutated.  Returns None only when both time fields are missing.
    """
    raise NotImplementedError("correct_time is not implemented")


def causal_edge(events: list[dict]) -> list:
    """Build valid directed causal edges as (cause_id, effect_id) tuples.

    Edges with missing endpoints, different sources, or a self-loop are
    discarded.  Valid edges are deduplicated in stable input order.
    """
    raise NotImplementedError("causal_edge is not implemented")


def topo_order(events: list[dict]) -> list:
    """Return event ids in causal order with a stable time/source tie-break.

    Raises ValueError starting with "causal cycle" when a cycle is present.
    """
    raise NotImplementedError("topo_order is not implemented")


def detect_gap(events: list[dict], sources: list[str]) -> list:
    """Report missing sequence numbers per source, resetting at every source."""
    raise NotImplementedError("detect_gap is not implemented")


def validate_timeline(events: list[dict]) -> list[str]:
    """Return missing-time, duplicate-sequence and causal-cycle issues."""
    raise NotImplementedError("validate_timeline is not implemented")


def timeline_batch(batches: list[dict], policy: dict) -> dict:
    """Merge independent batches, returning results keyed by batch_id."""
    raise NotImplementedError("timeline_batch is not implemented")