"""
Claude API 连通性测试脚本
基于 langchain_anthropic.ChatAnthropic 实现
运行前请确保已配置 .env 文件并安装依赖
"""

import os
import logging
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    # 加载环境变量
    load_dotenv()

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key or api_key == "sk-ant-你的ClaudeAPI密钥":
        logger.error("请先配置 .env 文件中的 ANTHROPIC_API_KEY")
        return

    model_name = os.getenv("MODEL_SONNET", "claude-3-7-sonnet-20250219")
    logger.info(f"准备测试模型: {model_name}")

    try:
        from langchain_anthropic import ChatAnthropic
        from langchain_core.messages import HumanMessage

        llm = ChatAnthropic(
            model=model_name,
            api_key=api_key,
            base_url=os.getenv("ANTHROPIC_BASE_URL"),
            temperature=0.0,
            max_tokens=512,
        )
        res = llm.invoke([HumanMessage(content="简单介绍竞品情报分析系统的作用")])
        print("模型输出：")
        print(res.content)
    except ImportError:
        logger.error("依赖未安装，请执行: pip install -r requirements.txt")
    except Exception as e:
        logger.error(f"API 测试失败: {e}")


if __name__ == "__main__":
    main()
