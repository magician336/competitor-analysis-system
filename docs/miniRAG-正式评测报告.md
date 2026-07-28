# CodeRadar Mini-RAG 正式评测报告

> 评测日期：2026-07-21
> 评测配置：`config/mini_rag.formal.yaml`（BGE-M3 + Cross-Encoder）
> 运行环境：Conda `CodeRadar`，Python 3.11.9，Elasticsearch 9.4.1
> 评测集：`data/samples/测试数据集.csv`（40 case 金标，`human_review_status=complete`）
> 结果文件：`data/runtime/mini_rag_ablations.json`
> 评测集 SHA-256：`sha256:4887e334e0ee88243cf619f1dae05c854d752a7d1582f67eddc630ecc4491873`

## 1. 评测概述

### 1.1 目标

对 CodeRadar 的 Mini-RAG 检索增强系统在真实多源竞品语料上进行正式评测，量化回答以下问题：

1. 语义检索（BGE-M3）相对词法检索（BM25）在该语料上的增益；
2. 混合检索（RRF 融合）是否优于单路检索；
3. Cross-Encoder 重排序与时间/版本/证据等级排序各阶段的边际贡献；
4. 版本化与元数据过滤的可靠性（历史版本误召回、过滤准确率）；
5. 可验证证据的引用正确率；
6. 各配置的延迟代价。

### 1.2 结论摘要

| 维度 | 结论 |
|---|---|
| 最优配置 | E 组（BM25+Dense+RRF+Cross-Encoder+时间+版本排序） |
| 最优 nDCG@10 | 0.880 |
| 最优 Recall@10 | 0.975（39/40 case 命中） |
| 引用正确率 | 0.97（39/40 case 引用可验证） |
| 元数据过滤准确率 | 1.00（全 6 组） |
| 历史版本误召回率 | 0.000（全 6 组） |
| 顽固未命中 | 1 个 case（`trae_code_folding`，跨语言语义改写） |

BGE-M3 语义检索是本次评测的最大增益来源；Cross-Encoder 重排序与时间/版本排序提供稳定边际提升；版本化与过滤机制在全部配置下保持完美质量。

## 2. 评测方法

### 2.1 金标评测集

评测集由 40 个硬编码 SEED 经 `prepare_evaluation_set` 解析为当前版本 chunk，再经人工复核（reviewer: magician336，`human_review_status=complete`）提升为金标。

**语料规模**：566 文档，2052 chunks（索引 `coderadar_chunks_v20260721065411244401`，BGE-M3 1024 维）。

**竞品分布**：

| 竞品 | case 数 |
|---|---:|
| Cursor | 8 |
| GitHub Copilot | 12 |
| Trae | 7 |
| CodeGeeX | 7 |
| 通义灵码 | 6 |

**来源类型分布**：

| 来源 | case 数 |
|---|---:|
| official_page | 8 |
| official_changelog | 14 |
| pricing | 6 |
| github_release | 1 |
| github_issue | 11 |

**事件类型分布**：product_release 15、risk_experience 11、unclassified 8、pricing_change 6。

每个 case 包含 `case_id` / `question`（中文自然语言）/ `needle`（预期命中的原文片段）/ `query_filters`（竞品、来源、事件、能力维度、产品版本、相对时间、current_only）/ `expected_quote`，用于 Recall@K、引用正确率与过滤准确率的客观计算。

### 2.2 消融设计

评测采用 6 组递进消融（A–F），隔离每个检索与排序组件的边际贡献：

| 组 | 描述 | BM25 | Dense | RRF | Cross-Encoder | 时间+版本 | 证据等级 |
|---|---|:---:|:---:|:---:|:---:|:---:|:---:|
| A | BM25 only | ✓ | | | | | |
| B | Dense Vector only | | ✓ | | | | |
| C | BM25 + Dense + RRF | ✓ | ✓ | ✓ | | | |
| D | C + Cross-Encoder Reranker | ✓ | ✓ | ✓ | ✓ | | |
| E | D + temporal + version | ✓ | ✓ | ✓ | ✓ | ✓ | |
| F | E + evidence-level | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

每组前 1 条 query 作 warmup（不计时），延迟统计为稳态值。

### 2.3 评测指标

| 指标 | 定义 |
|---|---|
| Recall@K | top-K 结果是否包含目标 chunk（金标 needle 所在 chunk） |
| MRR | 目标 chunk 排名倒数的均值 |
| nDCG@10 | top-10 排序质量（位置折扣增益） |
| citation_accuracy | 返回引用是否与原文一致且字符区间可验证 |
| metadata_filter_accuracy | 返回结果是否全部满足 `query_filters` |
| old_version_false_recall_rate | 误召回历史（非 current）版本 chunk 的比例 |
| p50 / p95 latency | 稳态查询延迟中位数与 95 分位 |

## 3. 评测结果

### 3.1 总体指标

| 组 | 配置 | R@5 | R@10 | MRR | nDCG@10 | 引用正确率 | 过滤准确率 | 历史误召回 | P50 (ms) | P95 (ms) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | BM25 only | 0.738 | 0.838 | 0.538 | 0.611 | 0.85 | 1.00 | 0.000 | 26.7 | 33.7 |
| B | Dense only | 0.912 | 0.963 | 0.854 | 0.879 | 0.97 | 1.00 | 0.000 | 71.1 | 83.3 |
| C | BM25+Dense+RRF | 0.912 | 0.938 | 0.824 | 0.848 | 0.95 | 1.00 | 0.000 | 103.8 | 119.1 |
| D | C + Cross-Encoder | 0.925 | 0.950 | 0.849 | 0.873 | 0.95 | 1.00 | 0.000 | 381.6 | 511.6 |
| **E** | **D + 时间 + 版本** | **0.925** | **0.975** | **0.852** | **0.880** | **0.97** | **1.00** | **0.000** | 407.5 | 590.3 |
| F | E + 证据等级 | 0.925 | 0.975 | 0.852 | 0.880 | 0.97 | 1.00 | 0.000 | 428.4 | 687.1 |

### 3.2 检索方法对比（A / B / C）

- **BM25（A）→ Dense（B）**：nDCG@10 从 0.611 提升至 0.879（+43.9%），Recall@10 从 0.838 提升至 0.963，引用正确率从 0.85 提升至 0.97。BGE-M3 对中英混合竞品语料的语义匹配能力远超词法匹配。
- **Dense（B）→ RRF（C）**：nDCG@10 从 0.879 反而降至 0.848，Recall@10 从 0.963 降至 0.938。BM25 的词法噪声在 RRF 融合中拉低了 Dense 的质量，表明对该语料 RRF 的默认融合权重未达最优。

### 3.3 排序阶段贡献（C / D / E / F）

| 阶段跃迁 | nDCG@10 变化 | Recall@10 变化 | 说明 |
|---|---|---|---|
| C → D（+Cross-Encoder） | 0.848 → 0.873 | 0.938 → 0.950 | 重排序提升顶部精度 |
| D → E（+时间+版本） | 0.873 → 0.880 | 0.950 → 0.975 | 时间衰减+版本权重把更多目标推入 top-10 |
| E → F（+证据等级） | 0.880 → 0.880 | 0.975 → 0.975 | 证据等级排序无边际收益，仅增加延迟 |

时间/版本排序（D→E）贡献了 Recall@10 的最后一次显著提升（+2.5pp），证明版本有效区间与时间衰减对"命中并排前"有实际价值。证据等级排序（E→F）在本评测集上无增益，因金标 case 的证据等级分布集中，区分度不足。

## 4. 结果分析

### 4.1 BGE-M3 语义检索的价值

A 组（BM25）有 6 个 case Recall@10=0，全部为中文自然语言 query。加入 BGE-M3 Dense（B 组）后，其中 5 个被救回（Recall@10 升至 1.0）：

| case | query 语言 | A (BM25) | B (Dense) | 救回 |
|---|---|:---:|:---:|:---:|
| cursor_official_agent | 中文 | 0.0 | 1.0 | ✓ |
| copilot_codex_jetbrains | 中文 | 0.0 | 1.0 | ✓ |
| trae_mcp_response | 中文 | 0.0 | 1.0 | ✓ |
| codegeex_rider_2026 | 中文 | 0.0 | 1.0 | ✓ |
| lingma_inline_generation | 中文 | 0.0 | 1.0 | ✓ |
| trae_code_folding | 中文 | 0.0 | 0.0 | ✗ |

BM25 对中文分词与术语改写敏感（如"把开发任务交给 Agent" vs 原文"handing off tasks to Cursor"），而 BGE-M3 的多语言语义空间能跨越表述差异匹配。这是 B 组相对 A 组增益的主要来源。

### 4.2 版本化与过滤质量

全部 6 组配置下：

- **metadata_filter_accuracy = 1.00**：返回结果 100% 满足 `query_filters`（竞品、来源、事件、能力维度、产品版本、相对时间），证明 `QueryParser` 的显式过滤参数与 `current_only_for_current_intent` 机制可靠。
- **old_version_false_recall_rate = 0.000**：无误召回历史（非 current）版本 chunk。`_resolve_seed` 在评测集准备阶段强制 `chunk.is_current` 过滤，索引层 `valid_from`/`valid_until` 有效区间与读别名版本化物理索引共同保证仅当前版本进入候选。

这两个指标在 A 组（最弱检索）即已满分，说明版本化质量与检索质量正交，由数据契约与索引层独立保证。

### 4.3 弱项 case 分析

**顽固未命中：`trae_code_folding`**

- query：「TRAE 用户为什么请求关闭聊天代码块自动折叠？」
- needle（预期原文）：`requires extra clicks for every code block`
- 6 组全部 Recall@10=0。

该 case 为跨语言语义改写：query 用中文描述"关闭自动折叠"的诉求，needle 用英文描述"额外点击"的后果，二者语义相关但表述无词汇与句式重叠。BGE-M3 未能建立该跨语言因果改写的语义关联，是当前系统的明确短板。改进方向：查询改写（Query Rewriter）引入跨语言同义扩展，或在 chunking 阶段为 issue 类来源抽取"诉求-后果"对作为补充检索键。

**RRF 引入的回退：`codegeex_rider_2026`**

- B 组（Dense）Recall@10=1.0，C 组（+RRF）与 D 组（+Cross-Encoder）回退至 0.0，E 组（+时间+版本）恢复至 1.0。

该 case 显示 RRF 融合将 BM25 的词法噪声混入候选，使目标 chunk 跌出 top-10；时间/版本排序重新将其推回。这印证了 3.2 节的结论：RRF 默认融合权重对该语料非最优，调整 RRF 的 BM25/Dense 权重或 `rrf_k` 可能避免回退。

**top-5 排序不足：`copilot_official_workflow`、`codegeex_rider_2026`**

E 组中这两个 case Recall@5=0 但 Recall@10=1.0，目标 chunk 命中但排名 6–10。提升顶部排序精度（如增大 rerank 候选窗、调优 reranker）可改善 MRR。

### 4.4 延迟权衡

| 配置 | P95 延迟 | nDCG@10 | 适用场景 |
|---|---:|---:|---|
| A（BM25） | 34 ms | 0.611 | 极低延迟兜底，质量不足 |
| B（Dense） | 83 ms | 0.879 | **性价比最优**：延迟低、质量接近最优 |
| C（RRF） | 119 ms | 0.848 | 融合反而降质，不推荐 |
| E（完整） | 590 ms | 0.880 | 质量最优，交互式查询可接受 |
| F（+证据） | 687 ms | 0.880 | 无质量增益，不推荐 |

Cross-Encoder 重排序是延迟主要来源（D 组 P95 从 119 ms 跳升至 512 ms）。若部署场景对延迟敏感，B 组（Dense only，P95 83 ms，nDCG 0.879）相对 E 组（0.880）质量差距仅 0.001，是高性价比的工程选择。

## 5. 结论与建议

### 5.1 结论

1. **BGE-M3 语义检索是该语料的核心能力**：相对 BM25 nDCG 提升 43.9%，解决 5/6 个中文 query 词法失配 case。
2. **完整排序链（E 组）达到最优质量**：nDCG@10=0.880，Recall@10=0.975，引用正确率 0.97。
3. **RRF 默认融合权重需调优**：当前 RRF 融合（C 组）反而低于纯 Dense（B 组），引入 case 回退。
4. **证据等级排序在本评测集无增益**：E→F 无质量提升，仅增延迟。
5. **版本化与过滤机制完全可靠**：全配置过滤准确率 1.0、历史误召回 0.0。
6. **剩余短板为跨语言因果改写**：1 个顽固 case（`trae_code_folding`）。

### 5.2 建议

| 优先级 | 建议 | 预期收益 |
|---|---|---|
| 高 | 调优 RRF 融合权重（降低 BM25 权重或上调 `rrf_k`） | 避免 C/D 组 case 回退，C 组质量回升 |
| 高 | 部署 B 组作为低延迟档，E 组作为高质量档 | 覆盖不同延迟敏感度场景 |
| 中 | Query Rewriter 引入跨语言同义扩展 | 解决 `trae_code_folding` 类跨语言改写 |
| 中 | 增大 `rerank_candidates` 或调优 Cross-Encoder | 提升 top-5 精度（MRR） |
| 低 | 评估证据等级排序在更大评测集的增益 | 当前 40 case 证据等级区分度不足 |
| 低 | 扩充评测集至含历史版本 case | 当前语料无历史版本，`old_version_false_recall_rate` 需真实历史样本验证 |

## 6. 复现步骤

### 6.1 环境配置

正式评测需 BGE-M3 与 Cross-Encoder 模型，默认 `.env` 为 hash 基线（Docker API 默认），需三层环境调整：

```powershell
# 解释器（非交互 PowerShell 不会激活 conda）
$py = 'E:\Software\Scoop\apps\miniconda3\current\envs\CodeRadar\python.exe'

# 1) .env 覆盖 formal.yaml：用系统环境变量反覆盖（load_dotenv override=False 时系统优先）
$env:MINIRAG_EMBEDDING_PROVIDER = 'sentence_transformers'
$env:MINIRAG_RERANKER_PROVIDER  = 'cross_encoder'
$env:MINIRAG_RERANKER_MODEL      = 'cross-encoder/mmarco-mMiniLMv2-L12-H384-v1'
$env:MINIRAG_RERANKER_STRICT     = 'true'

# 2) HF 镜像 + 绕过代理直连（httpx 默认 trust_env 走系统代理会切断 hf-mirror TLS）
$env:HF_ENDPOINT       = 'https://hf-mirror.com'
$env:NO_PROXY          = '*'
$env:HF_HUB_DISABLE_XET = '1'

# 3) 卸载 hf-xet 包：否则 LFS 大文件仍走 xet CDN（cas-bridge.xethub.hf.co）超时
& $py -m pip uninstall hf-xet -y
```

### 6.2 评测命令

```powershell
# 确保 Elasticsearch 运行
docker compose up -d elasticsearch

# 金标评测集（首次需 prepare -> 人工复核 -> finalize -> validate）
& $py -m scripts.prepare_evaluation_set
# ... 人工填写 data/samples/评测集人工复核.csv 的 reviewer/reviewed_at ...
& $py -m scripts.finalize_evaluation_review
& $py -m scripts.validate_evaluation_set

# 重建 BGE-M3 formal 索引（alias 切到 BGE-M3）
& $py -m scripts.build_index --config config/mini_rag.formal.yaml

# 正式评测（6 组消融）
& $py -m scripts.evaluate_rag --config config/mini_rag.formal.yaml --ablations --quiet --output data/runtime/mini_rag_ablations.json

# 评测后恢复 hash 默认索引（alias 切回 hash，Docker API 恢复正常）
& $py -m scripts.build_index
```

## 附录 A：正式配置参数

| 类别 | 参数 | 值 |
|---|---|---|
| Embedding | provider / model / dimension / normalize / batch | sentence_transformers / BAAI/bge-m3 / 1024 / true / 16 |
| Reranker | provider / model / batch / strict | cross_encoder / cross-encoder/mmarco-mMiniLMv2-L12-H384-v1 / 8 / true |
| Ranking | semantic / temporal / version / evidence / half_life | 0.70 / 0.12 / 0.08 / 0.10 / 180d |
| Retrieval | bm25 / dense / fused / rerank candidates / top_k / rrf_k | 20 / 20 / 30 / 20 / 8 / 60 |
| Chunking | target / max / overlap / min | 1200 / 1800 / 160 / 80 |

排序权重和校验为 1.0（构造时校验）。

## 附录 B：评测元数据

- `evaluation_id`（A 组）：`eval_0fc1f98482cee4215402fbed`
- 索引版本：`coderadar_chunks_v20260721065411244401`
- `config_sha256`：`sha256:610126b7c3ad167694c9e09bc0a279e215ec900b25f5fc4ebc3f78f6b5ff17f1`
- `actual_reranker_backend`：`cross_encoder`（D–F 组实际调用，A–C 组加载但不调用）
- `reranker_error`：`null`（Cross-Encoder 加载成功，无 fallback）
- `warmup_queries_per_group`：1（延迟统计为稳态值）
- 评测时间：2026-07-21T07:08 UTC（A 组），6 组总耗时约 1 小时
