from __future__ import annotations

import json

import pytest

from mini_rag.indexing import ElasticsearchClient, SearchBackendError


class _Response:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload
        self.content = b"" if payload is None else json.dumps(payload).encode()
        self.text = "" if payload is None else json.dumps(payload)

    def json(self):
        return self._payload


class _AliasSession:
    def __init__(self) -> None:
        self.alias_actions: list[dict] = []

    def request(self, method, url, **kwargs):
        if method == "HEAD":
            return _Response(200)
        if method == "GET" and "/_alias/" in url:
            return _Response(404, {"error": {"type": "alias_missing_exception"}, "status": 404})
        if method == "POST" and url.endswith("/_aliases"):
            self.alias_actions = kwargs["json"]["actions"]
            return _Response(200, {"acknowledged": True})
        raise AssertionError(f"unexpected request: {method} {url}")


def test_missing_alias_resolves_to_none_and_first_swap_only_adds() -> None:
    session = _AliasSession()
    client = ElasticsearchClient("http://es.test:9200", session=session)

    assert client.resolve_alias("chunks_current") is None
    client.swap_alias("chunks_current", "chunks_v1")

    assert session.alias_actions == [
        {
            "remove": {
                "index": "*",
                "alias": "chunks_current",
                "must_exist": False,
            }
        },
        {
            "add": {
                "index": "chunks_v1",
                "alias": "chunks_current",
                "is_write_index": True,
            }
        },
    ]


class _DenseSession:
    def __init__(self) -> None:
        self.body: dict = {}

    def request(self, method, url, **kwargs):
        assert method == "POST"
        assert url.endswith("/chunks_current/_search")
        self.body = kwargs["json"]
        return _Response(
            200,
            {
                "hits": {
                    "hits": [
                        {"_id": "one", "_score": 0.9, "_source": {"chunk_id": "one"}}
                    ]
                }
            },
        )


def test_dense_search_uses_filtered_approximate_knn() -> None:
    session = _DenseSession()
    client = ElasticsearchClient("http://es.test:9200", session=session)

    hits = client.search_dense(
        "chunks_current",
        [1.0, 0.0],
        {"competitor": "Cursor"},
        top_k=5,
    )

    assert hits[0].score == pytest.approx(0.9)
    assert session.body["knn"]["field"] == "embedding"
    assert session.body["knn"]["k"] == 5
    assert session.body["knn"]["num_candidates"] == 20
    assert session.body["knn"]["filter"] == {
        "bool": {"filter": [{"term": {"competitor": "Cursor"}}]}
    }
    assert session.body["_source"] == {"excludes": ["embedding"]}


def test_resolve_alias_rejects_multiple_physical_targets() -> None:
    class MultiAliasSession:
        def request(self, method, url, **kwargs):
            return _Response(200, {"chunks_v1": {}, "chunks_v2": {}})

    client = ElasticsearchClient("http://es.test:9200", session=MultiAliasSession())

    with pytest.raises(SearchBackendError, match="multiple indices"):
        client.resolve_alias("chunks_current")
