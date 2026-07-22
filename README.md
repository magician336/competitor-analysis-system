# CodeRadar

CodeRadar 提供 AI 编程助手竞品的多源数据采集、正文清洗、规则标注、版本化结构输出和 Mini-RAG 证据检索服务。Mini-RAG 是面向竞品动态的轻量检索增强生成（Retrieval-Augmented Generation，RAG）模块。系统使用 Okapi BM25 全文排序与 Dense Retrieval（稠密向量检索）召回候选，通过 RRF（Reciprocal Rank Fusion，倒数排名融合）、交叉编码器重排序、时间版本排序和证据等级排序返回可验证证据。

## 当前目录

```text
CodeRadar/
├── config/             # 竞品、来源及 D1—D7 标签规则
├── crawler/            # HTTP 客户端和各类采集器
├── processing/         # 清洗、规范化、标注、去重与版本管理
├── mini_rag/           # 切分、向量化、索引、检索、排序、证据与评测
├── backend/            # FastAPI 服务与 RAG 路由
├── schemas/            # RawRecord 和 StructuredDocument 数据模型
├── scripts/            # 数据、索引与评测命令行入口
├── tests/              # 离线测试与固定测试数据
└── data/
    ├── raw/            # 原始响应及请求元数据
    ├── cleaned/        # documents.jsonl
    ├── samples/        # 检索评测集
    ├── runtime/        # 查询轨迹和评测运行结果
    └── snapshots/      # ETag、Last-Modified 等增量状态
```

## 环境安装

项目使用 Python 3.11.9，虚拟环境固定放在 `CodeRadar\.venv`。在 PowerShell 中运行：

```powershell
Set-Location D:\26Spring\project\CodeRadar

& 'D:\Python 3.11.9\python.exe' -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r ..\docs\requirement.txt
& .\.venv\Scripts\python.exe -m pip check
```

`docs\requirement.txt` 是唯一依赖清单，`CodeRadar\requirements.txt` 只引用该文件。

本地 Sentence Transformers 模型权重在第一次启用对应 Provider 时下载到配置的缓存目录。默认 `hash` Provider 提供确定性离线向量，用于测试、接口联调和无模型网络环境；正式检索评测使用配置的中英双语 Embedding 模型和 Cross-Encoder Reranker（交叉编码器重排序器）。

Embedding 模型或向量维度变化后必须执行全量索引构建。不同模型生成的向量不写入同一个物理索引。

## GitHub Token

GitHub Token 用于提高 GitHub 应用程序编程接口（Application Programming Interface，API）的请求限额。只采集公开仓库时，Token 仅需公开仓库只读权限，不需要写权限。

项目目录中已创建空白 `.env`。如果文件不存在，则从模板创建，然后编辑本地文件：

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

```dotenv
GITHUB_TOKEN=你的本地令牌
```

`.env` 已加入 `.gitignore`。程序不会把 Token 写入原始数据、日志或测试数据。未配置 Token 时，官网采集和 GitHub 匿名试爬仍可运行，`doctor` 会显示 GitHub 请求限额提示。

## 配置检查

```powershell
.\.venv\Scripts\python.exe -m scripts.data_pipeline doctor
```

该命令检查 Python 版本、依赖文件、五个竞品配置、D1—D7 标签配置、数据目录和 GitHub Token 状态，不发送网络请求。

每个竞品均显式配置 11 类来源。启用项必须提供 HTTPS 地址或 GitHub 仓库；禁用项保留原因说明，`doctor` 会检查类型、证据等级和目标地址。表中 GitHub 任务会分别生成 GitHub Release 和 GitHub Issue 结构化来源。

| 来源类型 | Cursor | GitHub Copilot | Trae | 通义灵码 | CodeGeeX |
| --- | :---: | :---: | :---: | :---: | :---: |
| 官网与博客 `official` | ✓ | ✓ | ✓ | ✓ | ✓ |
| 更新日志 `changelog` | ✓ | ✓ | ✓ | ✓ | — |
| 定价页面 `pricing` | ✓ | ✓ | ✓ | ✓ | — |
| 产品文档 `product_docs` | ✓ | ✓ | ✓ | ✓ | ✓ |
| 状态页 `status_page` | ✓ | ✓ | — | — | — |
| GitHub `github` | — | ✓ | ✓ | — | ✓ |
| 插件市场 `plugin_marketplace` | — | ✓ | ✓ | ✓ | ✓ |
| 社区平台 `community` | ✓ | ✓ | — | — | — |
| 视频与测评 `review` | — | — | — | — | — |
| 安全与隐私 `security_privacy` | ✓ | ✓ | ✓ | ✓ | — |
| 基准测试 `benchmark` | ✓ | ✓ | ✓ | — | ✓ |

第三方视频、测评和社区地址需要先建立人工确认的 URL 允许列表，因此未验证的入口保持禁用。将可靠地址写入 `config/competitors.yaml` 的 `urls`，再把 `enabled` 改为 `true`，无需新增采集代码。

## 开始爬取

先查看 Cursor 和 GitHub Copilot 将执行的来源，不发送请求：

```powershell
.\.venv\Scripts\python.exe -m scripts.data_pipeline crawl `
  --competitors cursor,github_copilot `
  --since-days 90 `
  --dry-run
```

只查看新增来源任务，不发送请求：

```powershell
.\.venv\Scripts\python.exe -m scripts.data_pipeline crawl `
  --competitors all `
  --sources product_docs,status_page,plugin_marketplace,community,review,security_privacy,benchmark `
  --since-days 365 `
  --dry-run
```

现有 `data/raw` 已包含官网、更新日志、定价和 GitHub 数据时，只采集新增来源并重建结构化结果：

```powershell
.\.venv\Scripts\python.exe -m scripts.data_pipeline crawl `
  --competitors all `
  --sources product_docs,status_page,plugin_marketplace,community,review,security_privacy,benchmark `
  --since-days 365 `
  --force `
  --log-level INFO

.\.venv\Scripts\python.exe -m scripts.data_pipeline process --rebuild
.\.venv\Scripts\python.exe -m scripts.audit_documents
```

`--force` 使本轮对全部启用的新增 URL 发出完整请求。`process --rebuild` 会同时读取已有原始响应和新增原始响应，生成一个一致的 `documents.jsonl`。

执行试爬：

```powershell
.\.venv\Scripts\python.exe -m scripts.data_pipeline crawl `
  --competitors cursor,github_copilot `
  --since-days 90 `
  --max-issues 10 `
  --max-comments 2
```

将已保存的原始响应清洗并写入结构化文档：

```powershell
.\.venv\Scripts\python.exe -m scripts.data_pipeline process
```

清洗规则或标签规则调整后，从全部现有原始响应重建派生数据：

```powershell
.\.venv\Scripts\python.exe -m scripts.data_pipeline process --rebuild
```

`--rebuild` 会原子替换 `data/cleaned/documents.jsonl`，不会把规则修正记录为竞品内容的新历史版本。该命令只读取本地 `data/raw`，不发送网络请求。

采集并处理全部五个竞品：

```powershell
.\.venv\Scripts\python.exe -m scripts.data_pipeline all `
  --competitors all `
  --since-days 90
```

常用范围控制参数：

- `--sources`：选择 `official`、`changelog`、`pricing`、`product_docs`、`status_page`、`github`、`github_release`、`github_issue`、`plugin_marketplace`、`community`、`review`、`security_privacy` 或 `benchmark`；多个类型使用逗号分隔。
- `--max-issues 100`：设置每个仓库最多采集的 Issue 数量。
- `--max-comments 20`：设置每条 Issue 最多采集的评论数量。
- `--dry-run`：只校验并输出任务清单。
- `--force`：忽略 ETag 和 Last-Modified 条件缓存，重新请求来源。
- `--rebuild`：`process` 或 `all` 从全部原始响应重建清洗结果。
- `--log-level DEBUG|INFO|WARNING|ERROR`：设置日志级别。

采集器遵守 `robots.txt`，按域名限速，并使用 15 秒超时和最多 3 次指数退避。HTML Changelog 列表会提取带日期的同源条目链接，按照 `--since-days` 过滤，跟随下一页并抓取详情；每个任务默认最多访问 20 个列表页和 200 个详情条目。对于日期位于子页面正文的目录型 Changelog，配置通过层叠样式表选择器（Cascading Style Sheets Selector，CSS Selector）、同源约束、路径前缀和标题模式限定目录链接。采集器校验重定向后的最终地址，读取子页面正文中的显式日期标题或表格日期；处理器按日期段拆分文档，并只保留 `--since-days` 窗口内的条目。目录返回 HTTP 304 时会无条件刷新一次目录正文，以继续检查独立更新的子页面。列表页、无可靠日期边界和超出日期范围的候选响应继续保留用于审计。无法从静态超文本标记语言（HyperText Markup Language，HTML）提取有效入口时，记录 `needs_browser` 和解析提示。单个来源失败会记录错误并继续执行其他来源。

## 数据输出

每次采集创建唯一的 `crawl_run_id`，原始响应与元数据保存为：

```text
data/raw/<competitor>/<source_type>/<crawl_run_id>/
├── <raw_record_id>.html|json|xml
└── <raw_record_id>.meta.json
```

清洗结果保存为：

```text
data/cleaned/documents.jsonl
```

结构化处理会规范化统一资源定位符（Uniform Resource Locator，URL）、Unicode 和空白，计算 SHA-256 内容哈希，并按照“竞品、来源类型、规范 URL、内容哈希”去重。`document_id` 标识稳定来源实体，`version_id` 标识内容版本。内容变化会关闭当前版本的有效区间并保留历史版本。

来源类型写入 E1—E3 事件标签，正文按 `config/dimensions.yaml` 的关键词写入 D1—D7 多标签结果，同时保存置信度、命中原因和人工复核标记。

## Mini-RAG 索引

启动 Elasticsearch 单节点：

```powershell
docker compose up -d elasticsearch
docker compose ps
```

校验统一文档并查看数据分布：

```powershell
.\.venv\Scripts\python.exe -m scripts.audit_documents
```

全量构建版本化物理索引，并将读取别名切换到构建成功的索引：

```powershell
.\.venv\Scripts\python.exe -m scripts.build_index
```

增量同步当前索引：

```powershell
.\.venv\Scripts\python.exe -m scripts.build_index --incremental
```

索引以 Chunk 为检索单元。每个 Chunk 保存 `document_id`、`version_id`、稳定 `chunk_id`、原文字符区间、标题路径、来源、时间、产品版本、事件标签、能力标签和证据等级。`chunk_id` 包含文档版本语义，同一来源实体的多个内容版本具有独立索引记录。

## 查询与证据服务

命令行查询：

```powershell
.\.venv\Scripts\python.exe .\rag_query.py `
  "Cursor 最近有哪些 Agent 能力更新" `
  --competitor Cursor `
  --top-k 8
```

启动应用程序编程接口（Application Programming Interface，API）：

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app `
  --host 127.0.0.1 `
  --port 8000
```

主要接口如下：

- `POST /api/rag/query`：混合检索并返回证据、冲突和检索轨迹。
- `GET /api/rag/evidence/{chunk_id}`：读取可验证的原始 Chunk。
- `GET /api/rag/trace/{query_id}`：读取各阶段候选数量、排序配置和延迟。
- `GET /api/rag/index/status`：读取后端健康状态、索引别名和 Chunk 数量。
- `POST /api/rag/rebuild`：执行版本化全量索引构建和别名切换。
- `POST /api/rag/index/incremental`：执行幂等增量索引。
- `POST /api/rag/citations/validate`：验证引用范围、URL、引文和元数据。
- `POST /api/rag/evaluate`：运行固定检索评测集。

`GET /health` 是进程存活检查，`GET /ready` 同时检查 Elasticsearch、读取别名和索引可用性。Docker Compose 的 API 与 Elasticsearch 端口仅绑定到本机回环地址。

Compose 默认构建 `hash + lexical` CPU 基线镜像。容器内启用 Sentence Transformers Embedding 或 Cross-Encoder 时，在 `.env` 中设置 `MINIRAG_INSTALL_ML=true`，再执行 `docker compose up -d --build`；Dockerfile 使用 PyTorch CPU 软件源安装模型运行依赖。

查询解析器识别竞品、事件、能力维度、产品版本和相对时间。接口中的显式过滤参数具有最高优先级。返回证据保留原文、来源 URL、字符区间、版本、发布时间、证据等级和各阶段分数。

## 检索评测

评测数据使用 `data/samples/测试数据集.csv` 中的固定字段。以下命令依据当前结构化文档和切分配置生成 40 条候选案例、人工复核表及带哈希的评测 Manifest：

```powershell
.\.venv\Scripts\python.exe -m scripts.prepare_evaluation_set
.\.venv\Scripts\python.exe -m scripts.validate_evaluation_set
```

候选标签的 `human_review_status` 为 `pending`。人工在 `data/samples/评测集人工复核.csv` 中填写 `human_grade`、`question_valid`、`quote_valid`、`reviewer` 和 `reviewed_at`；补充相关 Chunk 时，使用 `additional_relevance_grades` 填写 `{"chunk_id": 1|2|3}`。全部案例复核完成后执行：

```powershell
.\.venv\Scripts\python.exe -m scripts.finalize_evaluation_review
.\.venv\Scripts\python.exe -m scripts.validate_evaluation_set
```

结构校验会拒绝空相关集合、重复 `case_id`、未知过滤字段、非法相关性等级、索引中不存在的 Chunk 和无法定位的预期引文。标准评测命令为：

```powershell
.\.venv\Scripts\python.exe -m scripts.evaluate_rag `
  --output data/runtime/mini_rag_evaluation.json
```

评测模块计算 Recall@K（前 K 条召回率）、MRR（Mean Reciprocal Rank，平均倒数排名）、nDCG（Normalized Discounted Cumulative Gain，归一化折损累计增益）、过滤准确率、引用正确率、历史版本误召回率、平均延迟和 P95 延迟，并提供 A—F 六组消融实验配置。

六组消融实验中的 D—F 使用真实 Cross-Encoder。`config/mini_rag.formal.yaml` 配置 BGE-M3 Embedding 与多语言 MiniLM Cross-Encoder；索引端和查询端使用相同的 BGE-M3 向量空间。首次运行会下载模型权重：

```powershell
$env:HF_HOME = 'D:\26Spring\project\CodeRadar\.cache\huggingface'

docker compose stop api
.\.venv\Scripts\python.exe -m scripts.build_index `
  --config config\mini_rag.formal.yaml

.\.venv\Scripts\python.exe -m scripts.evaluate_rag `
  --config config\mini_rag.formal.yaml `
  --ablations `
  --quiet `
  --output data/runtime/mini_rag_ablations.json
```

消融评测在 A—F 各组计时前执行一次预热，并在结果中记录 `warmup_queries_per_group=1` 和稳态延迟口径。BGE-M3 索引启用期间，查询服务也必须使用 `config/mini_rag.formal.yaml`。默认 Docker API 使用 Hash Embedding；恢复该服务前执行默认全量构建，使读取别名重新指向 Hash Embedding 索引。

评测 JSON 同时记录数据集哈希、配置哈希、索引版本、模型、候选标签来源和人工复核状态。对同一环境执行三次 A—F 实验后，可生成均值、样本标准差和报告图表：

```powershell
.\.venv\Scripts\python.exe -m scripts.summarize_rag_benchmark `
  data/runtime/mini_rag_ablations.run1.json `
  data/runtime/mini_rag_ablations.run2.json `
  data/runtime/mini_rag_ablations.run3.json
```

该命令生成 `data/runtime/mini_rag_ablations_summary.json`、检索质量图和 P95 延迟图。候选评测集在人工复核完成前只用于工程诊断，人工确认后使用相同命令重新评测并更新报告。

## 测试

默认测试排除显式标记的网络 smoke test，全程离线：

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not network" -q
.\.venv\Scripts\python.exe -m pytest -m "not network" `
  --cov=crawler --cov=processing --cov=schemas --cov=mini_rag --cov=backend
```

需要自行检查公开网络连通性时，可以显式启用单页 smoke test。该测试会访问 Cursor 官网，默认不会运行：

```powershell
$env:CODERADAR_RUN_NETWORK_TESTS = '1'
.\.venv\Scripts\python.exe -m pytest tests\test_network_smoke.py -m network -q
Remove-Item Env:CODERADAR_RUN_NETWORK_TESTS
```

命令退出码定义如下：

- `0`：全部来源成功，或处理命令成功。
- `2`：至少一个来源失败，其余来源继续完成。
- `1`：配置错误或运行级错误。
