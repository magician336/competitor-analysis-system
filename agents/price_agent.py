"""Price and commercialization Agent."""

from __future__ import annotations

from schemas.document import DimensionTag, EventType
from schemas.intelligence_card import AgentKind

from .base import AgentConfig, EvidenceBackedAgent


class PriceAgent(EvidenceBackedAgent):
    config = AgentConfig(
        kind=AgentKind.PRICE,
        event_type=EventType.PRICING_CHANGE,
        prompt_file="price_prompt.md",
        default_question=(
            "{competitor} 最近有哪些价格、套餐、额度、学生优惠、团队版或计费政策变化？"
        ),
        dimension_focus=(
            DimensionTag.PERFORMANCE_COST,
            DimensionTag.MODEL_EXTENSIBILITY,
            DimensionTag.SECURITY_COMPLIANCE,
            DimensionTag.EDUCATION_FIT,
        ),
        event_label="商业化与价格变化",
        impact_label=(
            "已检索到 {evidence_count} 条商业化证据，可用于判断套餐门槛、"
            "额度限制和单位任务成本对 CodeMate Campus 的影响。"
        ),
        opportunity_label=(
            "若竞品提高门槛或弱化教育优惠，可突出校园低成本、透明额度和学生认证方案。"
        ),
        threat_label=(
            "若竞品提供更强模型或更低学生价，可能压缩 CodeMate Campus 的价格优势。"
        ),
        action_label=(
            "补充价格页快照，记录套餐、额度、学生政策和模型差异，并更新成本效率评分。"
        ),
        base_impact=0.66,
    )


__all__ = ["PriceAgent"]
