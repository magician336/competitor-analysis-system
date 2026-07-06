"""
多 Agent 注册与调度模块
基于 LangChain create_tool_calling_agent + AgentExecutor 实现
"""

import logging
from typing import Dict, Any, List

from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from modules.llm_client import LLMClient
from modules.tools import search_price_intel, search_product_intel, search_sentiment_intel

logger = logging.getLogger(__name__)

# Agent 默认配置
AGENT_CONFIG = {
    "price": {
        "name": "price",
        "description": "价格监控 Agent：负责抓取并分析竞品价格变动、促销活动、套餐优惠等情报。",
        "model_tier": "haiku",
        "tools": [search_price_intel],
        "system_prompt": (
            "你是价格监控专家。请使用工具查询竞品价格情报，并对结果进行结构化总结。"
            "优先返回 JSON 格式：product、price_change、promotion、impact。"
        ),
    },
    "product": {
        "name": "product",
        "description": "新品检测 Agent：负责追踪竞品新品发布、版本更新、功能迭代等情报。",
        "model_tier": "sonnet",
        "tools": [search_product_intel],
        "system_prompt": (
            "你是产品情报专家。请使用工具查询竞品新品情报，提炼关键特性与市场定位。"
            "优先返回 JSON 格式：product、update_type、key_features、market_positioning。"
        ),
    },
    "sentiment": {
        "name": "sentiment",
        "description": "舆情分析 Agent：负责抓取并分析竞品相关的用户投诉、媒体报道、负面舆情。",
        "model_tier": "sonnet",
        "tools": [search_sentiment_intel],
        "system_prompt": (
            "你是舆情分析专家。请使用工具查询竞品舆情情报，识别负面主题并评估严重程度。"
            "优先返回 JSON 格式：is_negative、negative_topics、severity、suggested_response。"
        ),
    },
}


def build_agent_executor(agent_name: str) -> AgentExecutor:
    """
    根据 Agent 名称构建 AgentExecutor
    """
    config = AGENT_CONFIG.get(agent_name)
    if not config:
        raise ValueError(f"未知 Agent: {agent_name}")

    llm = LLMClient(model_tier=config["model_tier"])._llm
    tools = config["tools"]

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", config["system_prompt"]),
            ("human", "{input}"),
            MessagesPlaceholder("agent_scratchpad"),
        ]
    )

    agent = create_tool_calling_agent(llm, tools, prompt)
    return AgentExecutor(agent=agent, tools=tools, verbose=True)


def dispatch(agent_name: str, query: str) -> Dict[str, Any]:
    """
    根据 Agent 名称分发任务
    """
    try:
        if agent_name not in AGENT_CONFIG:
            return {"error": f"Agent {agent_name} 不存在，可用: {list(AGENT_CONFIG.keys())}"}

        logger.info(f"调度 Agent: {agent_name}, 输入: {query}")
        executor = build_agent_executor(agent_name)
        result = executor.invoke({"input": query})
        return {"agent": agent_name, "query": query, "result": result}
    except Exception as e:
        logger.error(f"Agent 调度失败 {agent_name}: {e}")
        return {"error": str(e)}


def list_agents() -> List[Dict[str, str]]:
    """列出所有可用 Agent"""
    return [
        {"name": config["name"], "description": config["description"]}
        for config in AGENT_CONFIG.values()
    ]
