"""
提示词模板管理模块
按价格、新品、舆情、简报四大场景拆分
统一使用 LangChain ChatPromptTemplate，便于链式调用
"""

from langchain_core.prompts import ChatPromptTemplate

PRICE_MONITOR_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "你是一位价格情报分析师。请从用户提供的价格相关文本中提取关键信息，并以 JSON 格式返回。",
        ),
        (
            "human",
            """\
请分析以下文本：

{text}

输出格式：
{{
  "product": "产品名称",
  "price_change": "价格变动描述",
  "promotion": "促销活动",
  "impact": "对竞争格局的影响"
}}
""",
        ),
    ]
)

NEW_PRODUCT_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "你是一位产品情报分析师。请从用户提供的文本中提取竞品新品/功能迭代信息，并以 JSON 格式返回。",
        ),
        (
            "human",
            """\
请分析以下文本：

{text}

输出格式：
{{
  "product": "产品名称",
  "update_type": "新品发布 / 功能迭代",
  "key_features": ["核心特性"],
  "market_positioning": "市场定位"
}}
""",
        ),
    ]
)

SENTIMENT_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "你是一位舆情分析师。请对用户提供竞品相关文本进行负面舆情识别，并以 JSON 格式返回。",
        ),
        (
            "human",
            """\
请分析以下文本：

{text}

输出格式：
{{
  "is_negative": true / false,
  "negative_topics": ["负面主题"],
  "severity": "高 / 中 / 低",
  "suggested_response": "建议回应方向"
}}
""",
        ),
    ]
)

BRIEFING_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "你是一位战略分析师。请基于多维度竞品情报生成结构化的竞争态势简报。",
        ),
        (
            "human",
            """\
请基于以下情报生成 Markdown 格式简报：

## 价格战情报
{price_intel}

## 新品/迭代情报
{product_intel}

## 舆情情报
{sentiment_intel}

要求：
1. 总结核心变化与威胁
2. 给出 SWOT 式对标要点
3. 输出可直接汇报的 Markdown 格式
""",
        ),
    ]
)
