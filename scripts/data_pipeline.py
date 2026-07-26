"""Unified command line for CodeRadar acquisition and processing."""

from __future__ import annotations

import argparse
import importlib
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml
from dotenv import load_dotenv

from crawler import (
    CrawlOrchestrator,
    LinkDiscoverySettings,
    SitemapDiscoverySettings,
)
from processing import ProcessingPipeline, RuleLabeler


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPETITOR_CONFIG = PROJECT_ROOT / "config" / "competitors.yaml"
DIMENSION_CONFIG = PROJECT_ROOT / "config" / "dimensions.yaml"
DATA_ROOT = PROJECT_ROOT / "data"
REQUIRED_COMPETITORS = {
    "cursor",
    "github_copilot",
    "trae",
    "tongyi_lingma",
    "codegeex",
}
REQUIRED_DIMENSION_CODES = {f"D{number}" for number in range(1, 8)}
REQUIRED_SOURCE_TYPES = (
    "official",
    "changelog",
    "pricing",
    "product_docs",
    "status_page",
    "github",
    "plugin_marketplace",
    "community",
    "review",
    "security_privacy",
    "benchmark",
    "rss",
)
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


def _load_yaml(path: Path) -> Mapping[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"配置文件顶层必须是对象：{path}")
    return value


def _configured_labeler() -> RuleLabeler:
    config = _load_yaml(DIMENSION_CONFIG)
    raw_dimensions = config.get("dimensions")
    if not isinstance(raw_dimensions, Mapping):
        raise ValueError("config/dimensions.yaml 缺少 dimensions 对象")
    rules: dict[str, list[str]] = {}
    for name, raw_rule in raw_dimensions.items():
        if not isinstance(raw_rule, Mapping):
            raise ValueError(f"维度 {name} 的规则必须是对象")
        code = str(raw_rule.get("code", "")).strip().upper()
        keywords = raw_rule.get("keywords")
        if not code or not isinstance(keywords, list) or not keywords:
            raise ValueError(f"维度 {name} 缺少 code 或 keywords")
        rules[code] = [str(keyword) for keyword in keywords if str(keyword).strip()]
    return RuleLabeler(
        rules=rules,
        review_threshold=float(config.get("review_threshold", 0.55)),
    )


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        force=True,
    )


def _add_runtime_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--log-level",
        choices=LOG_LEVELS,
        default="INFO",
        help="控制台日志级别（默认：INFO）",
    )


def _add_crawl_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--competitors",
        default="all",
        help="逗号分隔的竞品 ID，或 all（默认：all）",
    )
    parser.add_argument(
        "--sources",
        default="all",
        help=(
            "逗号分隔的来源：official,changelog,pricing,product_docs,"
            "status_page,github,github_release,github_issue,plugin_marketplace,"
            "community,review,security_privacy,benchmark,rss，或 all"
        ),
    )
    parser.add_argument(
        "--since-days",
        type=int,
        default=90,
        help="Changelog、Release 和 Issue 的回填天数（默认：90）",
    )
    parser.add_argument(
        "--max-issues",
        type=int,
        default=100,
        help="每个 GitHub 仓库最多采集的 Issue 数量（默认：100）",
    )
    parser.add_argument(
        "--max-comments",
        type=int,
        default=20,
        help="每条 Issue 最多采集的评论数量（默认：20）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只校验配置并显示任务，不发送请求",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="忽略 ETag 和 Last-Modified 条件缓存",
    )
    _add_runtime_argument(parser)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.data_pipeline",
        description="CodeRadar 多源采集、清洗与结构化命令",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    doctor = commands.add_parser("doctor", help="检查环境、依赖和配置")
    _add_runtime_argument(doctor)

    crawl = commands.add_parser("crawl", help="采集原始数据")
    _add_crawl_arguments(crawl)

    process = commands.add_parser("process", help="清洗并结构化原始数据")
    process.add_argument(
        "--force",
        action="store_true",
        help="重新处理全部原始记录；版本合并保持幂等",
    )
    process.add_argument(
        "--rebuild",
        action="store_true",
        help="仅依据现有原始记录原子重建清洗结果，不保留旧派生版本",
    )
    _add_runtime_argument(process)

    run_all = commands.add_parser("all", help="依次执行采集和结构化处理")
    _add_crawl_arguments(run_all)
    run_all.add_argument(
        "--rebuild",
        action="store_true",
        help="采集后仅依据全部原始记录重建清洗结果",
    )
    return parser


def _doctor_competitors(config: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    defaults = config.get("defaults")
    if not isinstance(defaults, Mapping):
        errors.append("competitors.yaml 缺少 defaults 对象")
    else:
        for key in (
            "request_timeout_seconds",
            "requests_per_second_per_domain",
            "max_retries",
            "since_days",
            "max_issues",
            "max_comments",
            "minimum_visible_text_chars",
        ):
            if key not in defaults:
                errors.append(f"competitors.yaml defaults 缺少 {key}")

    raw_competitors = config.get("competitors")
    if not isinstance(raw_competitors, list):
        return [*errors, "competitors.yaml 的 competitors 必须是数组"], warnings

    competitor_ids: list[str] = []
    for index, item in enumerate(raw_competitors):
        if not isinstance(item, Mapping):
            errors.append(f"competitors[{index}] 必须是对象")
            continue
        competitor_id = str(item.get("id", "")).strip()
        if not competitor_id:
            errors.append(f"competitors[{index}] 缺少 id")
            continue
        competitor_ids.append(competitor_id)
        sources = item.get("sources")
        if not isinstance(sources, Mapping):
            errors.append(f"{competitor_id} 缺少 sources 对象")
            continue
        for source_name in REQUIRED_SOURCE_TYPES:
            source = sources.get(source_name)
            if not isinstance(source, Mapping):
                errors.append(f"{competitor_id}.{source_name} 缺少配置")
                continue
            enabled = source.get("enabled")
            if not isinstance(enabled, bool):
                errors.append(f"{competitor_id}.{source_name}.enabled 必须是布尔值")
            evidence_level = str(source.get("evidence_level", "")).strip().upper()
            if evidence_level not in {"A", "B", "C", "D"}:
                errors.append(
                    f"{competitor_id}.{source_name}.evidence_level 必须是 A、B、C 或 D"
                )
            if not enabled:
                if not str(source.get("note", "")).strip():
                    warnings.append(
                        f"{competitor_id}.{source_name} 已禁用但未说明原因"
                    )
                continue
            if source_name == "github":
                repositories = source.get("repositories")
                if not isinstance(repositories, list) or not repositories:
                    errors.append(f"{competitor_id}.github 缺少 repositories")
                elif any(str(repo).count("/") != 1 for repo in repositories):
                    errors.append(f"{competitor_id}.github 仓库必须使用 owner/name 格式")
            else:
                urls = source.get("urls")
                if not isinstance(urls, list) or not urls:
                    errors.append(f"{competitor_id}.{source_name} 缺少 urls")
                elif any(not str(url).startswith("https://") for url in urls):
                    errors.append(f"{competitor_id}.{source_name} 仅允许 HTTPS URL")

            raw_link_discovery = source.get("link_discovery")
            if raw_link_discovery is not None:
                if not isinstance(raw_link_discovery, Mapping):
                    errors.append(
                        f"{competitor_id}.{source_name}.link_discovery 必须是对象"
                    )
                else:
                    try:
                        LinkDiscoverySettings.from_mapping(raw_link_discovery)
                    except (ValueError, re.error) as exc:
                        errors.append(
                            f"{competitor_id}.{source_name}.link_discovery: {exc}"
                        )

            raw_sitemap_discovery = source.get("sitemap_discovery")
            if raw_sitemap_discovery is not None:
                if not isinstance(raw_sitemap_discovery, Mapping):
                    errors.append(
                        f"{competitor_id}.{source_name}.sitemap_discovery 必须是对象"
                    )
                else:
                    try:
                        sitemap_settings = SitemapDiscoverySettings.from_mapping(
                            raw_sitemap_discovery
                        )
                    except ValueError as exc:
                        errors.append(
                            f"{competitor_id}.{source_name}.sitemap_discovery: {exc}"
                        )
                    else:
                        if any(
                            not url.startswith("https://")
                            for url in sitemap_settings.urls
                        ):
                            errors.append(
                                f"{competitor_id}.{source_name} 的 sitemap 仅允许 HTTPS URL"
                            )

            raw_allowed_item_origins = source.get("allowed_item_origins")
            if raw_allowed_item_origins is not None:
                if not isinstance(raw_allowed_item_origins, list) or not all(
                    isinstance(origin, str)
                    and origin.startswith("https://")
                    and origin.rstrip("/").count("/") == 2
                    for origin in raw_allowed_item_origins
                ):
                    errors.append(
                        f"{competitor_id}.{source_name}.allowed_item_origins "
                        "必须是 HTTPS origin 数组"
                    )

            for json_key in ("json_document", "json_items"):
                json_settings = source.get(json_key)
                if json_settings is None:
                    continue
                if not isinstance(json_settings, Mapping):
                    errors.append(
                        f"{competitor_id}.{source_name}.{json_key} 必须是对象"
                    )
                    continue
                identity_mode = str(
                    json_settings.get("identity_mode") or ""
                ).strip().lower()
                if identity_mode not in {"", "configured", "canonical_url"}:
                    errors.append(
                        f"{competitor_id}.{source_name}.{json_key}.identity_mode "
                        "必须是 configured 或 canonical_url"
                    )
                if identity_mode == "configured" and not (
                    json_settings.get("identity_field")
                    or json_settings.get("identity_template")
                ):
                    errors.append(
                        f"{competitor_id}.{source_name}.{json_key} 的 configured "
                        "身份模式需要 identity_field 或 identity_template"
                    )
                if identity_mode == "canonical_url" and not (
                    json_settings.get("url_field")
                    or json_settings.get("url_template")
                ):
                    errors.append(
                        f"{competitor_id}.{source_name}.{json_key} 的 canonical_url "
                        "身份模式需要 url_field 或 url_template"
                    )
                url_base = json_settings.get("url_base")
                if url_base is not None and not str(url_base).startswith("https://"):
                    errors.append(
                        f"{competitor_id}.{source_name}.{json_key}.url_base "
                        "仅允许 HTTPS URL"
                    )

    actual = set(competitor_ids)
    missing = REQUIRED_COMPETITORS - actual
    extra = actual - REQUIRED_COMPETITORS
    if missing:
        errors.append(f"缺少首期竞品：{', '.join(sorted(missing))}")
    if extra:
        warnings.append(f"配置包含额外竞品：{', '.join(sorted(extra))}")
    if len(competitor_ids) != len(actual):
        errors.append("竞品 ID 存在重复")
    return errors, warnings


def _doctor_dimensions(config: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    threshold = config.get("review_threshold")
    if not isinstance(threshold, (int, float)) or not 0 < float(threshold) < 1:
        errors.append("dimensions.yaml review_threshold 必须在 0 与 1 之间")
    dimensions = config.get("dimensions")
    if not isinstance(dimensions, Mapping):
        return [*errors, "dimensions.yaml 缺少 dimensions 对象"], warnings
    codes: list[str] = []
    for name, rule in dimensions.items():
        if not isinstance(rule, Mapping):
            errors.append(f"维度 {name} 的规则必须是对象")
            continue
        code = str(rule.get("code", "")).strip().upper()
        codes.append(code)
        keywords = rule.get("keywords")
        if not isinstance(keywords, list) or not keywords:
            errors.append(f"维度 {name} 缺少关键词")
    if set(codes) != REQUIRED_DIMENSION_CODES:
        errors.append("维度编码必须完整且固定为 D1—D7")
    if len(codes) != len(set(codes)):
        errors.append("维度编码存在重复")
    return errors, warnings


def run_doctor() -> int:
    checks: list[tuple[str, str, str]] = []
    errors: list[str] = []
    warnings: list[str] = []

    expected_python = (3, 11, 9)
    actual_python = sys.version_info[:3]
    if actual_python == expected_python:
        checks.append(("OK", "Python", ".".join(map(str, actual_python))))
    else:
        errors.append(
            "Python 版本应为 3.11.9，当前为 " + ".".join(map(str, actual_python))
        )

    expected_venv = (PROJECT_ROOT / ".venv").resolve()
    active_prefix = Path(sys.prefix).resolve()
    if active_prefix == expected_venv:
        checks.append(("OK", "虚拟环境", str(expected_venv)))
    else:
        errors.append(f"命令未使用项目虚拟环境：{expected_venv}")

    dependencies = {
        "requests": "requests",
        "BeautifulSoup": "bs4",
        "feedparser": "feedparser",
        "Pydantic": "pydantic",
        "pandas": "pandas",
        "PyYAML": "yaml",
        "python-dateutil": "dateutil",
    }
    missing_dependencies: list[str] = []
    for display_name, module_name in dependencies.items():
        try:
            importlib.import_module(module_name)
        except ImportError:
            missing_dependencies.append(display_name)
    if missing_dependencies:
        errors.append("缺少运行依赖：" + ", ".join(missing_dependencies))
    else:
        checks.append(("OK", "运行依赖", "可导入"))

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            chromium_path = Path(playwright.chromium.executable_path)
        if chromium_path.is_file():
            checks.append(("OK", "Chromium", str(chromium_path)))
        else:
            warnings.append(
                "缺少 Playwright Chromium；动态官网会保留 needs_browser。"
                "运行 python -m playwright install chromium"
            )
    except Exception:
        warnings.append(
            "未安装 Playwright；动态官网会保留 needs_browser。"
            "请从 docs/requirement.txt 重建环境"
        )

    requirement_path = PROJECT_ROOT.parent / "docs" / "requirement.txt"
    if requirement_path.is_file() and (PROJECT_ROOT / "requirements.txt").is_file():
        checks.append(("OK", "依赖清单", str(requirement_path)))
    else:
        errors.append("缺少 docs/requirement.txt 或 CodeRadar/requirements.txt")

    try:
        competitor_config = _load_yaml(COMPETITOR_CONFIG)
        config_errors, config_warnings = _doctor_competitors(competitor_config)
        errors.extend(config_errors)
        warnings.extend(config_warnings)
        if not config_errors:
            checks.append(("OK", "竞品配置", "5 个首期竞品及来源有效"))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        errors.append(f"竞品配置无法读取：{exc}")

    try:
        dimension_config = _load_yaml(DIMENSION_CONFIG)
        dimension_errors, dimension_warnings = _doctor_dimensions(dimension_config)
        errors.extend(dimension_errors)
        warnings.extend(dimension_warnings)
        if not dimension_errors:
            checks.append(("OK", "标签配置", "E1—E3 固定映射，D1—D7 规则有效"))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        errors.append(f"标签配置无法读取：{exc}")

    data_directory_errors: list[str] = []
    for directory in (DATA_ROOT / "raw", DATA_ROOT / "cleaned", DATA_ROOT / "snapshots"):
        if not directory.is_dir():
            data_directory_errors.append(f"缺少数据目录：{directory}")
    errors.extend(data_directory_errors)
    if not data_directory_errors:
        checks.append(("OK", "数据目录", str(DATA_ROOT)))

    if os.getenv("GITHUB_TOKEN", "").strip():
        checks.append(("OK", "GitHub Token", "已配置（值未显示）"))
    else:
        warnings.append("未配置 GITHUB_TOKEN；GitHub 匿名 API 请求限额较低")

    for status, name, detail in checks:
        print(f"[{status}] {name}: {detail}")
    for warning in warnings:
        print(f"[WARN] {warning}")
    for error in errors:
        print(f"[ERROR] {error}")
    print(f"doctor: {len(checks)} 项通过，{len(warnings)} 项警告，{len(errors)} 项错误")
    return 1 if errors else 0


def _crawl_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "competitors": args.competitors,
        "sources": args.sources,
        "since_days": args.since_days,
        "max_issues": args.max_issues,
        "max_comments": args.max_comments,
        "dry_run": args.dry_run,
        "force": args.force,
    }


def run_crawl(args: argparse.Namespace) -> int:
    with CrawlOrchestrator.from_yaml(
        COMPETITOR_CONFIG,
        DATA_ROOT,
        github_token=os.getenv("GITHUB_TOKEN") or None,
    ) as orchestrator:
        summary = orchestrator.run(**_crawl_kwargs(args))

    if summary.dry_run:
        print(f"dry-run: {len(summary.planned_tasks)} 个采集任务")
        for task in summary.planned_tasks:
            details = list(task.urls) or list(task.repositories)
            subtype = ""
            if task.source_type.value == "github":
                release = bool(task.metadata.get("collect_releases", True))
                issue = bool(task.metadata.get("collect_issues", True))
                subtype = f" releases={release} issues={issue}"
            print(
                f"  - {task.competitor}/{task.source_type.value}{subtype}: "
                + ", ".join(details)
            )
    else:
        print(
            f"crawl_run_id={summary.crawl_run_id} "
            f"success={summary.success_count} unchanged={summary.unchanged_count} "
            f"failed={summary.failure_count}"
        )
        for result in summary.results:
            print(
                f"  {result.competitor}/{result.source_type.value}: "
                f"success={result.success_count} unchanged={result.unchanged_count} "
                f"failed={result.failure_count}"
            )
            for error in result.errors:
                print(f"    [ERROR] {error}")
    for error in summary.configuration_errors:
        print(f"[ERROR] {error}")
    return summary.exit_code


def run_process(args: argparse.Namespace) -> int:
    pipeline = ProcessingPipeline(
        project_root=PROJECT_ROOT,
        raw_dir="data/raw",
        output_path="data/cleaned/documents.jsonl",
        labeler=_configured_labeler(),
    )
    result = pipeline.process(
        force=args.force,
        rebuild=getattr(args, "rebuild", False),
    )
    print(
        f"process: scanned={result.scanned} processed={result.processed_records} "
        f"generated={result.generated_documents} skipped={result.skipped} "
        f"added={result.added} unchanged={result.unchanged} "
        f"superseded={result.superseded} written={str(result.output_written).lower()}"
    )
    print(f"output={result.output_path}")
    if not result.output_written:
        print("[ERROR] rebuild 已中止；检测到处理失败，现有清洗结果保持不变")
    for failure in result.failures:
        print(
            f"[ERROR] {failure.meta_path}: {failure.error}"
            + (f" (raw_record_id={failure.raw_record_id})" if failure.raw_record_id else "")
        )
    if not result.failures:
        return 0
    return 2 if result.partial_failure else 1


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.log_level)
    try:
        if args.command == "doctor":
            return run_doctor()
        if args.command == "crawl":
            return run_crawl(args)
        if args.command == "process":
            return run_process(args)
        if args.command == "all":
            crawl_exit = run_crawl(args)
            if args.dry_run or crawl_exit == 1:
                return crawl_exit
            process_exit = run_process(args)
            if process_exit == 1:
                return 1
            return 2 if crawl_exit == 2 or process_exit == 2 else 0
        parser.error(f"未知命令：{args.command}")
    except KeyboardInterrupt:
        print("[ERROR] 用户中断运行", file=sys.stderr)
        return 1
    except Exception as exc:
        logging.getLogger(__name__).exception("pipeline failed")
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
