"""Sentiment, developer experience and risk Agent."""

from __future__ import annotations

from schemas.document import DimensionTag, EventType
from schemas.intelligence_card import AgentKind

from .base import AgentConfig, EvidenceBackedAgent


class RiskAgent(EvidenceBackedAgent):
    config = AgentConfig(
        kind=AgentKind.RISK,
        event_type=EventType.RISK_EXPERIENCE,
        prompt_file="risk_prompt.md",
        default_question=(
            "{competitor} 最近有哪些开发者体验、Agent 失控、隐私、安全、服务稳定或负面舆情问题？"
        ),
        dimension_focus=(
            DimensionTag.AGENT_CONTEXT,
            DimensionTag.PERFORMANCE_COST,
            DimensionTag.SECURITY_COMPLIANCE,
            DimensionTag.EDUCATION_FIT,
            DimensionTag.CODE_INTELLIGENCE,
        ),
        event_label="开发者体验与风险舆情",
        impact_label=(
            "已检索到 {evidence_count} 条风险证据，可用于判断稳定性、"
            "安全隐私、Agent 行为边界和教育场景风险。"
        ),
        opportunity_label=(
            "竞品风险可转化为 CodeMate Campus 的安全边界、教师可控和学术诚信设计卖点。"
        ),
        threat_label=(
            "若同类风险在本产品中复现，会直接影响学生代码安全、课程合规和教师信任。"
        ),
        action_label=(
            "建立风险复核清单，优先验证危险命令、代码上传、错误讲解和直接代写边界。"
        ),
        base_impact=0.78,
    )


__all__ = ["RiskAgent"]
