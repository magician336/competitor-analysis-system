"""
FastAPI 接口服务模块
"""

import logging
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from modules.agent_core import dispatch, list_agents

logger = logging.getLogger(__name__)
app = FastAPI(title="竞品情报分析系统", version="0.1.0")


class QueryRequest(BaseModel):
    query: str
    agent: str = "price"


@app.get("/")
def root():
    """健康检查接口"""
    return {"message": "竞品情报分析系统已启动", "version": "0.1.0"}


@app.get("/api/agents")
def get_agents():
    """获取所有可用 Agent 列表"""
    return {"agents": list_agents()}


@app.post("/api/analyze")
def analyze(request: QueryRequest):
    """竞品分析接口：调度指定 Agent 处理查询"""
    try:
        logger.info(f"收到分析请求: agent={request.agent}, query={request.query}")
        result = dispatch(request.agent, request.query)

        if "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"分析接口异常: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/health")
def health():
    """服务健康检查"""
    return {"status": "ok"}
