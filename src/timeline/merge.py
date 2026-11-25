"""Core merge logic for multi-source security incident timelines.

Pure functions over in-memory event dicts.  Caller-owned input is never
mutated and no external dependencies are used.
"""

import heapq


def parse_event(raw: dict) -> dict:
    """Normalize one raw event mapping.

    The result has id, source, local_time, seq, type, caused_by and
    corrected_time keys.  corrected_time starts as None.  Missing local_time
    or seq becomes None, and caused_by is copied.  id and source are required.
    """
    if not raw.get("id"):
        raise ValueError("event id is required")
    if not raw.get("source"):
        raise ValueError("event source is required")
    # caused_by is always a fresh list owned by the normalized event.
    caused_by = list(raw.get("caused_by") or [])
    return {
        "id": raw["id"],
        "source": raw["source"],
        "local_time": raw.get("local_time"),
        "seq": raw.get("seq"),
        "type": raw.get("type"),
        "caused_by": caused_by,
        "corrected_time": None,
    }


def estimate_skew(samples: list[dict]) -> dict:
    """Return one clock offset per source using its minimum-RTT sample."""
    best_rtt: dict = {}
    skew: dict = {}
    for sample in samples:
        source = sample["source"]
        # Strictly smaller RTT wins; an equal RTT keeps the first sample.
        if source not in best_rtt or sample["rtt"] < best_rtt[source]:
            best_rtt[source] = sample["rtt"]
            skew[source] = sample["offset"]
    return skew


def correct_time(event: dict, skew: dict) -> int:
    """Return local_time plus the source offset, applying the offset once.

    An existing corrected_time is returned unchanged.  The event is not
    mutated.  Returns None only when both time fields are missing.
    """
    corrected = event.get("corrected_time")
    if corrected is not None:
        # Already corrected: never add the offset a second time.
        return corrected
    local_time = event.get("local_time")
    if local_time is None:
        return None
    return local_time + skew.get(event["source"], 0)


def _event_index(events: list[dict]) -> dict:
    """Map each event id to its event dict."""
    return {event["id"]: event for event in events}


def _valid_edges(events: list[dict]) -> list[tuple]:
    """Build legal (cause_id, effect_id) edges in stable input order.

    An edge is legal only when both endpoints exist, both come from the same
    source, and the edge is not a self-loop.  Duplicate edges are dropped.
    """
    by_id = _event_index(events)
    edges = []
    seen = set()
    for effect in events:
        effect_id = effect["id"]
        for cause_id in effect.get("caused_by", ()):
            cause = by_id.get(cause_id)
            if cause is None:
                # Points at an event that does not exist.
                continue
            if cause["source"] != effect["source"]:
                # Causal markers only link events of one source.
                continue
            if cause_id == effect_id:
                # A self-loop would make the event depend on itself.
                continue
            edge = (cause_id, effect_id)
            if edge not in seen:
                seen.add(edge)
                edges.append(edge)
    return edges


def causal_edge(events: list[dict]) -> list:
    """Build valid directed causal edges as (cause_id, effect_id) tuples.

    Edges with missing endpoints, different sources, or a self-loop are
    discarded.  Valid edges are deduplicated in stable input order.
    """
    return _valid_edges(events)


def _sort_key(event: dict):
    """Ordering key: corrected time, then source, seq and id.

    Events without a corrected time sort after every timed event instead of
    being treated as timestamp zero.
    """
    corrected = event.get("corrected_time")
    return (
        corrected is None,
        corrected,
        event["source"],
        event["seq"],
        event["id"],
    )


def topo_order(events: list[dict]) -> list:
    """Return event ids in causal order with a stable time/source tie-break.

    Raises ValueError starting with "causal cycle" when a cycle is present.
    """
    by_id = _event_index(events)
    edges = _valid_edges(events)

    indegree = {event["id"]: 0 for event in events}
    dependents: dict = {event["id"]: [] for event in events}
    for cause_id, effect_id in edges:
        indegree[effect_id] += 1
        dependents[cause_id].append(effect_id)

    # Kahn's algorithm: among every currently-available event pick the one
    # with the smallest sort key, so causal edges always win over raw time
    # and input order never influences the result.
    ready = [
        (_sort_key(event), event["id"])
        for event in events
        if indegree[event["id"]] == 0
    ]
    heapq.heapify(ready)

    order = []
    while ready:
        _, current_id = heapq.heappop(ready)
        order.append(current_id)
        for effect_id in dependents[current_id]:
            indegree[effect_id] -= 1
            if indegree[effect_id] == 0:
                heapq.heappush(ready, (_sort_key(by_id[effect_id]), effect_id))

    if len(order) != len(events):
        # Remaining events still carry indegree: they belong to a cycle.
        placed = set(order)
        remaining = sorted(event_id for event_id in indegree if event_id not in placed)
        raise ValueError("causal cycle: " + ",".join(remaining))
    return order


def detect_gap(events: list[dict], sources: list[str]) -> list:
    """Report missing sequence numbers per source, resetting at every source."""
    gaps = []
    for source in sources:
        # Sequence state is rebuilt from scratch for each source; numbers are
        # never concatenated across the source boundary.
        seqs = {
            event["seq"]
            for event in events
            if event["source"] == source and event["seq"] is not None
        }
        if not seqs:
            continue
        low, high = min(seqs), max(seqs)
        missing = [value for value in range(low, high) if value not in seqs]
        if missing:
            gaps.append({"source": source, "missing": missing})
    return gaps


def _causal_cycles(events: list[dict]) -> list[list[str]]:
    """Return causal cycles as lists of event ids, using legal edges only.

    A cycle is a strongly connected component of size at least two.  The
    ids inside each cycle are sorted; the cycle list itself is sorted for a
    deterministic report.
    """
    adjacency = {event["id"]: [] for event in events}
    for cause_id, effect_id in _valid_edges(events):
        adjacency[cause_id].append(effect_id)

    # Iterative Tarjan so a deep graph cannot hit the recursion limit.
    index = {}
    lowlink = {}
    on_stack = set()
    stack = []
    next_index = 0
    cycles = []

    for root in adjacency:
        if root in index:
            continue
        index[root] = lowlink[root] = next_index
        next_index += 1
        stack.append(root)
        on_stack.add(root)
        work = [(root, iter(adjacency[root]))]
        while work:
            node, neighbors = work[-1]
            advanced = False
            for neighbor in neighbors:
                if neighbor not in index:
                    index[neighbor] = lowlink[neighbor] = next_index
                    next_index += 1
                    stack.append(neighbor)
                    on_stack.add(neighbor)
                    work.append((neighbor, iter(adjacency[neighbor])))
                    advanced = True
                    break
                if neighbor in on_stack:
                    lowlink[node] = min(lowlink[node], index[neighbor])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[node])
            if lowlink[node] == index[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) >= 2:
                    cycles.append(sorted(component))

    return sorted(cycles)


def validate_timeline(events: list[dict]) -> list[str]:
    """Return missing-time, duplicate-sequence and causal-cycle issues."""
    issues = []

    # 1. Events without a corrected time, in input order.
    for event in events:
        if event.get("corrected_time") is None:
            issues.append("missing_time:" + event["id"])

    # 2. Repeated non-None sequence numbers within one source.  Each duplicate
    #    (source, seq) key is reported once at its first appearance; duplicate
    #    events themselves are kept, never merged or dropped.
    seen_keys = set()
    reported = set()
    for event in events:
        seq = event["seq"]
        if seq is None:
            continue
        key = (event["source"], seq)
        if key in seen_keys:
            if key not in reported:
                reported.add(key)
                issues.append("duplicate_seq:{}:{}".format(event["source"], seq))
        else:
            seen_keys.add(key)

    # 3. Causal cycles, reported last with their ids sorted.
    for cycle in _causal_cycles(events):
        issues.append("causal_cycle:" + ",".join(cycle))

    return issues


def timeline_batch(batches: list[dict], policy: dict) -> dict:
    """Merge independent batches, returning results keyed by batch_id."""
    skew_policy = policy.get("skew", "min_rtt")
    if skew_policy != "min_rtt":
        raise ValueError("unsupported skew policy: {}".format(skew_policy))

    merged = {}
    for batch in batches:
        batch_id = batch["batch_id"]
        if batch_id in merged:
            raise ValueError("duplicate batch_id: {}".format(batch_id))

        # Every batch derives its own skew from its own samples; nothing is
        # carried over from a previous batch.
        skew = estimate_skew(batch.get("samples", []))

        events = []
        for raw in batch["events"]:
            event = parse_event(raw)
            event["corrected_time"] = correct_time(event, skew)
            events.append(event)

        merged[batch_id] = {
            "batch_id": batch_id,
            "skew": skew,
            "events": events,
            "order": topo_order(events),
            "gaps": detect_gap(events, batch.get("sources", [])),
            "issues": validate_timeline(events),
        }
    return merged