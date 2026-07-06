# 基于 Claude Skill 的竞品动态追踪与智能对标分析系统

## 项目简介
本项目是 20 天实训项目，基于 **Anthropic Claude + LangChain + FastAPI** 构建，聚焦价格战、新品发布、负面舆情三大维度，实现竞品情报的自动采集、分析、对标与简报生成。

## 环境要求
- Python 3.10 / 3.11 / 3.12（本项目使用 3.12）
- Conda 虚拟环境 `competitor-intel`

## 快速开始

### 1. 激活 Conda 环境
```bash
conda activate competitor-intel
```

### 2. 安装依赖
```bash
pip install -r requirements.txt
```

### 3. 配置 API 密钥
```bash
cp .env.example .env
```
然后编辑 `.env`，填入你的 Claude API 密钥。

### 4. 测试 Claude API 连通性
```bash
python test_anthropic.py
```

### 5. 启动 FastAPI 服务
```bash
python -m uvicorn modules.api_server:app --reload
```
启动后访问 Swagger 文档：http://127.0.0.1:8000/docs

## 项目目录说明
```
competitor-analysis-system/
├── requirements.txt      # Python 依赖清单
├── environment.yml       # Conda 环境配置文件
├── .env.example          # 环境变量模板
├── .gitignore
├── README.md
├── data/
│   └── raw/              # 原始采集数据
├── chroma_db/            # 向量数据库持久化目录
├── memory/               # 历史会话/记忆持久化目录
├── modules/              # 核心功能模块
│   ├── data_loader.py    # 数据采集与 DocumentLoader
│   ├── llm_client.py     # Claude 模型统一封装
│   ├── prompts.py        # 提示词模板管理
│   ├── rag_chain.py      # RAG 检索增强链
│   ├── tools.py          # Agent 工具函数
│   ├── agent_core.py     # 多 Agent 调度逻辑
│   └── api_server.py     # FastAPI 接口服务
├── test_anthropic.py     # Claude API 连通测试
└── docs/                 # 实训过程文档
```

## 开发规范
- 所有 API 密钥与敏感配置必须写入 `.env`，禁止硬编码。
- Prompt 模板统一放入 `modules/prompts.py`。
- 竞品分析结论必须绑定原始数据源，禁止凭空编造。
- 每日填写《程序员日志》与《项目例会记录表》。
