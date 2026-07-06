"""
Claude 模型统一封装模块
按模型分层调用规范：Haiku / Sonnet / Opus
基于 langchain_anthropic.ChatAnthropic 实现
"""

import os
import logging
from typing import Optional

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

load_dotenv()
logger = logging.getLogger(__name__)


def get_api_key() -> Optional[str]:
    """从环境变量读取 Anthropic API 密钥"""
    return os.getenv("ANTHROPIC_API_KEY")


def get_base_url() -> Optional[str]:
    """从环境变量读取 Anthropic Base URL（中转平台可选）"""
    return os.getenv("ANTHROPIC_BASE_URL")


def get_model_name(model_tier: str = "sonnet") -> str:
    """
    根据层级获取模型名称
    :param model_tier: haiku | sonnet | opus
    """
    env_map = {
        "haiku": "MODEL_HAIKU",
        "sonnet": "MODEL_SONNET",
        "opus": "MODEL_OPUS",
    }
    env_key = env_map.get(model_tier, "MODEL_SONNET")
    return os.getenv(env_key, "claude-3-7-sonnet-20250219")


class LLMClient:
    """Claude LLM 客户端，封装 langchain_anthropic.ChatAnthropic"""

    def __init__(self, model_tier: str = "sonnet", temperature: float = 0.0, max_tokens: int = 1024):
        self.model_tier = model_tier
        self.model_name = get_model_name(model_tier)
        self.api_key = get_api_key()
        self.base_url = get_base_url()
        self.temperature = temperature
        self.max_tokens = max_tokens

        if not self.api_key:
            logger.warning("ANTHROPIC_API_KEY 未设置，模型调用将失败")

        self._llm = ChatAnthropic(
            model=self.model_name,
            api_key=self.api_key,
            base_url=self.base_url,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )

    def invoke(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """
        调用 Claude 模型
        :param prompt: 用户输入
        :param system_prompt: 可选的系统提示词
        :return: 模型生成的文本内容
        """
        try:
            messages = []
            if system_prompt:
                messages.append(SystemMessage(content=system_prompt))
            messages.append(HumanMessage(content=prompt))

            response = self._llm.invoke(messages)
            return response.content
        except Exception as e:
            logger.error(f"模型调用失败 [{self.model_tier}]: {e}")
            raise
