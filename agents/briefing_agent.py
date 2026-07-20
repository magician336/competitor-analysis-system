"""Evidence-aware Markdown briefing generation through a real LCEL pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import Runnable, RunnableLambda

from schemas.capability_snapshot import (
    CapabilityScore,
    CapabilityScoreStatus,
    CapabilitySnapshot,
)
from schemas.document import DimensionTag
from schemas.intelligence_card import EvidenceReference, IntelligenceCard


_DIMENSION_LABELS: dict[DimensionTag, str] = {
    DimensionTag.CODE_INTELLIGENCE: "D1 代码智能与生成质量",
    DimensionTag.AGENT_CONTEXT: "D2 项目级上下文与 Agent",
    DimensionTag.IDE_ECOSYSTEM: "D3 IDE、工具链与生态",
    DimensionTag.MODEL_EXTENSIBILITY: "D4 模型接入与扩展",
    DimensionTag.PERFORMANCE_COST: "D5 性能、稳定性与成本",
    DimensionTag.SECURITY_COMPLIANCE: "D6 安全与合规",
    DimensionTag.EDUCATION_FIT: "D7 教育适配",
}
_LOW_CONFIDENCE = 0.55


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _plain(value: Any) -> str:
    """Render model-authored text as one inert Markdown line."""

    return " ".join(str(value or "").replace("\x00", "").split())


def _table_cell(value: Any) -> str:
    return _plain(value).replace("|", "\\|") or "—"


def _prompt_value_to_text(value: Any) -> str:
    return str(value.to_string()) if hasattr(value, "to_string") else str(value)


def _format_delta(value: int | None) -> str:
    if value is None:
        return "—"
    if value > 0:
        return f"↑ +{value}"
    if value < 0:
        return f"↓ {value}"
    return "→ 0"


class BriefingAgent:
    """Create a product-manager briefing without executing evidence text.

    The Prompt file is the actual Markdown output template in the LCEL chain.
    Evidence quotations are deliberately not interpolated; only immutable
    source metadata is placed in the evidence index.
    """

    prompt_name = "briefing_prompt.md"

    def __init__(self, *, prompt_path: str | Path | None = None) -> None:
        self.prompt_path = Path(prompt_path) if prompt_path else (
            _repo_root() / "prompts" / self.prompt_name
        )
        template_text = self._load_template(self.prompt_path)
        self.prompt_template = PromptTemplate.from_template(template_text)
        required = {
            "competitor",
            "briefing_meta",
            "executive_summary",
            "core_dynamics",
            "opportunities",
            "threats",
            "actions",
            "capability_snapshot",
            "evidence_index",
            "review_items",
        }
        missing = required - set(self.prompt_template.input_variables)
        if missing:
            raise RuntimeError(
                "briefing template is missing variables: " + ", ".join(sorted(missing))
            )
        self.pipeline: Runnable = (
            RunnableLambda(self._prepare, name="briefing_prepare")
            | RunnableLambda(self._build_sections, name="briefing_sections")
            | self.prompt_template
            | RunnableLambda(_prompt_value_to_text, name="briefing_render_markdown")
            | RunnableLambda(lambda value: value.strip(), name="briefing_strip")
        )

    def generate(
        self,
        competitor: str,
        cards: list[IntelligenceCard],
        snapshot: CapabilitySnapshot | None = None,
        previous_snapshot: CapabilitySnapshot | None = None,
    ) -> str:
        """Render one briefing; the fourth argument is optional and compatible."""

        return self.pipeline.invoke(
            {
                "competitor": competitor,
                "cards": cards,
                "snapshot": snapshot,
                "previous_snapshot": previous_snapshot,
            }
        )

    @staticmethod
    def _load_template(path: Path) -> str:
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError(f"cannot load briefing prompt: {path}") from exc
        if not value:
            raise RuntimeError(f"briefing prompt is empty: {path}")
        return value

    @staticmethod
    def _prepare(payload: dict[str, Any]) -> dict[str, Any]:
        competitor = _plain(payload.get("competitor"))
        if not competitor:
            raise ValueError("competitor must not be blank")
        cards = list(payload.get("cards") or [])
        if any(not isinstance(card, IntelligenceCard) for card in cards):
            raise TypeError("cards must contain IntelligenceCard instances")
        scoped = [
            card
            for card in cards
            if card.competitor.strip().casefold() == competitor.casefold()
        ]
        scoped.sort(
            key=lambda card: (
                -card.priority_score,
                -card.created_at.timestamp(),
                card.card_id,
            )
        )
        snapshot = payload.get("snapshot")
        previous = payload.get("previous_snapshot")
        if snapshot is not None and not isinstance(snapshot, CapabilitySnapshot):
            raise TypeError("snapshot must be a CapabilitySnapshot")
        if previous is not None and not isinstance(previous, CapabilitySnapshot):
            raise TypeError("previous_snapshot must be a CapabilitySnapshot")
        snapshot_matches = snapshot is None or (
            snapshot.competitor.strip().casefold() == competitor.casefold()
        )
        previous_matches = previous is None or (
            previous.competitor.strip().casefold() == competitor.casefold()
        )
        return {
            "competitor": competitor,
            "cards": scoped,
            "snapshot": snapshot if snapshot_matches else None,
            "previous_snapshot": previous if previous_matches else None,
            "snapshot_mismatch": not snapshot_matches,
            "previous_snapshot_mismatch": not previous_matches,
        }

    @classmethod
    def _build_sections(cls, payload: dict[str, Any]) -> dict[str, str]:
        cards: list[IntelligenceCard] = payload["cards"]
        snapshot: CapabilitySnapshot | None = payload["snapshot"]
        previous: CapabilitySnapshot | None = payload["previous_snapshot"]
        evidence, evidence_conflicts = cls._collect_evidence(cards)
        reviews = cls._review_items(
            cards,
            snapshot,
            evidence,
            evidence_conflicts,
            snapshot_mismatch=payload["snapshot_mismatch"],
            previous_snapshot_mismatch=payload["previous_snapshot_mismatch"],
        )
        return {
            "competitor": _plain(payload["competitor"]),
            "briefing_meta": cls._briefing_meta(cards, snapshot),
            "executive_summary": cls._executive_summary(cards, snapshot, evidence, reviews),
            "core_dynamics": cls._core_dynamics(cards),
            "opportunities": cls._statements(
                cards,
                "opportunity",
                "暂无可用机会结论。",
            ),
            "threats": cls._statements(cards, "threat", "暂无可用威胁结论。"),
            "actions": cls._statements(
                cards,
                "recommended_action",
                "暂无可执行行动建议。",
            ),
            "capability_snapshot": cls._capability_snapshot(snapshot, previous),
            "evidence_index": cls._evidence_index(evidence),
            "review_items": "\n".join(f"- {item}" for item in reviews),
        }

    @staticmethod
    def _briefing_meta(
        cards: list[IntelligenceCard],
        snapshot: CapabilitySnapshot | None,
    ) -> str:
        if snapshot is not None:
            window_start = (
                snapshot.window_start.date().isoformat()
                if snapshot.window_start
                else "未指定"
            )
            window_end = (
                snapshot.window_end.date().isoformat()
                if snapshot.window_end
                else str(snapshot.snapshot_date)
            )
            version = _plain(snapshot.product_version) or "未指定"
            return (
                f"快照日期 {snapshot.snapshot_date}；分析窗口 {window_start} 至 {window_end}；"
                f"产品版本 {version}；评分规则 {snapshot.scoring_version}。"
            )
        if cards:
            latest = max(card.created_at for card in cards).date().isoformat()
            return f"基于 {len(cards)} 张情报卡片；最新卡片日期 {latest}；当前无能力快照。"
        return "当前没有情报卡片或能力快照。"

    @staticmethod
    def _executive_summary(
        cards: list[IntelligenceCard],
        snapshot: CapabilitySnapshot | None,
        evidence: list[dict[str, Any]],
        reviews: list[str],
    ) -> str:
        lines = [
            f"- 纳入 **{len(cards)}** 张情报卡片、**{len(evidence)}** 条去重证据；"
            f"当前有 **{len(reviews)}** 项待复核。"
        ]
        if cards:
            lead = cards[0]
            lines.append(
                f"- 最高优先级动态为 **{_plain(lead.event_title)}** "
                f"（{lead.priority_score}/100，{lead.alert_level.value}）。"
            )
            high_priority = sum(card.priority_score >= 80 for card in cards)
            lines.append(f"- 红色优先级动态 **{high_priority}** 项，列表已按优先级降序排列。")
        else:
            lines.append("- 暂无竞品动态，不能形成发布级结论（待复核）。")
        if snapshot is None:
            lines.append("- 七维能力快照缺失，能力判断均视为 insufficient snapshot（待复核）。")
        else:
            lines.append(
                f"- 七维综合分 **{snapshot.total_score:.2f}**，覆盖率 "
                f"**{snapshot.coverage_ratio:.1%}**，总体置信度 "
                f"**{snapshot.overall_confidence:.2f}**。"
            )
        return "\n".join(lines)

    @staticmethod
    def _card_markers(card: IntelligenceCard) -> list[str]:
        markers: list[str] = []
        if card.confidence_score < _LOW_CONFIDENCE:
            markers.append(f"低置信度 {card.confidence_score:.2f}（待复核）")
        if card.conflict_notes:
            markers.append(f"存在 {len(card.conflict_notes)} 项证据冲突（待复核）")
        if not card.evidence:
            markers.append("无证据（待复核）")
        if card.review_required and not markers:
            markers.append("卡片标记为待复核")
        return markers

    @classmethod
    def _core_dynamics(cls, cards: list[IntelligenceCard]) -> str:
        if not cards:
            return "- 暂无可用情报卡片（待复核）。"
        lines: list[str] = []
        for index, card in enumerate(cards, 1):
            dimensions = (
                "、".join(_DIMENSION_LABELS[tag] for tag in card.dimension_tags)
                or "未标注"
            )
            markers = cls._card_markers(card)
            lines.extend(
                [
                    f"{index}. **{_plain(card.event_title)}** — 优先级 "
                    f"{card.priority_score}/100，告警 {card.alert_level.value}，"
                    f"风险 {card.threat_level.value}",
                    f"   - 摘要：{_plain(card.summary)}",
                    f"   - 类型：{card.event_type.value}；能力：{dimensions}",
                    f"   - 置信度：{card.confidence_score:.2f}；证据："
                    f"{len({item.chunk_id for item in card.evidence})} 条",
                ]
            )
            if markers:
                lines.append("   - 标记：" + "；".join(markers))
        return "\n".join(lines)

    @classmethod
    def _statements(
        cls,
        cards: list[IntelligenceCard],
        attribute: str,
        empty_message: str,
    ) -> str:
        seen: set[str] = set()
        lines: list[str] = []
        for card in cards:
            text = _plain(getattr(card, attribute, ""))
            if not text or text.casefold() == "unknown" or text.casefold() in seen:
                continue
            seen.add(text.casefold())
            markers = cls._card_markers(card)
            suffix = f"（{'；'.join(markers)}）" if markers else ""
            lines.append(
                f"- **{_plain(card.event_title)}**：{text}{suffix}"
            )
        return "\n".join(lines) if lines else f"- {empty_message}"

    @staticmethod
    def _detail_map(snapshot: CapabilitySnapshot | None) -> dict[DimensionTag, CapabilityScore]:
        return {item.dimension: item for item in snapshot.details} if snapshot else {}

    @classmethod
    def _capability_snapshot(
        cls,
        snapshot: CapabilitySnapshot | None,
        previous: CapabilitySnapshot | None,
    ) -> str:
        current_by_dimension = cls._detail_map(snapshot)
        previous_by_dimension = cls._detail_map(previous)
        lines = [
            "| 能力维度 | 得分 | 置信度 | 证据数 | 较上期 | 状态 |",
            "|---|---:|---:|---:|---:|---|",
        ]
        for dimension, label in _DIMENSION_LABELS.items():
            detail = current_by_dimension.get(dimension)
            if detail is None or detail.status == CapabilityScoreStatus.INSUFFICIENT_EVIDENCE:
                lines.append(
                    f"| {label} | — | — | {detail.evidence_count if detail else 0} | — | "
                    "insufficient_evidence（证据不足，待复核） |"
                )
                continue
            delta = detail.delta
            previous_detail = previous_by_dimension.get(dimension)
            if delta is None and previous_detail is not None and (
                previous_detail.status == CapabilityScoreStatus.SCORED
            ):
                delta = detail.score - previous_detail.score
            lines.append(
                f"| {label} | {detail.score} | {detail.confidence:.2f} | "
                f"{detail.evidence_count} | {_format_delta(delta)} | scored |"
            )
        if snapshot is None:
            lines.extend(
                [
                    "",
                    "> insufficient snapshot：尚未生成能力快照，七个维度均需补证据和评分。",
                ]
            )
        else:
            lines.extend(
                [
                    "",
                    f"> 综合分 {snapshot.total_score:.2f}；覆盖率 {snapshot.coverage_ratio:.1%}；"
                    f"总体置信度 {snapshot.overall_confidence:.2f}；快照 {snapshot.snapshot_id}。",
                ]
            )
        return "\n".join(lines)

    @staticmethod
    def _collect_evidence(
        cards: list[IntelligenceCard],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        by_id: dict[str, dict[str, Any]] = {}
        conflicts: list[str] = []
        for card in cards:
            for evidence in card.evidence:
                existing = by_id.get(evidence.chunk_id)
                if existing is None:
                    by_id[evidence.chunk_id] = {
                        "evidence": evidence,
                        "card_titles": [_plain(card.event_title)],
                    }
                    continue
                if _plain(card.event_title) not in existing["card_titles"]:
                    existing["card_titles"].append(_plain(card.event_title))
                retained: EvidenceReference = existing["evidence"]
                if (
                    retained.url != evidence.url
                    or retained.version_id != evidence.version_id
                    or retained.title != evidence.title
                ):
                    conflicts.append(
                        f"证据 {evidence.chunk_id} 在不同卡片中元数据不一致，索引保留高优先级卡片版本。"
                    )
        return list(by_id.values()), conflicts

    @staticmethod
    def _safe_url(value: str) -> str | None:
        raw = str(value or "").strip()
        forbidden = ("<", ">", "\r", "\n", "\t", " ")
        if not raw or any(character in raw for character in forbidden):
            return None
        compact = _plain(raw)
        try:
            parsed = urlsplit(compact)
        except ValueError:
            return None
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
            return None
        return compact

    @classmethod
    def _evidence_index(cls, evidence_entries: list[dict[str, Any]]) -> str:
        if not evidence_entries:
            return "- 暂无可引用证据（待复核）。"
        lines = [
            "| chunk_id | 标题 | URL | 等级 | 日期 / 版本 | 关联动态 |",
            "|---|---|---|:---:|---|---|",
        ]
        for entry in evidence_entries:
            evidence: EvidenceReference = entry["evidence"]
            safe_url = cls._safe_url(evidence.url)
            url = (
                f"[{_table_cell(safe_url)}](<{safe_url}>)"
                if safe_url
                else "不安全或无效 URL（待复核）"
            )
            date_text = (
                evidence.publish_time.date().isoformat()
                if evidence.publish_time is not None
                else "日期未知（待复核）"
            )
            version = _plain(evidence.product_version) or "版本未知（待复核）"
            related = "；".join(entry["card_titles"])
            lines.append(
                f"| `{_table_cell(evidence.chunk_id)}` | {_table_cell(evidence.title)} | {url} | "
                f"{evidence.evidence_level.value} | {_table_cell(date_text)} / "
                f"{_table_cell(version)} | "
                f"{_table_cell(related)} |"
            )
        return "\n".join(lines)

    @classmethod
    def _review_items(
        cls,
        cards: list[IntelligenceCard],
        snapshot: CapabilitySnapshot | None,
        evidence_entries: list[dict[str, Any]],
        evidence_conflicts: list[str],
        *,
        snapshot_mismatch: bool,
        previous_snapshot_mismatch: bool,
    ) -> list[str]:
        reviews: list[str] = []
        if not cards:
            reviews.append("没有匹配竞品的情报卡片，不能形成已证实结论。")
        for card in cards:
            title = _plain(card.event_title)
            if card.confidence_score < _LOW_CONFIDENCE:
                reviews.append(f"{title}：置信度 {card.confidence_score:.2f} 低于 0.55。")
            if not card.evidence:
                reviews.append(f"{title}：没有绑定证据。")
            for conflict in card.conflict_notes:
                reviews.append(f"{title}：证据冲突——{_plain(conflict)}")
            for assumption in card.assumptions:
                reviews.append(f"{title}：假设待核验——{_plain(assumption)}")
            if card.review_required and (
                card.confidence_score >= _LOW_CONFIDENCE
                and card.evidence
                and not card.conflict_notes
                and not card.assumptions
            ):
                reviews.append(f"{title}：卡片显式要求人工复核。")
        reviews.extend(evidence_conflicts)
        for entry in evidence_entries:
            evidence: EvidenceReference = entry["evidence"]
            if evidence.publish_time is None:
                reviews.append(f"证据 {evidence.chunk_id} 缺少发布日期。")
            if not evidence.product_version:
                reviews.append(f"证据 {evidence.chunk_id} 缺少产品版本。")
            if cls._safe_url(evidence.url) is None:
                reviews.append(f"证据 {evidence.chunk_id} 的 URL 不安全或无效。")

        if snapshot_mismatch:
            reviews.append("能力快照竞品与简报竞品不一致，已拒绝使用该快照。")
        if previous_snapshot_mismatch:
            reviews.append("上期快照竞品与简报竞品不一致，已拒绝计算变化。")
        if snapshot is None:
            reviews.append("能力快照缺失（insufficient snapshot）。")
        else:
            details = cls._detail_map(snapshot)
            insufficient = [
                _DIMENSION_LABELS[dimension]
                for dimension in DimensionTag
                if dimension not in details
                or details[dimension].status == CapabilityScoreStatus.INSUFFICIENT_EVIDENCE
            ]
            if insufficient:
                reviews.append("能力快照证据不足：" + "、".join(insufficient) + "。")
            if snapshot.coverage_ratio < 1.0:
                reviews.append(f"能力快照覆盖率仅 {snapshot.coverage_ratio:.1%}。")
            if snapshot.overall_confidence < _LOW_CONFIDENCE:
                reviews.append(f"能力快照总体置信度仅 {snapshot.overall_confidence:.2f}。")

        cleaned: list[str] = []
        seen: set[str] = set()
        for item in reviews:
            compact = _plain(item)
            if compact and compact.casefold() not in seen:
                seen.add(compact.casefold())
                cleaned.append(compact)
        if not cleaned:
            cleaned.append("当前无强制复核项；发布前仍建议抽样核验证据链接。")
        return cleaned


__all__ = ["BriefingAgent"]
