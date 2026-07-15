from __future__ import annotations

import json

from scripts.evaluate_rag import _provenance


def test_evaluation_provenance_records_hashes_and_review_status(tmp_path) -> None:
    samples = tmp_path / "data" / "samples"
    samples.mkdir(parents=True)
    dataset = samples / "测试数据集.csv"
    dataset.write_text("case_id,question\ncase,question\n", encoding="utf-8")
    (samples / "评测集说明.json").write_text(
        json.dumps(
            {
                "human_review_status": "pending",
                "label_origin": "AI-assisted candidate labels",
            }
        ),
        encoding="utf-8",
    )
    config = tmp_path / "mini_rag.yaml"
    config.write_text("version: 1\n", encoding="utf-8")

    result = _provenance(dataset, str(config))

    assert result["dataset_sha256"].startswith("sha256:")
    assert result["config_sha256"].startswith("sha256:")
    assert result["human_review_status"] == "pending"
    assert result["label_origin"] == "AI-assisted candidate labels"
