from __future__ import annotations

import json

import pytest

from scripts.summarize_rag_benchmark import (
    aggregate,
    render_latency_svg,
    render_quality_svg,
)


def _run(path, *, latency_offset: float = 0.0) -> None:
    payload = {}
    ablation_flags = {
        "A": (True, False, False, False, False, False),
        "B": (False, True, False, False, False, False),
        "C": (True, True, True, False, False, False),
        "D": (True, True, True, True, False, False),
        "E": (True, True, True, True, True, False),
        "F": (True, True, True, True, True, True),
    }
    for index, group in enumerate("ABCDEF", start=1):
        flags = ablation_flags[group]
        payload[group] = {
            "case_count": 40,
            "metrics": {
                "recall@5": 0.5,
                "recall@10": 0.75,
                "mrr": 0.6,
                "ndcg@10": 0.65,
                "citation_accuracy": 0.75,
                "metadata_filter_accuracy": 1.0,
                "old_version_false_recall_rate": 0.0,
                "mean_latency_ms": 10.0 * index + latency_offset,
                "p95_latency_ms": 20.0 * index + latency_offset,
                "query_success_rate": 1.0,
            },
            "config": {
                "top_ks": [5, 10],
                "ndcg_k": 10,
                "query_top_k": 10,
                "dataset_sha256": "sha256:data",
                "human_review_status": "pending",
                "label_origin": "AI-assisted candidate labels",
                "config_sha256": "sha256:config",
                "index_version": "chunks_v1",
                "embedding_model": "embedding",
                "embedding_dimension": 32,
                "bm25_candidates": 20,
                "dense_candidates": 20,
                "fused_candidates": 30,
                "rerank_candidates": 20,
                "rrf_k": 60,
                "reranker_model": "reranker",
                "actual_reranker_backend": "cross_encoder",
                "ranking_weights": {
                    "semantic_weight": 0.7,
                    "temporal_weight": 0.12,
                    "version_weight": 0.08,
                    "evidence_weight": 0.1,
                    "temporal_half_life_days": 180,
                },
                "latency_scope": "steady_state_after_group_warmup",
                "warmup_queries_per_group": 1,
                "ablation": {
                    "group": group,
                    "description": f"group {group}",
                    "use_bm25": flags[0],
                    "use_dense": flags[1],
                    "use_rrf": flags[2],
                    "use_reranker": flags[3],
                    "use_temporal_version": flags[4],
                    "use_evidence_ranking": flags[5],
                },
            },
        }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_aggregate_and_svg_rendering(tmp_path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    _run(first)
    _run(second, latency_offset=2.0)

    summary = aggregate([first, second])
    quality = tmp_path / "quality.svg"
    latency = tmp_path / "latency.svg"
    render_quality_svg(summary, quality)
    render_latency_svg(summary, latency)

    assert summary["run_count"] == 2
    assert summary["invariants"]["config_sha256"] == "sha256:config"
    assert summary["invariants"]["effective_candidate_windows"] == {
        "bm25": 20,
        "dense": 20,
        "fused": 30,
        "rerank": 20,
    }
    assert summary["invariants"]["ablation_flags"]["F"]["use_evidence_ranking"] is True
    assert summary["groups"]["A"]["metrics"]["p95_latency_ms"]["mean"] == 21.0
    assert "A–F 检索质量对比" in quality.read_text(encoding="utf-8")
    assert "A–F 稳态 P95 延迟" in latency.read_text(encoding="utf-8")


def test_aggregate_rejects_incompatible_runs(tmp_path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    _run(first)
    _run(second)
    payload = json.loads(second.read_text(encoding="utf-8"))
    payload["F"]["config"]["index_version"] = "chunks_v2"
    second.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="invariants"):
        aggregate([first, second])


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("config_sha256", "sha256:different"),
        ("bm25_candidates", 21),
        ("rrf_k", 61),
        ("top_ks", [5]),
        ("query_top_k", 11),
        ("ndcg_k", 20),
    ],
)
def test_aggregate_rejects_shared_evaluation_config_drift(
    tmp_path, key, value
) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    _run(first)
    _run(second)
    payload = json.loads(second.read_text(encoding="utf-8"))
    payload["C"]["config"][key] = value
    second.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="invariants"):
        aggregate([first, second])


def test_aggregate_rejects_ranking_weight_and_ablation_drift(tmp_path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    _run(first)
    _run(second)
    payload = json.loads(second.read_text(encoding="utf-8"))
    payload["D"]["config"]["ranking_weights"]["semantic_weight"] = 0.6
    second.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="invariants"):
        aggregate([first, second])

    _run(second)
    payload = json.loads(second.read_text(encoding="utf-8"))
    payload["F"]["config"]["ablation"]["use_evidence_ranking"] = False
    second.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="ablation flags"):
        aggregate([first, second])
