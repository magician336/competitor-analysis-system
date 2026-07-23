"""Aggregate repeated A-F Mini-RAG runs and render report-ready SVG charts."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from xml.sax.saxutils import escape


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GROUPS = tuple("ABCDEF")
QUALITY_METRICS = ("recall@10", "mrr", "ndcg@10")
SUMMARY_METRICS = (
    "recall@5",
    "recall@10",
    "mrr",
    "ndcg@10",
    "citation_accuracy",
    "metadata_filter_accuracy",
    "old_version_false_recall_rate",
    "mean_latency_ms",
    "p95_latency_ms",
    "query_success_rate",
)
ABLATION_FLAGS = (
    "use_bm25",
    "use_dense",
    "use_rrf",
    "use_reranker",
    "use_temporal_version",
    "use_evidence_ranking",
)


def _resolve(path: str | Path) -> Path:
    value = Path(path)
    return value.resolve() if value.is_absolute() else (PROJECT_ROOT / value).resolve()


def _load_run(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or tuple(sorted(payload)) != GROUPS:
        raise ValueError(f"{path} must contain exactly A-F result groups")
    for group in GROUPS:
        item = payload[group]
        if not isinstance(item, dict) or not isinstance(item.get("metrics"), dict):
            raise ValueError(f"{path} group {group} has no metrics object")
    return payload


def _series(values: Sequence[float]) -> dict[str, float]:
    checked = [float(value) for value in values]
    if not checked or any(not math.isfinite(value) for value in checked):
        raise ValueError("benchmark metric values must be finite")
    return {
        "mean": statistics.fmean(checked),
        "stddev": statistics.stdev(checked) if len(checked) > 1 else 0.0,
        "min": min(checked),
        "max": max(checked),
    }


def _required(config: dict[str, Any], key: str) -> Any:
    value = config.get(key)
    if value is None:
        raise ValueError(f"benchmark config is missing required invariant {key}")
    return value


def _effective_candidate_windows(config: dict[str, Any]) -> dict[str, int]:
    explicit = config.get("effective_candidate_windows")
    if explicit is not None:
        if not isinstance(explicit, dict):
            raise ValueError("effective_candidate_windows must be an object")
        windows = {
            name: explicit.get(name)
            for name in ("bm25", "dense", "fused", "rerank")
        }
    else:
        query_top_k = int(_required(config, "query_top_k"))
        windows = {
            "bm25": max(int(_required(config, "bm25_candidates")), query_top_k),
            "dense": max(int(_required(config, "dense_candidates")), query_top_k),
            "fused": max(int(_required(config, "fused_candidates")), query_top_k),
            "rerank": max(int(_required(config, "rerank_candidates")), query_top_k),
        }
    if any(value is None for value in windows.values()):
        raise ValueError(
            "effective_candidate_windows must define bm25, dense, fused and rerank"
        )
    checked = {name: int(value) for name, value in windows.items()}
    if any(value <= 0 for value in checked.values()):
        raise ValueError("effective_candidate_windows values must be positive")
    return checked


def _shared_invariants(item: dict[str, Any]) -> dict[str, Any]:
    config = item.get("config", {})
    if not isinstance(config, dict):
        raise ValueError("benchmark result config must be an object")
    top_ks = _required(config, "top_ks")
    ranking_weights = _required(config, "ranking_weights")
    if not isinstance(top_ks, list) or not top_ks:
        raise ValueError("top_ks must be a non-empty list")
    if not isinstance(ranking_weights, dict) or not ranking_weights:
        raise ValueError("ranking_weights must be a non-empty object")
    return {
        "case_count": item.get("case_count"),
        "dataset_sha256": _required(config, "dataset_sha256"),
        "human_review_status": _required(config, "human_review_status"),
        "label_origin": _required(config, "label_origin"),
        "config_sha256": _required(config, "config_sha256"),
        "index_version": _required(config, "index_version"),
        "embedding_model": _required(config, "embedding_model"),
        "embedding_dimension": _required(config, "embedding_dimension"),
        "reranker_model": _required(config, "reranker_model"),
        "actual_reranker_backend": _required(config, "actual_reranker_backend"),
        "effective_candidate_windows": _effective_candidate_windows(config),
        "rrf_k": _required(config, "rrf_k"),
        "ranking_weights": ranking_weights,
        "top_ks": top_ks,
        "query_top_k": _required(config, "query_top_k"),
        "ndcg_k": _required(config, "ndcg_k"),
        "latency_scope": _required(config, "latency_scope"),
        "warmup_queries_per_group": _required(config, "warmup_queries_per_group"),
    }


def _ablation_flags(item: dict[str, Any], group: str) -> dict[str, bool]:
    config = item.get("config", {})
    ablation = config.get("ablation") if isinstance(config, dict) else None
    if not isinstance(ablation, dict) or ablation.get("group") != group:
        raise ValueError(f"group {group} has an invalid ablation identity")
    flags = {name: ablation.get(name) for name in ABLATION_FLAGS}
    if any(not isinstance(value, bool) for value in flags.values()):
        raise ValueError(f"group {group} has incomplete ablation flags")
    return flags


def aggregate(paths: Sequence[Path]) -> dict[str, Any]:
    if not paths:
        raise ValueError("at least one A-F result file is required")
    runs = [_load_run(path) for path in paths]
    invariants = {
        **_shared_invariants(runs[0]["A"]),
        "ablation_flags": {
            group: _ablation_flags(runs[0][group], group) for group in GROUPS
        },
    }
    shared_invariants = {
        key: value for key, value in invariants.items() if key != "ablation_flags"
    }
    for run_index, run in enumerate(runs, start=1):
        for group in GROUPS:
            item = run[group]
            if _shared_invariants(item) != shared_invariants:
                raise ValueError(
                    f"run {run_index} group {group} does not share benchmark invariants"
                )
            if _ablation_flags(item, group) != invariants["ablation_flags"][group]:
                raise ValueError(
                    f"run {run_index} group {group} does not share ablation flags"
                )
    groups: dict[str, Any] = {}
    for group in GROUPS:
        exemplar = runs[0][group]
        metrics: dict[str, Any] = {}
        for name in SUMMARY_METRICS:
            values = [run[group]["metrics"].get(name) for run in runs]
            if any(value is None for value in values):
                raise ValueError(f"group {group} is missing metric {name}")
            metrics[name] = _series([float(value) for value in values])
        groups[group] = {
            "description": exemplar.get("config", {}).get("ablation", {}).get(
                "description", group
            ),
            "metrics": metrics,
        }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_count": len(runs),
        "source_files": [str(path) for path in paths],
        "invariants": invariants,
        "groups": groups,
    }


def _svg_header(width: int, height: int, title: str, subtitle: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">',
        "<style>",
        ".title{font:700 25px 'Segoe UI','Microsoft YaHei',sans-serif;fill:#10233f}",
        ".subtitle{font:14px 'Segoe UI','Microsoft YaHei',sans-serif;fill:#5b6b82}",
        ".label{font:600 14px 'Segoe UI','Microsoft YaHei',sans-serif;fill:#243b5a}",
        ".tick{font:12px 'Segoe UI','Microsoft YaHei',sans-serif;fill:#687891}",
        ".value{font:600 12px 'Segoe UI','Microsoft YaHei',sans-serif;fill:#243b5a}",
        ".note{font:12px 'Segoe UI','Microsoft YaHei',sans-serif;fill:#687891}",
        "</style>",
        f'<rect width="{width}" height="{height}" rx="18" fill="#f7f9fc"/>',
        f'<text x="42" y="44" class="title">{escape(title)}</text>',
        f'<text x="42" y="70" class="subtitle">{escape(subtitle)}</text>',
    ]


def render_quality_svg(summary: dict[str, Any], path: Path) -> None:
    width, height = 1120, 650
    left, right, top, bottom = 105, 55, 120, 54
    plot_width = width - left - right
    plot_height = height - top - bottom
    row_height = plot_height / len(GROUPS)
    colors = {"recall@10": "#1463ff", "mrr": "#00a6a6", "ndcg@10": "#f59e0b"}
    labels = {"recall@10": "Recall@10", "mrr": "MRR", "ndcg@10": "nDCG@10"}
    lines = _svg_header(
        width,
        height,
        "A–F 检索质量对比",
        f"{summary['run_count']} 次重复实验的均值；候选评测集状态：{summary['invariants']['human_review_status']}",
    )
    for tick in range(6):
        value = tick / 5
        x = left + value * plot_width
        lines.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{height-bottom}" stroke="#dce3ed"/>')
        lines.append(f'<text x="{x:.1f}" y="{height-bottom+22}" text-anchor="middle" class="tick">{value:.1f}</text>')
    legend_x = 690
    for index, metric in enumerate(QUALITY_METRICS):
        x = legend_x + index * 130
        lines.append(f'<rect x="{x}" y="42" width="14" height="14" rx="3" fill="{colors[metric]}"/>')
        lines.append(f'<text x="{x+21}" y="54" class="tick">{labels[metric]}</text>')
    bar_height = 17
    for row, group in enumerate(GROUPS):
        y_center = top + (row + 0.5) * row_height
        lines.append(f'<text x="72" y="{y_center+5:.1f}" text-anchor="middle" class="label">{group}</text>')
        for index, metric in enumerate(QUALITY_METRICS):
            value = summary["groups"][group]["metrics"][metric]["mean"]
            y = y_center - 29 + index * 22
            bar_width = max(1.0, value * plot_width)
            lines.append(f'<rect x="{left}" y="{y:.1f}" width="{bar_width:.1f}" height="{bar_height}" rx="4" fill="{colors[metric]}" opacity="0.9"/>')
            label_x = min(left + bar_width + 7, width - right - 34)
            lines.append(f'<text x="{label_x:.1f}" y="{y+13:.1f}" class="value">{value:.3f}</text>')
    lines.append('<text x="42" y="628" class="note">数值越高表示检索质量越好；质量指标只适用于当前候选标签，人工复核完成后需重算。</text>')
    lines.append("</svg>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render_latency_svg(summary: dict[str, Any], path: Path) -> None:
    width, height = 1120, 540
    left, right, top, bottom = 115, 75, 115, 62
    plot_width = width - left - right
    plot_height = height - top - bottom
    values = [summary["groups"][group]["metrics"]["p95_latency_ms"] for group in GROUPS]
    maximum = max(item["mean"] + item["stddev"] for item in values)
    # Reserve horizontal space after the longest error bar for its value label.
    # Without this padding, a clipped error bar can run through the label when
    # the largest mean + standard deviation is also the axis maximum.
    label_padding = max(100.0, maximum * 0.14)
    axis_max = max(
        10.0,
        math.ceil((maximum + label_padding) / 100.0) * 100.0,
    )
    lines = _svg_header(
        width,
        height,
        "A–F 稳态 P95 延迟",
        f"每组预热 1 次；柱为 {summary['run_count']} 次均值，误差线为样本标准差",
    )
    for tick in range(6):
        value = axis_max * tick / 5
        x = left + value / axis_max * plot_width
        lines.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{height-bottom}" stroke="#dce3ed"/>')
        lines.append(f'<text x="{x:.1f}" y="{height-bottom+23}" text-anchor="middle" class="tick">{value:.0f}</text>')
    row_height = plot_height / len(GROUPS)
    for row, group in enumerate(GROUPS):
        item = summary["groups"][group]["metrics"]["p95_latency_ms"]
        mean, stddev = item["mean"], item["stddev"]
        y = top + row * row_height + 13
        y_mid = y + 17
        bar_width = mean / axis_max * plot_width
        error_width = stddev / axis_max * plot_width
        lines.append(f'<text x="77" y="{y_mid+5:.1f}" text-anchor="middle" class="label">{group}</text>')
        lines.append(f'<rect x="{left}" y="{y:.1f}" width="{bar_width:.1f}" height="34" rx="7" fill="#6f52ed" opacity="0.9"/>')
        x_mean = left + bar_width
        error_left = max(float(left), x_mean - error_width)
        error_right = min(float(left + plot_width), x_mean + error_width)
        lines.append(f'<line x1="{error_left:.1f}" y1="{y_mid:.1f}" x2="{error_right:.1f}" y2="{y_mid:.1f}" stroke="#182842" stroke-width="2"/>')
        lines.append(f'<line x1="{error_left:.1f}" y1="{y_mid-6:.1f}" x2="{error_left:.1f}" y2="{y_mid+6:.1f}" stroke="#182842" stroke-width="2"/>')
        lines.append(f'<line x1="{error_right:.1f}" y1="{y_mid-6:.1f}" x2="{error_right:.1f}" y2="{y_mid+6:.1f}" stroke="#182842" stroke-width="2"/>')
        lines.append(f'<text x="{min(error_right+9, width-right-40):.1f}" y="{y_mid+5:.1f}" class="value">{mean:.1f} ms</text>')
    lines.append('<text x="42" y="518" class="note">数值越低表示响应越快；延迟比较使用同一设备、索引、模型缓存和候选窗口。</text>')
    lines.append("</svg>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", help="Repeated A-F result JSON files")
    parser.add_argument(
        "--output",
        default="data/runtime/mini_rag_ablations_summary.json",
        help="Summary JSON path",
    )
    parser.add_argument(
        "--assets-dir",
        default="docs/assets",
        help="Directory for report-ready SVG charts",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    inputs = [_resolve(path) for path in args.inputs]
    summary = aggregate(inputs)
    output = _resolve(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    assets = _resolve(args.assets_dir)
    quality_path = assets / "mini_rag_ablation_quality.svg"
    latency_path = assets / "mini_rag_ablation_latency.svg"
    render_quality_svg(summary, quality_path)
    render_latency_svg(summary, latency_path)
    print(
        json.dumps(
            {
                "summary": str(output),
                "quality_chart": str(quality_path),
                "latency_chart": str(latency_path),
                "run_count": summary["run_count"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
