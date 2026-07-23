"""Product intelligence Agent."""

from __future__ import annotations

from schemas.document import DimensionTag, EventType
from schemas.intelligence_card import AgentKind

from .base import AgentConfig, EvidenceBackedAgent


class ProductAgent(EvidenceBackedAgent):
    config = AgentConfig(
        kind=AgentKind.PRODUCT,
        event_type=EventType.PRODUCT_RELEASE,
        prompt_file="product_prompt.md",
        default_question=(
            "{competitor} 最近有哪些模型接入、Agent、IDE、代码库理解或工具链能力发布？"
        ),
        dimension_focus=(
            DimensionTag.CODE_INTELLIGENCE,
            DimensionTag.AGENT_CONTEXT,
            DimensionTag.IDE_ECOSYSTEM,
            DimensionTag.MODEL_EXTENSIBILITY,
            DimensionTag.EDUCATION_FIT,
        ),
        event_label="产品与技术发布",
        impact_label=(
            "已检索到 {evidence_count} 条产品证据，可用于分析代码智能、"
            "项目上下文、Agent 自主性和 IDE 生态的竞争变化。"
        ),
        opportunity_label=(
            "将通用 Agent 能力翻译成课程实验、中文报错讲解和分步学习模式，可形成教育场景差异化。"
        ),
        threat_label=(
            "若竞品的多文件 Agent、测试修复或 IDE 集成成熟，CodeMate Campus 需要避免功能代差。"
        ),
        action_label=(
            "按 D1-D7 标签复核发布证据，抽取可复现功能点，并纳入能力快照和基准任务。"
        ),
        base_impact=0.72,
    )


__all__ = ["ProductAgent"]
