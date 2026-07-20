# AI 编程助手竞品动态追踪与智能对标分析系统项目规划

## 0. 本次调整说明

本版正式确定以 **AI 编程助手 / AI IDE / 代码生成与软件开发 Agent** 为竞品赛道。

调整重点不是简单增加若干通用指标，而是将原有“价格战、新品发布、负面舆情”三类监控要求，重构为一套更适合 AI 编程助手的 **双层维度体系**：

1. **事件监控层**：保留实训要求中的价格变化、产品发布、负面舆情三类动态，用于判断“发生了什么”。
2. **能力对标层**：增加代码智能、项目级上下文、Agent 能力、IDE 生态、模型扩展、性能成本、安全隐私和教育适配等专业维度，用于判断“这一变化具体影响了什么能力”。

因此，一条竞品情报不再只标注为“新品”或“舆情”，而是同时具有：

```text
事件类型：product_release
能力标签：agent_context、code_intelligence、ide_ecosystem
```

这种设计既满足原项目考核要求，又能体现 AI 编程助手选题的专业性。

---

## 1. 项目名称

**CodeRadar：AI 编程助手竞品动态追踪与智能对标分析系统**

---

## 2. 项目定位

本项目面向 **AI 编程助手、AI IDE、代码生成工具和软件开发 Agent** 赛道，构建一个能够持续采集竞品信息、识别产品动态、分析专业能力差异并生成竞争态势简报的智能系统。

系统以大模型为分析核心，结合 LangChain、RAG、Multi-Agent、FastAPI 和前端可视化，实现以下完整链路：

```text
多源数据采集
    ↓
竞品与版本识别
    ↓
事件类型分类
    ↓
AI 编程能力维度标注
    ↓
证据检索与可信度评估
    ↓
本产品对标分析
    ↓
机会点、威胁点与行动建议
```

本项目不将 AI 编程助手简单视为普通 SaaS 产品，而是重点分析其在代码生成、代码库理解、Agent 自动执行、开发工具集成、隐私安全和教育场景等方面的竞争差异。

---

## 3. 产品选择

### 3.1 本企业产品设定

本项目虚拟一个自研产品：

> **CodeMate Campus：面向高校学生的 AI 编程学习助手**

### 3.2 产品定位

CodeMate Campus 面向高校计算机相关专业学生，重点服务课程学习、编程实验、错误诊断、算法理解和实验报告整理等场景。

其差异化定位不是与 Cursor 等产品正面竞争“通用 AI IDE”，而是强调：

- 中文课程内容理解；
- C / C++ / Python / Java 等教学语言支持；
- 编译错误与运行错误解释；
- 分步骤算法讲解；
- 编程实验辅助；
- 学习过程引导；
- 学术诚信与答案边界控制。

### 3.3 核心功能设定

| 功能模块 | 功能说明 |
|---|---|
| 代码问答 | 回答课程代码、语法、接口和工程问题 |
| 报错诊断 | 解释编译错误、运行错误、依赖冲突和环境配置问题 |
| 算法讲解 | 结合伪代码、复杂度和示例讲解算法 |
| 实验辅助 | 根据实验要求、代码和结果辅助整理实验过程 |
| 代码审查 | 分析正确性、可读性、性能和潜在缺陷 |
| 项目上下文理解 | 读取多个文件并分析模块关系 |
| 学习模式 | 优先给出提示、推导和纠错，而不是直接输出最终答案 |
| IDE 扩展 | 后续扩展为 VS Code 插件或 Web IDE 插件 |

---

## 4. 竞品选择

首期选择 5 个具有代表性的 AI 编程产品。

| 竞品名称 | 类型 | 选择理由 |
|---|---|---|
| Cursor | AI IDE | Agent、多文件编辑和代码库理解能力具有代表性 |
| GitHub Copilot | AI 编程助手 | 用户规模大，IDE 与 GitHub 生态完整 |
| Trae | AI IDE / 编程助手 | 国内产品，中文环境和公开资料较丰富 |
| 通义灵码 | 国内代码助手 | 中文开发场景、大厂生态和企业服务具有代表性 |
| CodeGeeX | 代码生成工具 | 国内代码大模型与开源生态代表产品 |

后续可扩展：

- Windsurf；
- JetBrains AI Assistant；
- Replit Agent；
- 豆包 MarsCode；
- 文心快码；
- Amazon Q Developer。

---

## 5. 项目目标

### 5.1 总体目标

构建一套面向 AI 编程助手赛道的竞品动态追踪与智能对标系统，能够回答以下问题：

1. 某竞品最近发生了什么重要变化？
2. 该变化属于价格、发布还是风险事件？
3. 该变化影响了哪些 AI 编程能力？
4. 与 CodeMate Campus 相比，该变化构成机会还是威胁？
5. 判断依据来自哪些可追溯证据？
6. 产品团队应采取什么行动？

### 5.2 核心成果

项目最终应完成：

1. 竞品主体与版本管理；
2. 多源数据采集与清洗；
3. 三类事件动态识别；
4. 七类 AI 编程能力标签识别；
5. RAG 竞品知识库；
6. Multi-Agent 分析流程；
7. AI 编程助手能力对标矩阵；
8. 竞品评分与变化趋势；
9. 可视化看板；
10. 竞争态势简报；
11. FastAPI 服务接口；
12. Docker 部署与结项文档。

---

## 6. 监控与对标维度设计

## 6.1 双层维度模型

系统使用两套互补维度。

### 第一层：事件监控维度

用于识别竞品动态的事件性质，保留项目要求中的三类核心事件。

| 编码 | 事件维度 | 主要回答的问题 |
|---|---|---|
| E1 | 商业化与价格变化 | 产品如何收费，使用门槛是否变化 |
| E2 | 产品与技术发布 | 产品新增了什么功能、模型或集成 |
| E3 | 开发者体验与风险舆情 | 用户遇到了什么问题，是否存在风险 |

### 第二层：AI 编程助手能力维度

用于将事件映射到具体产品能力，形成专业化对标。

| 编码 | 能力维度 | 建议权重 |
|---|---|---:|
| D1 | 代码智能与生成质量 | 20% |
| D2 | 项目级上下文与 Agent 自主性 | 18% |
| D3 | IDE、工具链与开发生态 | 12% |
| D4 | 模型接入与扩展能力 | 10% |
| D5 | 性能、稳定性与成本效率 | 10% |
| D6 | 安全、隐私与企业合规 | 12% |
| D7 | 用户体验与教育场景适配 | 18% |

总权重为 100%。权重可在系统配置页中调整。

---

## 6.2 E1：商业化与价格变化

原“价格战”维度调整为 **商业化与使用成本**，避免只关注订阅价格。

| 子指标 | 监控内容 |
|---|---|
| 免费使用门槛 | 免费请求次数、试用期、免费模型限制 |
| 订阅价格 | 月付、年付、团队版和企业版价格 |
| 请求与额度限制 | 高级请求、快速请求、Token 或调用次数 |
| 学生政策 | 学生认证、教育优惠、校园计划 |
| 模型差异 | 不同套餐可使用的模型和上下文长度 |
| 团队功能 | 组织管理、权限控制、审计和计费 |
| 隐性成本 | 超额计费、按量计费、功能锁定 |
| 单任务成本 | 完成一次代码生成、修复或 Agent 任务的估算成本 |

该维度既监控价格变化，也分析“价格对应的实际可用能力”。

---

## 6.3 E2：产品与技术发布

原“新品发布”维度调整为 **产品与技术能力演进**。

| 子指标 | 监控内容 |
|---|---|
| 新模型接入 | Claude、GPT、Gemini、DeepSeek、Qwen 等 |
| 代码补全 | 行级补全、块级补全、预测编辑 |
| Chat 能力 | 代码问答、文件引用、错误解释 |
| Agent 能力 | 规划、编辑、运行、测试、修复和回滚 |
| 代码库理解 | 项目索引、跨文件检索、依赖关系理解 |
| 终端工具 | 命令生成、执行确认、错误修复 |
| 测试能力 | 自动生成测试、执行测试、根据失败结果修复 |
| 代码审查 | Pull Request 审查、漏洞分析、重构建议 |
| 规则与记忆 | 项目规则、自定义指令、跨会话记忆 |
| 协议与插件 | MCP、插件、扩展市场、外部工具调用 |
| IDE 支持 | VS Code、JetBrains、独立 IDE、Web IDE |
| 企业能力 | 团队知识库、权限、审计、私有部署 |

---

## 6.4 E3：开发者体验与风险舆情

原“负面舆情”维度调整为 **开发者体验、可信度和风险事件**。

| 子指标 | 监控内容 |
|---|---|
| 代码正确性 | 编译失败、测试失败、逻辑错误、虚构 API |
| Agent 失控 | 错误修改文件、删除代码、执行危险命令 |
| 上下文失效 | 忽略项目规则、引用错误文件、跨文件理解错误 |
| 性能问题 | 响应慢、补全延迟、IDE 卡顿、服务中断 |
| 计费争议 | 额度消耗异常、价格不透明、自动续费争议 |
| 隐私风险 | 源代码上传、数据保留、训练用途不明确 |
| 安全风险 | 密钥泄露、危险代码、供应链和插件风险 |
| 用户体验 | 干扰式补全、配置复杂、学习成本高 |
| 企业合规 | 权限、审计、数据区域和合规认证不足 |
| 教育风险 | 直接代写、错误讲解、学术诚信问题 |

---

## 6.5 D1：代码智能与生成质量

该维度衡量 AI 编程助手最基础、最核心的代码能力。

| 指标 | 说明 |
|---|---|
| 语法正确率 | 生成代码能否通过解析或编译 |
| 单元测试通过率 | 代码是否满足题目或需求 |
| API 使用正确性 | 是否调用真实、匹配版本的 API |
| Bug 修复能力 | 能否定位并修复已有代码中的问题 |
| 重构能力 | 是否保持行为一致并改善结构 |
| 代码解释能力 | 是否准确解释逻辑、复杂度和缺陷 |
| 多语言支持 | 对 C、C++、Python、Java 等语言的支持 |
| 代码风格 | 可读性、命名、模块化和注释质量 |

可选基准任务：

- 函数补全；
- 编译错误修复；
- 单元测试驱动修复；
- 算法实现；
- 代码重构；
- 代码解释；
- 安全缺陷识别。

---

## 6.6 D2：项目级上下文与 Agent 自主性

该维度区分普通“代码问答工具”和真正的“开发 Agent”。

| 指标 | 说明 |
|---|---|
| 代码库索引 | 是否能够建立项目级语义索引 |
| 跨文件理解 | 是否理解调用链、模块依赖和数据流 |
| 上下文选择 | 是否能够自动选取相关文件 |
| 多文件编辑 | 是否能够协调修改多个文件 |
| 任务规划 | 是否先分解任务并形成执行步骤 |
| 工具调用 | 是否调用终端、搜索、测试、Git 等工具 |
| 自动验证 | 修改后是否主动编译、运行或测试 |
| 错误恢复 | 工具失败后是否能够调整方案 |
| 人工确认 | 高风险操作是否请求用户确认 |
| 变更可追踪 | 是否提供 diff、检查点、撤销和回滚 |

---

## 6.7 D3：IDE、工具链与开发生态

| 指标 | 说明 |
|---|---|
| IDE 覆盖 | VS Code、JetBrains、Visual Studio、独立 IDE |
| 操作系统 | Windows、macOS、Linux 支持情况 |
| 终端集成 | 命令执行、日志分析和 Shell 支持 |
| Git 集成 | diff、commit、branch、PR 工作流 |
| CI/CD 集成 | GitHub Actions、GitLab CI 等 |
| 插件生态 | 插件数量、开放接口和第三方扩展 |
| 团队协作 | 共享规则、团队知识、组织管理 |
| 迁移成本 | 从现有 IDE 或工具迁移的难度 |

---

## 6.8 D4：模型接入与扩展能力

| 指标 | 说明 |
|---|---|
| 模型数量 | 支持的闭源和开源模型 |
| 模型切换 | 是否可按任务切换模型 |
| BYOK | 是否支持用户自带 API Key |
| 自定义模型 | 是否支持本地模型或企业模型 |
| 上下文长度 | 可使用的有效上下文规模 |
| 自定义规则 | 项目规则、系统提示和代码规范 |
| MCP / Tool | 是否支持 MCP 或外部工具协议 |
| API 开放性 | 是否提供 SDK、API 或扩展接口 |
| 模型透明度 | 是否明确当前使用的模型与限制 |

---

## 6.9 D5：性能、稳定性与成本效率

| 指标 | 说明 |
|---|---|
| 首次响应延迟 | 从请求到开始输出的时间 |
| 补全延迟 | 编辑器内代码建议的响应速度 |
| Agent 完成时间 | 完成完整开发任务所需时间 |
| 服务可用性 | 故障频率和状态页记录 |
| 大项目性能 | 大型代码库索引和检索表现 |
| 资源占用 | CPU、内存和网络占用 |
| 请求成功率 | 超时、限流和失败情况 |
| 单任务成本 | 完成可验证任务的实际成本 |
| 额度利用率 | 有效结果占额度消耗的比例 |

---

## 6.10 D6：安全、隐私与企业合规

| 指标 | 说明 |
|---|---|
| 数据保留政策 | 上传代码和提示词保留时间 |
| 训练数据使用 | 用户数据是否用于模型训练 |
| 退出训练 | 是否支持关闭数据训练用途 |
| 本地与私有部署 | 是否支持本地模型、VPC 或私有化 |
| 传输与存储加密 | 数据安全措施 |
| 权限控制 | 团队、项目和成员权限 |
| 审计日志 | 是否记录模型和 Agent 操作 |
| 密钥保护 | 是否检测并避免泄露凭证 |
| 危险命令控制 | 是否限制高风险终端操作 |
| 合规认证 | SOC 2、ISO 27001 等公开合规信息 |

---

## 6.11 D7：用户体验与教育场景适配

该维度用于体现 CodeMate Campus 的差异化定位。

| 指标 | 说明 |
|---|---|
| 中文理解 | 中文需求、报错和课程术语理解 |
| 教学语言覆盖 | C、C++、Python、Java 等课程语言 |
| 报错讲解 | 是否说明错误原因和修复思路 |
| 分步骤引导 | 是否提供提示、推导和渐进式帮助 |
| 算法讲解 | 是否解释复杂度、边界条件和示例 |
| 实验适配 | 是否理解实验题目、代码和运行结果 |
| 学习模式 | 是否避免直接代写并鼓励自主完成 |
| 学术诚信 | 是否支持答案边界、引用和风险提示 |
| 初学者体验 | 安装、配置、交互和学习成本 |
| 教师可控性 | 是否支持课程规则、教师配置和使用记录 |

---

## 6.12 事件与能力维度映射示例

| 竞品动态 | 事件类型 | 能力标签 |
|---|---|---|
| Cursor 发布新的多文件 Agent | E2 产品与技术发布 | D2 项目上下文、D3 工具链 |
| Copilot 调整高级请求额度 | E1 商业化与价格 | D5 成本效率、D4 模型接入 |
| 用户投诉 Agent 删除未提交代码 | E3 风险舆情 | D2 Agent 自主性、D6 安全 |
| 通义灵码新增中文报错解释 | E2 产品与技术发布 | D1 代码智能、D7 教育适配 |
| 某产品发生大规模服务中断 | E3 风险舆情 | D5 稳定性 |
| 某产品宣布不使用客户代码训练 | E2 产品政策更新 | D6 隐私合规 |

---

## 7. 数据源规划

### 7.1 数据源分类

| 数据源类型 | 具体内容 | 主要用途 |
|---|---|---|
| 官网与博客 | 产品公告、发布文章、企业说明 | 产品发布和战略变化 |
| Changelog / Release Notes | 版本记录、功能变更 | 功能增量和版本时间线 |
| Pricing 页面 | 套餐、额度和企业版政策 | 商业化监控 |
| 产品文档 | 模型列表、上下文、Agent、隐私说明 | 能力事实核验 |
| 状态页 | 服务故障、恢复时间 | 稳定性分析 |
| GitHub | Issues、仓库、Release、讨论区 | Bug、生态和用户反馈 |
| 插件市场 | VS Code、JetBrains 插件评价 | IDE 体验和版本反馈 |
| 社区平台 | Reddit、知乎、CSDN、掘金、Hacker News | 舆情和真实使用问题 |
| 视频与测评 | B站、YouTube、技术博客 | 使用过程和横向评测 |
| 安全与隐私页面 | Trust Center、隐私政策、合规说明 | 安全与企业能力 |
| 基准测试数据 | 编程题集、修复任务、人工测试结果 | 代码能力对标 |

### 7.2 证据等级

系统对信息来源进行分级，避免将营销宣传和用户个例等同处理。

| 等级 | 来源 | 建议可信度 |
|---|---|---:|
| A | 官方文档、价格页、Changelog、状态页 | 0.90–1.00 |
| B | 可复现测试、公开仓库、权威技术媒体 | 0.75–0.90 |
| C | 多名用户一致反馈、专业测评 | 0.55–0.75 |
| D | 单条评论、未验证截图、二手转载 | 0.20–0.55 |

### 7.3 数据采集方式

| 方式 | 说明 |
|---|---|
| 手工导入 | 使用 CSV / JSON 录入基准测试和难抓取页面 |
| requests + BeautifulSoup | 采集静态官网、博客和文档 |
| RSS Feed | 采集博客、新闻和版本更新 |
| GitHub API | 获取 Issues、Release 和仓库信息 |
| Document Loader | 通过 LangChain 加载网页、PDF、RSS 等 |
| 定时任务 | 使用 APScheduler 执行周期性采集 |
| 页面快照与哈希 | 比较价格页、模型页和文档变化 |
| 人工复核 | 对高风险情报和低置信度事件进行确认 |

---

## 8. 系统总体架构

```text
CodeRadar 系统架构

数据源层
├── 官网 / Blog / Changelog
├── Pricing 页面
├── 产品文档与隐私政策
├── GitHub Issues / Release
├── 插件市场与社区评论
├── 状态页
└── 编程基准测试数据

采集与处理层
├── 网页与 API 采集
├── 文本清洗和去重
├── 时间与版本标准化
├── 竞品实体识别
├── 事件类型分类
├── 能力维度多标签分类
├── 证据等级判断
└── 结构化存储

RAG 知识库层
├── 文本切分
├── Embedding
├── Chroma / FAISS
├── 元数据过滤
├── 混合检索
└── 证据引用追踪

Multi-Agent 分析层
├── Price Agent
├── Product Intelligence Agent
├── Sentiment & Risk Agent
├── Benchmark Agent
├── Compare Agent
└── Briefing Agent

服务层
├── FastAPI
├── API Key 鉴权
├── 定时任务
├── 日志和追踪
├── 缓存与重试
└── 异常处理

前端展示层
├── 竞品与维度配置
├── 动态情报卡片
├── 能力雷达图
├── 功能与生态矩阵
├── 价格和额度趋势
├── 版本发布时间线
├── 风险与故障趋势
├── 基准测试对比
└── 竞争态势简报
```

---

## 9. 核心功能模块

### 9.1 竞品配置模块

功能：

- 添加、编辑和删除竞品；
- 设置产品类型；
- 配置官网、Changelog、Pricing、GitHub 等数据源；
- 设置监控优先级；
- 设置事件维度；
- 设置能力维度权重；
- 设置关键词和触发阈值；
- 记录当前版本和主要套餐；
- 设置是否纳入基准测试。

### 9.2 数据采集与清洗模块

处理流程：

1. 采集网页、RSS、API 或手工数据；
2. 解析标题、正文、时间、作者和来源；
3. 去除广告、导航、重复内容；
4. 识别竞品、产品版本和套餐；
5. 判断事件类型；
6. 标注一个或多个能力维度；
7. 评估证据等级和置信度；
8. 保存原文、快照和结构化结果。

结构化文档示例：

```json
{
  "id": "doc_001",
  "competitor": "Cursor",
  "product_version": "x.y",
  "event_type": "product_release",
  "dimension_tags": [
    "agent_context",
    "ide_ecosystem"
  ],
  "title": "Cursor 发布新的多文件 Agent 功能",
  "content": "……",
  "source_type": "official_changelog",
  "evidence_level": "A",
  "url": "https://example.com",
  "publish_time": "2026-04-01",
  "crawl_time": "2026-04-02",
  "content_hash": "..."
}
```

### 9.3 RAG 知识库模块

功能：

- 按竞品、版本、事件类型和能力维度切分文档；
- 生成向量并存入 Chroma 或 FAISS；
- 支持关键词与向量混合检索；
- 支持按时间、来源等级和竞品过滤；
- 为分析结论返回原始证据；
- 避免不同版本、不同套餐的信息混淆；
- 对相互矛盾的信息返回冲突提示。

检索示例：

```text
查询：
Cursor 最近三个月在 Agent 自主执行能力上有哪些变化？

过滤条件：
competitor = Cursor
dimension = agent_context
publish_time >= 最近三个月

返回：
1. 官方 Changelog
2. 产品文档变化
3. GitHub Issue 或用户反馈
4. 结论、来源链接和证据等级
```

### 9.4 Multi-Agent 分析模块

| Agent | 职责 |
|---|---|
| Price Agent | 提取价格、套餐、额度、学生优惠和计费变化 |
| Product Intelligence Agent | 提取新模型、新功能、Agent、上下文和 IDE 集成变化 |
| Sentiment & Risk Agent | 识别正确性、稳定性、隐私、安全和计费争议 |
| Benchmark Agent | 汇总固定任务测试结果，生成代码能力与效率指标 |
| Compare Agent | 将竞品变化映射到七类能力维度，并与 CodeMate Campus 对比 |
| Briefing Agent | 汇总高价值情报，生成机会、威胁和行动建议 |

为控制四周项目工作量，前三个 Agent 为必做，Benchmark Agent 可先支持手工导入测试结果，Compare Agent 与 Briefing Agent 负责统一输出。

### 9.5 竞品情报卡片模块

```json
{
  "competitor": "Cursor",
  "event_type": "product_release",
  "dimension_tags": [
    "agent_context",
    "ide_ecosystem"
  ],
  "event_title": "Cursor 推出新的项目级 Agent 功能",
  "summary": "该功能增强了多文件修改、终端执行和测试验证能力。",
  "change_before": "主要依赖对话式编辑",
  "change_after": "支持规划、跨文件编辑和自动验证",
  "evidence": [
    {
      "source": "Cursor Changelog",
      "evidence_level": "A",
      "url": "https://example.com",
      "quote": "……"
    }
  ],
  "capability_impact": {
    "agent_context": 9,
    "ide_ecosystem": 7,
    "code_intelligence": 6
  },
  "impact_analysis": "该功能提高了复杂项目任务的自动完成能力。",
  "relevance_to_our_product": "medium",
  "threat_level": "high",
  "opportunity": "CodeMate Campus 可强化教学过程解释、代码变更说明和安全执行边界。",
  "recommended_action": "设计课程项目级 Agent 原型，并增加教师规则约束。",
  "confidence_score": 0.91,
  "priority_score": 88,
  "alert_level": "orange"
}
```

### 9.6 能力快照与变化检测

系统为每个竞品维护定期能力快照。

```json
{
  "competitor": "GitHub Copilot",
  "snapshot_date": "2026-04-15",
  "scores": {
    "code_intelligence": 86,
    "agent_context": 78,
    "ide_ecosystem": 92,
    "model_extensibility": 75,
    "performance_cost": 76,
    "security_compliance": 88,
    "education_fit": 58
  },
  "confidence": {
    "code_intelligence": 0.82,
    "agent_context": 0.79
  }
}
```

快照变化用于回答：

- 哪个竞品能力增长最快；
- 哪个维度近期竞争最激烈；
- 哪些差距正在扩大；
- 哪些功能已从差异化能力变为行业标配。

### 9.7 基准测试模块

MVP 阶段建立 12–20 个固定任务，允许人工在不同产品中执行后录入结果。

任务类型：

1. 单函数补全；
2. 编译错误修复；
3. 单元测试修复；
4. 多文件功能修改；
5. 代码重构；
6. 代码解释；
7. 测试生成；
8. 安全缺陷识别。

主要指标：

| 指标 | 说明 |
|---|---|
| compile_success | 是否能够编译或运行 |
| test_pass_rate | 单元测试通过率 |
| task_success | 是否完成完整任务 |
| edit_rounds | 完成任务所需交互轮数 |
| latency | 响应或完成时间 |
| manual_intervention | 人工修改次数 |
| harmful_action | 是否产生危险操作 |
| estimated_cost | 估算请求或订阅成本 |

### 9.8 对标分析模块

调整后的对标矩阵不再只比较“有没有功能”，而是同时比较能力、证据和可信度。

| 对标维度 | CodeMate Campus | Cursor | Copilot | Trae | 通义灵码 | CodeGeeX |
|---|---:|---:|---:|---:|---:|---:|
| 代码智能 | 中 | 强 | 强 | 中强 | 中强 | 中 |
| 项目上下文与 Agent | 中 | 强 | 强 | 强 | 中 | 中 |
| IDE 与工具生态 | 中 | 强 | 强 | 强 | 中强 | 中 |
| 模型与扩展能力 | 中 | 强 | 中强 | 强 | 中 | 强 |
| 性能与成本效率 | 高 | 中 | 中 | 高 | 高 | 高 |
| 安全与隐私 | 中 | 中强 | 强 | 中 | 强 | 中 |
| 教育场景适配 | 强 | 弱 | 弱 | 中 | 中强 | 中 |

表中结果应由证据和规则生成，而不是在代码中固定写死。

### 9.9 可视化看板模块

前端页面包括：

1. 竞品配置页；
2. 数据源与抓取任务页；
3. 最新竞品动态页；
4. 情报卡片详情页；
5. 价格和额度趋势图；
6. 产品能力发布时间线；
7. 七维能力雷达图；
8. 竞品功能与生态矩阵；
9. 代码基准测试柱状图；
10. Agent 能力变化趋势图；
11. 风险舆情与故障趋势图；
12. 证据来源分布图；
13. 简报预览与导出页。

---

## 10. 评分与优先级模型

### 10.1 能力评分

每个能力维度采用 0–100 分。

```text
综合能力分 =
20% × 代码智能
+ 18% × 项目上下文与 Agent
+ 12% × IDE 与生态
+ 10% × 模型与扩展
+ 10% × 性能与成本
+ 12% × 安全与合规
+ 18% × 教育适配
```

评分必须记录：

- 分值；
- 证据数量；
- 证据等级；
- 评分时间；
- 产品版本；
- 评分置信度。

### 10.2 情报优先级

```text
priority_score =
35% × 事件影响程度
+ 25% × 时间紧迫性
+ 20% × 证据置信度
+ 20% × 与 CodeMate Campus 的相关性
```

预警等级：

| 分数 | 等级 |
|---:|---|
| 0–39 | blue |
| 40–59 | yellow |
| 60–79 | orange |
| 80–100 | red |

---

## 11. 技术选型

| 层级 | 技术 |
|---|---|
| 开发语言 | Python |
| 后端框架 | FastAPI |
| 大模型接口 | Claude API，可兼容其他 LLM |
| LangChain | Chains、Agents、Tools、RAG、Memory、Callbacks |
| 结构化输出 | Pydantic / Structured Output |
| 向量数据库 | Chroma 或 FAISS |
| 关系数据库 | MySQL，开发阶段可使用 SQLite |
| 数据采集 | requests、BeautifulSoup、RSS、GitHub API |
| 数据处理 | pandas |
| 定时任务 | APScheduler |
| 前端框架 | Vue 或 React |
| 可视化 | ECharts |
| 部署 | Docker、docker-compose |
| 链路追踪 | LangSmith 或自建日志系统 |
| 测试 | pytest、FastAPI TestClient |

---

## 12. API 接口规划

### 12.1 竞品与维度配置

```text
GET    /api/competitors
POST   /api/competitors
PUT    /api/competitors/{id}
DELETE /api/competitors/{id}

GET    /api/dimensions
PUT    /api/dimensions/weights
```

### 12.2 数据采集

```text
POST /api/crawl/run
GET  /api/crawl/tasks
GET  /api/documents
GET  /api/documents/{id}
POST /api/documents/import
```

### 12.3 RAG 检索

```text
POST /api/rag/query
POST /api/rag/rebuild
GET  /api/rag/evidence/{id}
```

### 12.4 Agent 分析

```text
POST /api/agent/price
POST /api/agent/product
POST /api/agent/sentiment-risk
POST /api/agent/benchmark
POST /api/agent/compare
POST /api/agent/briefing
```

### 12.5 基准测试

```text
GET  /api/benchmarks/tasks
POST /api/benchmarks/runs
GET  /api/benchmarks/results
GET  /api/benchmarks/compare
```

### 12.6 看板

```text
GET /api/dashboard/summary
GET /api/dashboard/price-trend
GET /api/dashboard/release-timeline
GET /api/dashboard/risk-trend
GET /api/dashboard/capability-radar
GET /api/dashboard/capability-trend
GET /api/dashboard/ecosystem-matrix
GET /api/dashboard/benchmark
```

### 12.7 报告

```text
POST /api/reports/generate
GET  /api/reports/{id}
GET  /api/reports/{id}/download
```

---

## 13. 数据表设计

### 13.1 competitors

| 字段 | 类型 | 说明 |
|---|---|---|
| id | int | 主键 |
| name | varchar | 竞品名称 |
| category | varchar | AI IDE / 插件 / Agent |
| official_url | varchar | 官网 |
| pricing_url | varchar | 价格页 |
| changelog_url | varchar | 更新日志 |
| priority | int | 监控优先级 |
| current_version | varchar | 当前版本 |
| dimensions | json | 关注能力维度 |
| created_at | datetime | 创建时间 |

### 13.2 documents

| 字段 | 类型 | 说明 |
|---|---|---|
| id | int | 主键 |
| competitor_id | int | 竞品 ID |
| title | varchar | 标题 |
| content | text | 正文 |
| source_type | varchar | 来源类型 |
| evidence_level | varchar | A / B / C / D |
| url | varchar | 来源 |
| product_version | varchar | 相关版本 |
| publish_time | datetime | 发布时间 |
| crawl_time | datetime | 抓取时间 |
| content_hash | varchar | 去重哈希 |

### 13.3 intelligence_cards

| 字段 | 类型 | 说明 |
|---|---|---|
| id | int | 主键 |
| competitor_id | int | 竞品 ID |
| event_type | varchar | pricing / release / risk |
| dimension_tags | json | 七类能力标签 |
| event_title | varchar | 事件标题 |
| summary | text | 摘要 |
| change_before | text | 变化前 |
| change_after | text | 变化后 |
| impact_analysis | text | 影响分析 |
| opportunity | text | 机会点 |
| threat | text | 威胁点 |
| recommended_action | text | 行动建议 |
| confidence_score | float | 置信度 |
| priority_score | int | 优先级 |
| alert_level | varchar | 预警等级 |
| created_at | datetime | 创建时间 |

### 13.4 capability_snapshots

| 字段 | 类型 | 说明 |
|---|---|---|
| id | int | 主键 |
| competitor_id | int | 竞品 ID |
| snapshot_date | date | 快照日期 |
| product_version | varchar | 产品版本 |
| scores | json | 七维能力分 |
| confidence | json | 各维度置信度 |
| evidence_count | json | 各维度证据数 |
| total_score | float | 综合得分 |

### 13.5 benchmark_tasks

| 字段 | 类型 | 说明 |
|---|---|---|
| id | int | 主键 |
| name | varchar | 任务名称 |
| task_type | varchar | 补全 / 修复 / 重构等 |
| language | varchar | 编程语言 |
| prompt | text | 任务说明 |
| repository_path | varchar | 测试项目路径 |
| validation_command | varchar | 验证命令 |
| difficulty | varchar | 难度 |

### 13.6 benchmark_runs

| 字段 | 类型 | 说明 |
|---|---|---|
| id | int | 主键 |
| competitor_id | int | 竞品 ID |
| task_id | int | 任务 ID |
| product_version | varchar | 产品版本 |
| task_success | bool | 是否完成 |
| test_pass_rate | float | 测试通过率 |
| edit_rounds | int | 交互轮数 |
| latency_ms | int | 耗时 |
| manual_intervention | int | 人工干预次数 |
| estimated_cost | float | 估算成本 |
| notes | text | 备注 |
| run_at | datetime | 执行时间 |

### 13.7 reports

| 字段 | 类型 | 说明 |
|---|---|---|
| id | int | 主键 |
| title | varchar | 报告标题 |
| report_type | varchar | 日报 / 周报 / 专题报告 |
| content | text | 报告内容 |
| period_start | date | 开始时间 |
| period_end | date | 结束时间 |
| created_at | datetime | 创建时间 |

---

## 14. 四周项目阶段规划

### 14.1 第一周：立项、维度设计与系统原型

任务：

- 确定 AI 编程助手选题；
- 完成 CodeMate Campus 产品定位；
- 确定首期竞品；
- 设计三类事件维度；
- 设计七类能力维度和评分规则；
- 设计数据结构；
- 绘制系统原型；
- 配置 Claude、LangChain 和开发环境。

产出：

- `项目规划.md`
- `竞品列表.xlsx`
- `AI编程助手监控维度设计.md`
- `能力评分规则.md`
- `系统原型图`
- `立项说明PPT`

### 14.2 第二周：数据采集、清洗与 RAG

任务：

- 编写官网、Changelog、Pricing 和 GitHub 采集器；
- 整理测试数据集；
- 实现文本清洗、去重和版本标准化；
- 实现事件分类和能力多标签标注；
- 构建 Chroma / FAISS 向量库；
- 实现带元数据过滤的 RAG 查询；
- 验证证据引用。

产出：

- `crawler/`
- `data/raw/`
- `data/cleaned/`
- `vector_store/`
- `rag_query.py`
- `测试数据集.csv`

### 14.3 第三周：Prompt、Agent 与对标分析

任务：

- 设计 Price Prompt；
- 设计 Product Intelligence Prompt；
- 设计 Sentiment & Risk Prompt；
- 设计能力标签 Prompt；
- 使用 Pydantic 约束结构化输出；
- 实现 Multi-Agent 协作；
- 生成竞品情报卡片；
- 建立首版能力快照；
- 准备 12–20 个基准测试任务；
- 实现手工测试结果导入。

产出：

- `prompts/price_prompt.md`
- `prompts/product_prompt.md`
- `prompts/risk_prompt.md`
- `prompts/dimension_tagging_prompt.md`
- `agents/price_agent.py`
- `agents/product_agent.py`
- `agents/risk_agent.py`
- `agents/benchmark_agent.py`
- `agents/compare_agent.py`
- `schemas/intelligence_card.py`
- `benchmarks/tasks/`

### 14.4 第四周：API、前端、测试与部署

任务：

- 使用 FastAPI 封装服务；
- 实现 Swagger 文档；
- 实现竞品配置、情报卡片、能力快照和报告接口；
- 开发前端看板；
- 绘制价格趋势、雷达图、矩阵图和基准测试图；
- 完成单元测试和端到端测试；
- 测试维度识别准确率；
- 测试 Agent 工具调用稳定性；
- 统计 Token 消耗；
- 完成 Docker 部署；
- 准备结项汇报和演示。

产出：

- `backend/`
- `frontend/`
- `API接口文档.md`
- `测试报告.md`
- `部署文档.md`
- `用户使用手册.md`
- `竞争态势简报Demo`
- `结项汇报PPT`
- `系统演示视频`

---

## 15. 项目目录规划

```text
CodeRadar/
├── README.md
├── 项目规划.md
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
│
├── config/
│   ├── competitors.yaml
│   ├── dimensions.yaml
│   └── scoring.yaml
│
├── data/
│   ├── raw/
│   ├── cleaned/
│   ├── samples/
│   ├── snapshots/
│   └── competitors.csv
│
├── crawler/
│   ├── crawl_official.py
│   ├── crawl_changelog.py
│   ├── crawl_pricing.py
│   ├── crawl_github.py
│   ├── crawl_forum.py
│   └── rss_loader.py
│
├── backend/
│   ├── main.py
│   ├── config.py
│   ├── database.py
│   ├── routers/
│   │   ├── competitors.py
│   │   ├── documents.py
│   │   ├── rag.py
│   │   ├── agents.py
│   │   ├── benchmarks.py
│   │   ├── dashboard.py
│   │   └── reports.py
│   └── services/
│       ├── crawl_service.py
│       ├── rag_service.py
│       ├── agent_service.py
│       ├── benchmark_service.py
│       ├── scoring_service.py
│       └── report_service.py
│
├── rag/
│   ├── document_loader.py
│   ├── text_splitter.py
│   ├── embeddings.py
│   ├── vector_store.py
│   ├── retriever.py
│   └── evidence_ranker.py
│
├── agents/
│   ├── price_agent.py
│   ├── product_agent.py
│   ├── risk_agent.py
│   ├── benchmark_agent.py
│   ├── compare_agent.py
│   └── briefing_agent.py
│
├── prompts/
│   ├── price_prompt.md
│   ├── product_prompt.md
│   ├── risk_prompt.md
│   ├── dimension_tagging_prompt.md
│   ├── benchmark_prompt.md
│   ├── compare_prompt.md
│   └── briefing_prompt.md
│
├── schemas/
│   ├── competitor.py
│   ├── document.py
│   ├── intelligence_card.py
│   ├── capability_snapshot.py
│   ├── benchmark.py
│   └── report.py
│
├── benchmarks/
│   ├── tasks/
│   ├── repositories/
│   ├── validators/
│   └── results/
│
├── frontend/
│   ├── src/
│   ├── public/
│   └── package.json
│
├── reports/
│   ├── daily/
│   ├── weekly/
│   └── final/
│
└── docs/
    ├── AI编程助手监控维度设计.md
    ├── 能力评分规则.md
    ├── API接口文档.md
    ├── 测试报告.md
    ├── 部署文档.md
    └── 用户使用手册.md
```

---

## 16. 评估指标

### 16.1 功能完成度

| 指标 | 目标 |
|---|---|
| 竞品配置 | 支持增删改查和维度权重配置 |
| 数据采集 | 至少支持官网、Changelog、Pricing 三类来源 |
| 事件分类 | 支持价格、发布、风险三类事件 |
| 能力标签 | 支持七类能力多标签识别 |
| RAG 查询 | 返回答案、证据、来源和时间 |
| Agent 分析 | 至少实现三个核心分析 Agent |
| 情报卡片 | 输出结构化 JSON |
| 能力对标 | 生成竞品能力快照和雷达图 |
| 基准测试 | 至少完成 12 个固定任务的数据录入与对比 |
| 可视化 | 至少包含四类图表 |
| 简报生成 | 支持 Markdown，并至少支持一种导出格式 |
| API 服务 | Swagger 可访问 |
| 部署 | 支持 Docker 本地运行 |

### 16.2 分析质量

| 指标 | 说明 |
|---|---|
| 事件分类准确率 | 价格、发布、风险分类是否正确 |
| 多标签 F1 | 七类能力标签识别质量 |
| 实体识别准确率 | 竞品、版本、套餐和模型是否正确 |
| 信息抽取完整率 | 时间、变化前后、功能和限制是否完整 |
| 证据引用正确率 | 结论是否能追溯到真实来源 |
| 证据等级合理性 | 官方信息与用户评论是否正确区分 |
| 评分一致性 | 同一规则下不同竞品评分是否稳定 |
| 机会与威胁有效性 | 是否结合 CodeMate Campus 定位 |
| 简报可读性 | 是否适合产品或管理人员阅读 |

### 16.3 基准测试质量

| 指标 | 说明 |
|---|---|
| 可复现性 | 使用相同任务能否重复验证 |
| 版本记录 | 是否记录产品、模型和时间 |
| 验证自动化 | 是否通过编译或单元测试判断结果 |
| 人工干预记录 | 是否记录额外提示和人工修改 |
| 公平性 | 不同产品是否使用等价任务条件 |
| 成本记录 | 是否记录额度、时间或估算成本 |

### 16.4 工程质量

| 指标 | 说明 |
|---|---|
| API 响应时间 | 常规查询处于可接受范围 |
| Agent 调用成功率 | 工具调用和 JSON 解析稳定 |
| 异常恢复 | 数据源或模型失败时可重试 |
| 日志记录 | 保留抓取、分析和错误日志 |
| 测试覆盖 | 核心模块具有单元测试 |
| 可维护性 | 模块职责清晰、配置与代码分离 |

---

## 17. 风险与应对方案

| 风险 | 表现 | 应对方案 |
|---|---|---|
| 产品变化频繁 | 文档、价格和功能快速变化 | 保存时间、版本和页面快照 |
| 官方宣传偏差 | 宣传能力与实际效果不一致 | 区分官方证据与可复现测试 |
| 舆情样本偏差 | 少数用户意见被放大 | 使用来源等级和多源交叉验证 |
| 基准测试不公平 | 不同产品配置和模型不同 | 固定任务、记录模型和交互轮数 |
| GUI 产品难自动测试 | Cursor 等工具无法统一 API 调用 | MVP 使用人工执行和结构化录入 |
| 维度过多 | 四周内难以全部实现 | 三类事件必做，七类能力采用统一多标签器 |
| LLM 输出不稳定 | JSON 错误或字段缺失 | Pydantic、重试和规则校验 |
| 信息版本混淆 | 新旧套餐、模型和功能冲突 | 强制记录版本、时间和来源 |
| Token 成本高 | 长文档和多 Agent 消耗大 | 文本切分、缓存和模型分层 |
| 数据源反爬 | 页面无法稳定采集 | RSS、API、手工导入和样例数据 |
| 隐私与版权风险 | 保存完整网页或用户代码 | 仅保存必要文本并记录来源 |
| 前端时间不足 | 页面和图表完成度低 | 优先完成情报卡片、雷达图和趋势图 |

---

## 18. 最终成果清单

| 成果 | 说明 |
|---|---|
| 项目规划文档 | 明确 AI 编程助手选题、维度和开发计划 |
| 竞品数据集 | 包含三类事件和七类能力标签 |
| RAG 知识库 | 支持按竞品、时间、事件和能力检索 |
| Multi-Agent 系统 | 支持价格、产品、风险、对标和简报 |
| 能力快照 | 保存竞品七维评分及证据 |
| 基准测试集 | 包含固定编程任务和验证方法 |
| FastAPI 后端 | 提供系统核心接口 |
| 前端看板 | 展示动态、趋势、雷达图、矩阵和基准结果 |
| 竞争态势简报 | 输出机会点、威胁点和行动建议 |
| 测试报告 | 包含分类、Agent、RAG 和基准测试结果 |
| 部署文档 | 支持 Docker 本地或云端部署 |
| 结项汇报 PPT | 用于最终展示 |
| 系统演示视频 | 展示完整业务流程 |

---

## 19. 项目亮点总结

1. **选题边界清晰**：聚焦 AI 编程助手、AI IDE 和开发 Agent。
2. **满足原始考核要求**：保留价格变化、产品发布、负面舆情三类核心监控事件。
3. **专业维度更贴合赛道**：增加代码智能、项目上下文、Agent、IDE 生态、模型扩展、安全隐私和教育适配。
4. **事件与能力解耦**：一条动态既有事件类型，也有能力标签，便于检索和统计。
5. **强调变化而非静态列表**：通过版本、能力快照和时间线分析竞品演进。
6. **结论可追溯**：每个评分和判断均绑定证据来源、时间和可信度。
7. **对标方式更客观**：结合文档证据、用户反馈和固定编程任务。
8. **突出本产品差异化**：将教育场景适配作为核心维度，而不是简单模仿通用 AI IDE。
9. **技术链路完整**：覆盖采集、RAG、Multi-Agent、结构化输出、FastAPI、可视化和部署。
10. **四周范围可控**：前三类 Agent 必做，基准测试可采用人工执行和结构化录入。

最终，CodeRadar 不只是抓取 AI 编程产品新闻，而是建立一个能够持续回答以下问题的竞品智能系统：

> 某项竞品变化具体增强了哪类开发能力，对 CodeMate Campus 构成何种机会或威胁，我们应当采取什么行动？
