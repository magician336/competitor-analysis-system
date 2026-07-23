"""Build a traceable, AI-assisted candidate retrieval evaluation set."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from mini_rag.chunking import ChunkingDispatcher
from mini_rag.config import load_settings
from mini_rag.evaluation import validate_evaluation_cases
from mini_rag.ingestion import DocumentLoader
from mini_rag.models import Chunk, EvaluationCase


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REVIEW_PATH = PROJECT_ROOT / "data" / "samples" / "评测集人工复核.csv"
DEFAULT_MANIFEST_PATH = PROJECT_ROOT / "data" / "samples" / "评测集说明.json"


@dataclass(frozen=True, slots=True)
class Seed:
    case_id: str
    question: str
    competitor: str
    source_type: str
    needle: str
    title_contains: str | None = None
    dimension: str | None = None
    use_version_filter: bool = False
    extra_filters: dict[str, Any] = field(default_factory=dict)


SEEDS: tuple[Seed, ...] = (
    Seed("cursor_official_agent", "Cursor 官网如何描述把开发任务交给 Agent 的工作方式？", "Cursor", "official_page", "handing off tasks to Cursor", dimension="agent_context"),
    Seed("cursor_teams_price", "Cursor Teams 套餐的月费和团队管理能力是什么？", "Cursor", "pricing", "$32 / user / mo.", title_contains="Cursor · Pricing", dimension="performance_cost"),
    Seed("cursor_enterprise_controls", "Cursor Enterprise 套餐提供哪些访问控制和审计能力？", "Cursor", "pricing", "SCIM seat management", title_contains="Cursor · Pricing", dimension="security_compliance"),
    Seed("cursor_side_chats_311", "Cursor 3.11 的 side chat 如何避免打断主 Agent 会话？", "Cursor", "official_changelog", "without interrupting your main agent conversation", title_contains="Side Chats and Conversation Search", dimension="agent_context", use_version_filter=True),
    Seed("cursor_automations_38", "Cursor 3.8 Automations 新增了哪些触发器和计算机操作能力？", "Cursor", "official_changelog", "new triggers for GitHub and Slack, and support for computer use", title_contains="Improvements to Cursor Automations", dimension="agent_context", use_version_filter=True),
    Seed("cursor_bugbot_metrics", "Cursor Bugbot 的评审速度、成本和缺陷发现数量发生了什么变化？", "Cursor", "official_changelog", "average review time for Bugbot is now ~90 seconds", title_contains="Bugbot is now over 3x faster", dimension="performance_cost"),
    Seed("cursor_cloud_subagent_37", "Cursor 3.7 的 /in-cloud 子 Agent 如何隔离长时间任务？", "Cursor", "official_changelog", "runs on its own VM and branch", title_contains="Cloud Environment Setup", dimension="agent_context", use_version_filter=True),
    Seed("cursor_canvas_design_37", "Cursor 3.7 的 Canvas Design Mode 如何让用户指导界面修改？", "Cursor", "official_changelog", "Select and annotate UI elements directly", title_contains="Canvas Design Mode", dimension="agent_context", use_version_filter=True),

    Seed("copilot_official_workflow", "GitHub Copilot 可以在哪些开发工作界面使用 Agent？", "GitHub Copilot", "official_page", "GitHub, your IDE, the CLI, project tools, chat apps", dimension="education_fit"),
    Seed("copilot_ai_credit_value", "GitHub AI Credits 的美元换算关系是什么？", "GitHub Copilot", "pricing", "1 AI credit = $0.01 USD", dimension="performance_cost"),
    Seed("copilot_repository_overview", "第一次浏览仓库时，Copilot repository overview 会汇总哪些信息？", "GitHub Copilot", "official_changelog", "summary of the repository’s purpose, the technologies it uses, and its contribution guidelines", title_contains="Ask Copilot for a repository overview", dimension="agent_context"),
    Seed("copilot_otel_management", "企业如何集中配置 Copilot 的 OpenTelemetry 导出端点和内容采集策略？", "GitHub Copilot", "official_changelog", "mandate where GitHub Copilot sends OpenTelemetry", title_contains="Enterprise-managed OpenTelemetry", dimension="security_compliance"),
    Seed("copilot_codex_jetbrains", "JetBrains IDE 中的 GitHub Copilot 如何启用 Codex Agent Provider？", "GitHub Copilot", "official_changelog", "install the Codex CLI on your machine", title_contains="Codex as agent provider", dimension="model_extensibility"),
    Seed("copilot_app_all_plans", "GitHub Copilot 桌面应用支持哪些套餐和自带模型密钥方式？", "GitHub Copilot", "official_changelog", "Bring your own key (BYOK)", title_contains="GitHub Copilot app available to all", dimension="education_fit"),
    Seed("copilot_mdm_settings", "企业管理员可以通过哪些 MDM 或配置工具下发 Copilot 设置？", "GitHub Copilot", "official_changelog", "Microsoft Intune, Jamf, or Group Policy", title_contains="Deploy managed Copilot settings", dimension="security_compliance"),
    Seed("copilot_usage_metrics", "Copilot usage metrics 对 CLI 代码行和 IDE 识别做了哪些修正？", "GitHub Copilot", "official_changelog", "CLI now reports suggested lines of code", title_contains="Improved accuracy and coverage", dimension="performance_cost"),
    Seed("copilot_actions_token", "在 GitHub Actions 中运行 Copilot CLI 时如何避免保存长期 PAT？", "GitHub Copilot", "official_changelog", "using the built-in GITHUB_TOKEN", title_contains="no longer needs a personal access token", dimension="security_compliance"),
    Seed("copilot_credit_pool", "Copilot cost center 的 AI credit pool 如何限制共享额度消耗？", "GitHub Copilot", "official_changelog", "cap how much of your enterprise’s monthly included AI credits", title_contains="Cost centers now support AI credit pools", dimension="performance_cost"),
    Seed("copilot_vscode_1123_browser", "GitHub Copilot 在 VS Code 1.123 系列中增加了哪些集成浏览器能力？", "GitHub Copilot", "official_changelog", "agents to navigate pages, inspect content, capture screenshots", title_contains="Visual Studio Code, June 2026", dimension="ide_ecosystem", use_version_filter=True),
    Seed("copilot_release_agent_files", "Copilot Chat 0.43 的工作区指令发现修复覆盖哪些文件？", "GitHub Copilot", "github_release", "discover AGENTS.md and CLAUDE.md at workspace roots", title_contains="v0.43.2026040601", dimension="agent_context", use_version_filter=True),
    Seed("copilot_release_response_time", "Copilot Chat 0.38 的低积极度设置如何缩短响应时间？", "GitHub Copilot", "github_release", "Minimize response time for low aggressiveness settings", title_contains="v0.38.2026022702", dimension="agent_context", use_version_filter=True),
    Seed("copilot_release_plan_agent_fields", "Copilot Chat 0.37 为 Plan agent 配置增加了哪些字段？", "GitHub Copilot", "github_release", "Add infer and agents fields to Plan agent config", title_contains="v0.37.2026012901", dimension="agent_context", use_version_filter=True),
    Seed("copilot_release_manual_compaction", "Copilot Chat 0.38 对会话压缩增加了什么能力？", "GitHub Copilot", "github_release", "Support manual compaction", title_contains="v0.38.2026021105", dimension="agent_context", use_version_filter=True),
    Seed("copilot_issue_sandbox_cwd", "Copilot Chat 沙箱为什么会破坏相对路径和 Git 命令？", "GitHub Copilot", "github_issue", "agent's working directory is a temporary directory", title_contains="sandbox sets CWD to temp directory", dimension="security_compliance"),
    Seed("copilot_issue_sandbox_settings", "Copilot Chat 沙箱设置文件为什么可能导致权限提升？", "GitHub Copilot", "github_issue", "A sandboxed process can read and overwrite this file", title_contains="settings file is writable", dimension="security_compliance"),
    Seed("copilot_issue_custom_models", "Copilot Chat 用户提出了怎样的自定义模型接入需求？", "GitHub Copilot", "github_issue", "Add support for custom AI models via API keys", title_contains="custom AI models via API keys", dimension="model_extensibility"),

    Seed("trae_official_products", "TRAE 官网提供哪两个主要产品入口？", "Trae", "official_page", "TRAE Work: Your Professional AI Work Assistant", dimension="ide_ecosystem"),
    Seed("trae_pro_plus_price", "TRAE Pro+ 的月费、用量倍率和并发云任务数是多少？", "Trae", "pricing", "3.5x more usage than Pro", title_contains="Pricing | TRAE", dimension="performance_cost"),
    Seed("trae_ultra_price", "TRAE Ultra 的月费、用量倍率和模型权益是什么？", "Trae", "pricing", "20x more usage than Pro", title_contains="Pricing | TRAE", dimension="performance_cost"),
    Seed("trae_code_folding", "TRAE 用户为什么请求关闭聊天代码块自动折叠？", "Trae", "github_issue", "requires extra clicks for every code block", title_contains="disable code block folding", extra_filters={"current_only": True}),
    Seed("trae_mcp_response", "TRAE 的 run_mcp 工具为何会造成重复解析和额外往返？", "Trae", "github_issue", "requiring double parsing", title_contains="Improve MCP Tool Response Handling", dimension="model_extensibility"),
    Seed("trae_codex_sidebar", "TRAE 与 Codex Sidebar 集成时报告了哪些命令和布局问题？", "Trae", "github_issue", "command 'chatgpt.openSidebar' not found", title_contains="Codex Sidebar partially incompatible", dimension="ide_ecosystem"),
    Seed("trae_sandbox_permission", "TRAE 沙箱权限设计为什么会阻断自动化任务？", "Trae", "github_issue", "生成的脚本都在沙箱里运行", title_contains="沙箱没有权限", dimension="security_compliance"),

    Seed("codegeex_vscode_activation", "CodeGeeX 的 VS Code 扩展激活失败时报告了什么状态码？", "CodeGeeX", "github_issue", "status code 502", title_contains="vscode激活报错", dimension="ide_ecosystem"),
    Seed("codegeex_sequential_mcp", "CodeGeeX 中 sequential-thinking MCP 服务为何属于启动成功但调用失败？", "CodeGeeX", "github_issue", "服务器可以正常启动", title_contains="sequential-thinking MCP", dimension="ide_ecosystem"),
    Seed("codegeex_path_escape", "CodeGeeX 如何错误处理文件路径中的换行和制表转义组合？", "CodeGeeX", "github_issue", "路径中包含`\\n`或`\\t`", title_contains="文件路径错误应用转义符号"),
    Seed("codegeex_commit_message", "CodeGeeX 用户如何评价生成提交消息功能的可用性？", "CodeGeeX", "github_issue", "完全不可用的状态", title_contains="生成提交消息的成功率太低了"),
    Seed("codegeex_rider_2026", "CodeGeeX 用户提出了哪个 Rider 版本的兼容需求？", "CodeGeeX", "github_issue", "rider2026", title_contains="支持一下rider2026"),
    Seed("codegeex_image_upload", "CodeGeeX VS Code 扩展的图片上传提案包含哪些交互方式？", "CodeGeeX", "github_issue", "支持直接粘贴图片到聊天输入框", title_contains="添加图片上传功能", dimension="education_fit"),
    Seed("codegeex_wsl_path", "CodeGeeX 在 VS Code 的 WSL 场景中为何找不到正确路径？", "CodeGeeX", "github_issue", "vscode没有把//wsl 路径带上", title_contains="wsl插件正常运行", dimension="ide_ecosystem"),

    Seed("lingma_agent_planning", "通义灵码编程智能体如何拆解并执行编码任务？", "通义灵码", "official_page", "具备自主规划、自动感知、工具使用等能力", dimension="agent_context"),
    Seed("lingma_terminal_commands", "通义灵码智能体怎样完成终端命令执行？", "通义灵码", "official_page", "自动完成命令编写并运行终端", dimension="agent_context"),
    Seed("lingma_project_awareness", "通义灵码工程自动感知会识别哪些项目上下文？", "通义灵码", "official_page", "自动感知工程框架、技术栈", dimension="agent_context"),
    Seed("lingma_inline_generation", "通义灵码行间代码生成使用哪些上下文并生成什么粒度的代码？", "通义灵码", "official_page", "为你生成行级/函数级代码", dimension="code_intelligence"),
    Seed("lingma_qa_sources", "通义灵码研发智能问答基于哪些文档和知识来源？", "通义灵码", "official_page", "基于海量研发文档、产品文档", dimension="code_intelligence"),
    Seed("lingma_teams_price", "Qoder CN 原灵码企业标准版的单价和 Credits 额度是多少？", "通义灵码", "pricing", "99 元 / 席位·月", dimension="performance_cost"),
    Seed("lingma_changelog_side_question", "Qoder CN CLI 的侧边提问如何减少对主对话的打断？", "通义灵码", "official_changelog", "在不中断主对话的情况下快速提问", title_contains="v0.2.13", dimension="agent_context", use_version_filter=True),
    Seed("lingma_changelog_unified_credits", "Qoder CN 全产品统一 Credits 后，余额和有效期如何处理？", "通义灵码", "official_changelog", "Credits 资源池统一", title_contains="v3.2.0", dimension="performance_cost", use_version_filter=True),
    Seed("lingma_changelog_mcp_elicitation", "Qoder CN CLI 的 MCP Elicitation 支持什么远程交互？", "通义灵码", "official_changelog", "MCP 服务器现可在远程请求中向用户发起输入提示", title_contains="v0.2.16", dimension="ide_ecosystem", use_version_filter=True),
    Seed("lingma_changelog_permissions_proxy", "Qoder CN CLI 1.0.17 在权限和网络访问方面增加了什么能力？", "通义灵码", "official_changelog", "新增 SOCKS5 代理支持", title_contains="v1.0.17", dimension="security_compliance", use_version_filter=True),
)


CSV_FIELDS = (
    "case_id",
    "question",
    "relevant_document_ids",
    "relevant_chunk_ids",
    "relevance_grades",
    "expected_competitor",
    "expected_event_type",
    "expected_dimension_tags",
    "expected_version",
    "expected_evidence_quote",
    "query_filters",
)


AI_REVIEW_FIELDS = (
    "ai_grade",
    "ai_confidence",
    "ai_question_valid",
    "ai_quote_valid",
    "ai_additional_relevance_grades",
    "ai_rationale",
    "ai_model",
    "ai_reviewed_at",
    "ai_review_run_id",
)


HUMAN_REVIEW_FIELDS = (
    "human_grade",
    "question_valid",
    "quote_valid",
    "additional_relevance_grades",
    "reviewer",
    "reviewed_at",
)


REVIEW_FIELDS = (
    "case_id", "question", "chunk_id", "document_id", "competitor",
    "source_type", "title", "product_version", "event_type",
    "dimension_tags", "expected_quote", "content", "suggested_grade",
    *AI_REVIEW_FIELDS,
    *HUMAN_REVIEW_FIELDS,
    "notes",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def _resolve_seed(seed: Seed, chunks: Sequence[Chunk]) -> list[Chunk]:
    candidates = [
        chunk
        for chunk in chunks
        if chunk.competitor == seed.competitor
        and chunk.source_type.value == seed.source_type
        and seed.needle.casefold() in chunk.content.casefold()
        # 评测 relevant chunk 必须是当前版本：case 的 query_filters 固定
        # current_only=True，选到历史版本会导致 validate 时 current_only
        # 过滤不满足，且与真实检索行为不一致。
        and chunk.is_current
        and (
            seed.title_contains is None
            or seed.title_contains.casefold() in chunk.title.casefold()
        )
    ]
    if not candidates:
        raise ValueError(f"seed {seed.case_id} did not resolve to a chunk")
    for chunk in candidates:
        if seed.dimension and seed.dimension not in [item.value for item in chunk.dimension_tags]:
            raise ValueError(
                f"seed {seed.case_id} expects missing dimension {seed.dimension}: "
                f"{chunk.dimension_tags}"
            )
        if seed.use_version_filter and not chunk.product_version:
            raise ValueError(f"seed {seed.case_id} requires a product version")
    return sorted(candidates, key=lambda item: (item.document_id, item.char_start))


def _case(seed: Seed, chunks: Sequence[Chunk]) -> EvaluationCase:
    chunk = chunks[0]
    event_type = chunk.event_type.value if chunk.event_type else None
    filters: dict[str, Any] = {
        "competitor": seed.competitor,
        "source_types": [seed.source_type],
        "current_only": True,
        **seed.extra_filters,
    }
    if event_type:
        filters["event_types"] = [event_type]
    if seed.dimension:
        filters["dimension_tags"] = [seed.dimension]
    expected_version = chunk.product_version if seed.use_version_filter else None
    if expected_version:
        filters["product_versions"] = [expected_version]
    return EvaluationCase(
        case_id=seed.case_id,
        question=seed.question,
        relevant_document_ids=list(dict.fromkeys(item.document_id for item in chunks)),
        relevant_chunk_ids=[item.chunk_id for item in chunks],
        relevance_grades={item.chunk_id: 3.0 for item in chunks},
        expected_competitor=seed.competitor,
        expected_event_type=event_type,
        expected_dimension_tags=[seed.dimension] if seed.dimension else [],
        expected_version=expected_version,
        expected_evidence_quote=seed.needle,
        query_filters=filters,
    )


def _write_cases(path: Path, cases: Sequence[EvaluationCase]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for case in cases:
            payload = case.model_dump(mode="json")
            writer.writerow(
                {
                    field_name: (
                        json.dumps(payload[field_name], ensure_ascii=False, separators=(",", ":"))
                        if isinstance(payload.get(field_name), (list, dict))
                        else payload.get(field_name) or ""
                    )
                    for field_name in CSV_FIELDS
                }
            )


def _write_review(path: Path, rows: Sequence[tuple[Seed, Sequence[Chunk]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        for seed, chunks in rows:
            for chunk in chunks:
                writer.writerow(
                    {
                    "case_id": seed.case_id,
                    "question": seed.question,
                    "chunk_id": chunk.chunk_id,
                    "document_id": chunk.document_id,
                    "competitor": chunk.competitor,
                    "source_type": chunk.source_type.value,
                    "title": chunk.title,
                    "product_version": chunk.product_version or "",
                    "event_type": chunk.event_type.value if chunk.event_type else "",
                    "dimension_tags": json.dumps(
                        [item.value for item in chunk.dimension_tags], ensure_ascii=False
                    ),
                    "expected_quote": seed.needle,
                    "content": chunk.content,
                    "suggested_grade": 3,
                    **{field_name: "" for field_name in AI_REVIEW_FIELDS},
                    "human_grade": "",
                    "question_valid": "",
                    "quote_valid": "",
                    "additional_relevance_grades": "",
                    "reviewer": "",
                    "reviewed_at": "",
                        "notes": "",
                    }
                )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="Mini-RAG YAML path")
    parser.add_argument("--output", help="Candidate evaluation CSV path")
    parser.add_argument("--review-output", help="Human review CSV path")
    parser.add_argument("--manifest-output", help="Manifest JSON path")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings(PROJECT_ROOT, args.config)
    documents_path = settings.documents_path
    output_path = Path(args.output).resolve() if args.output else settings.evaluation_path
    review_path = Path(args.review_output).resolve() if args.review_output else DEFAULT_REVIEW_PATH
    manifest_path = Path(args.manifest_output).resolve() if args.manifest_output else DEFAULT_MANIFEST_PATH
    documents = DocumentLoader(documents_path).load()
    chunks = ChunkingDispatcher(settings.chunking).chunk_documents(documents)
    current_document_count = sum(document.is_current for document in documents)
    historical_document_count = len(documents) - current_document_count
    resolved = [(seed, _resolve_seed(seed, chunks)) for seed in SEEDS]
    cases = [_case(seed, chunks) for seed, chunks in resolved]
    validate_evaluation_cases(cases)
    _write_cases(output_path, cases)
    _write_review(review_path, resolved)
    distributions = {
        "competitors": dict(Counter(case.expected_competitor for case in cases)),
        "sources": dict(Counter(chunks[0].source_type.value for _, chunks in resolved)),
        "events": dict(
            Counter(
                chunks[0].event_type.value if chunks[0].event_type else "unclassified"
                for _, chunks in resolved
            )
        ),
        "dimensions": dict(
            Counter(seed.dimension or "unfiltered" for seed, _ in resolved)
        ),
    }
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "scripts.prepare_evaluation_set",
        "human_review_status": "pending",
        "ai_review_status": "pending",
        "label_origin": "AI-assisted candidate labels",
        "case_count": len(cases),
        "document_count": len(documents),
        "current_document_count": current_document_count,
        "historical_document_count": historical_document_count,
        "chunk_count": len(chunks),
        "documents_path": str(documents_path),
        "documents_sha256": _sha256(documents_path),
        "dataset_path": str(output_path),
        "dataset_sha256": _sha256(output_path),
        "review_path": str(review_path),
        "chunking": settings.chunking.model_dump(mode="json"),
        "distributions": distributions,
        "limitations": [
            "Human relevance review is pending.",
            (
                f"The corpus contains {historical_document_count} historical document versions; "
                "historical-version relevance still requires explicit review cases."
            ),
            "Conflict quality requires manually confirmed conflicting fact samples.",
        ],
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
