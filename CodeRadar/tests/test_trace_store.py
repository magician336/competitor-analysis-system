from mini_rag.api.trace_store import TraceStore


def test_trace_store_persists_and_enforces_retention(tmp_path) -> None:
    path = tmp_path / "traces.jsonl"
    store = TraceStore(path, retention=2)
    store.put({"query_id": "q1", "query": "one"})
    store.put({"query_id": "q2", "query": "two"})
    store.put({"query_id": "q3", "query": "three"})

    assert len(store) == 2
    assert store.get("q1") is None
    assert store.get("q3") == {"query_id": "q3", "query": "three"}

    reloaded = TraceStore(path, retention=2)
    assert reloaded.get("q2") is not None
    assert reloaded.get("q3") is not None

