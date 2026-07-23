"""Transparent rule-based E1--E3 and D1--D7 classification."""

from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path
from typing import Mapping, Sequence

import yaml

from schemas.document import DimensionTag, EventType, SourceType

from .normalizers import normalize_text


DEFAULT_DIMENSION_RULES: dict[DimensionTag, tuple[str, ...]] = {
    DimensionTag.CODE_INTELLIGENCE: (
        "code generation",
        "code completion",
        "autocomplete",
        "debug",
        "refactor",
        "code review",
        "代码生成",
        "代码补全",
        "调试",
        "重构",
        "代码审查",
    ),
    DimensionTag.AGENT_CONTEXT: (
        "agent",
        "context window",
        "codebase",
        "repository context",
        "memory",
        "mcp",
        "智能体",
        "上下文",
        "代码库",
        "仓库理解",
        "记忆",
    ),
    DimensionTag.IDE_ECOSYSTEM: (
        "ide",
        "vs code",
        "vscode",
        "jetbrains",
        "extension",
        "plugin",
        "editor",
        "编辑器",
        "插件",
        "扩展",
    ),
    DimensionTag.MODEL_EXTENSIBILITY: (
        "model",
        "gpt",
        "claude",
        "gemini",
        "deepseek",
        "bring your own key",
        "byok",
        "模型",
        "自定义模型",
    ),
    DimensionTag.PERFORMANCE_COST: (
        "price",
        "pricing",
        "cost",
        "quota",
        "request limit",
        "latency",
        "performance",
        "fast",
        "价格",
        "费用",
        "额度",
        "延迟",
        "性能",
        "速度",
    ),
    DimensionTag.SECURITY_COMPLIANCE: (
        "security",
        "privacy",
        "compliance",
        "soc 2",
        "gdpr",
        "vulnerability",
        "data retention",
        "安全",
        "隐私",
        "合规",
        "漏洞",
        "数据保留",
    ),
    DimensionTag.EDUCATION_FIT: (
        "education",
        "student",
        "teacher",
        "classroom",
        "campus",
        "academic",
        "教育",
        "学生",
        "教师",
        "课堂",
        "校园",
    ),
}


@dataclass(frozen=True, slots=True)
class LabelResult:
    event_type: EventType | None
    dimension_tags: list[DimensionTag]
    label_confidence: float
    label_reasons: list[str]
    needs_review: bool


class RuleLabeler:
    """Configurable keyword labeler with auditable matching reasons."""

    def __init__(
        self,
        rules: Mapping[str | DimensionTag, Sequence[str]] | None = None,
        review_threshold: float = 0.55,
    ) -> None:
        if not 0 <= review_threshold <= 1:
            raise ValueError("review_threshold must be between 0 and 1")
        source_rules = rules or DEFAULT_DIMENSION_RULES
        self.rules: dict[DimensionTag, tuple[str, ...]] = {}
        for raw_tag, keywords in source_rules.items():
            tag = raw_tag if isinstance(raw_tag, DimensionTag) else self._parse_dimension(raw_tag)
            normalised = tuple(
                dict.fromkeys(
                    normalize_text(keyword, preserve_lines=False).casefold()
                    for keyword in keywords
                    if normalize_text(keyword, preserve_lines=False)
                )
            )
            self.rules[tag] = normalised
        self.review_threshold = review_threshold

    @classmethod
    def from_yaml(cls, path: str | Path) -> "RuleLabeler":
        """Load dimension keywords and the review threshold from YAML."""

        config_path = Path(path)
        try:
            payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise ValueError(f"Cannot load dimension rules from {config_path}: {exc}") from exc
        dimensions = payload.get("dimensions")
        if not isinstance(dimensions, Mapping) or not dimensions:
            raise ValueError(f"Dimension config {config_path} must define a non-empty dimensions mapping")
        rules: dict[str, Sequence[str]] = {}
        for dimension, definition in dimensions.items():
            if not isinstance(definition, Mapping):
                raise ValueError(f"Dimension {dimension!r} must be a mapping")
            keywords = definition.get("keywords", [])
            if isinstance(keywords, str) or not isinstance(keywords, Sequence):
                raise ValueError(f"Dimension {dimension!r} keywords must be a list")
            rules[str(dimension)] = [str(keyword) for keyword in keywords]
        try:
            threshold = float(payload.get("review_threshold", 0.55))
        except (TypeError, ValueError) as exc:
            raise ValueError("review_threshold must be numeric") from exc
        return cls(rules=rules, review_threshold=threshold)

    @staticmethod
    def _parse_dimension(value: str) -> DimensionTag:
        key = value.strip().upper()
        code_map = {
            "D1": DimensionTag.CODE_INTELLIGENCE,
            "D2": DimensionTag.AGENT_CONTEXT,
            "D3": DimensionTag.IDE_ECOSYSTEM,
            "D4": DimensionTag.MODEL_EXTENSIBILITY,
            "D5": DimensionTag.PERFORMANCE_COST,
            "D6": DimensionTag.SECURITY_COMPLIANCE,
            "D7": DimensionTag.EDUCATION_FIT,
        }
        if key in code_map:
            return code_map[key]
        return DimensionTag(value.strip().lower())

    @staticmethod
    def event_for_source(source_type: SourceType | str) -> EventType | None:
        aliases = {
            "official": SourceType.OFFICIAL_PAGE,
            "changelog": SourceType.OFFICIAL_CHANGELOG,
        }
        if isinstance(source_type, SourceType):
            source = source_type
        else:
            raw_source = str(source_type).strip().lower()
            source = aliases[raw_source] if raw_source in aliases else SourceType(raw_source)
        if source == SourceType.PRICING:
            return EventType.PRICING_CHANGE
        if source in {SourceType.OFFICIAL_CHANGELOG, SourceType.GITHUB_RELEASE, SourceType.RSS}:
            return EventType.PRODUCT_RELEASE
        if source in {
            SourceType.GITHUB_ISSUE,
            SourceType.STATUS_PAGE,
            SourceType.PLUGIN_MARKETPLACE,
            SourceType.COMMUNITY,
            SourceType.REVIEW,
        }:
            return EventType.RISK_EXPERIENCE
        return None

    @staticmethod
    def _matches_keyword(haystack: str, keyword: str) -> bool:
        """Match Latin keywords on token boundaries and CJK terms by substring."""

        if re.search(r"[\u3400-\u9fff]", keyword):
            return keyword in haystack
        escaped = re.escape(keyword)
        plural = r"(?:s|es)?" if keyword and keyword[-1].isalnum() else ""
        pattern = rf"(?<![a-z0-9]){escaped}{plural}(?![a-z0-9])"
        return re.search(pattern, haystack, flags=re.IGNORECASE) is not None

    def label(self, source_type: SourceType | str, title: str, content: str) -> LabelResult:
        """Classify one document and return labels plus exact matched terms."""

        aliases = {
            "official": SourceType.OFFICIAL_PAGE,
            "changelog": SourceType.OFFICIAL_CHANGELOG,
        }
        if isinstance(source_type, SourceType):
            source = source_type
        else:
            raw_source = str(source_type).strip().lower()
            source = aliases[raw_source] if raw_source in aliases else SourceType(raw_source)
        haystack = normalize_text(f"{title}\n{content}", preserve_lines=False).casefold()
        matched: dict[DimensionTag, list[str]] = {}
        for tag, keywords in self.rules.items():
            found = [
                keyword
                for keyword in keywords
                if self._matches_keyword(haystack, keyword)
            ]
            if found:
                matched[tag] = found

        dimension_tags = list(matched)
        event_type = self.event_for_source(source)
        reasons: list[str] = []
        if event_type:
            reasons.append(f"source:{source.value}->{event_type.value}")
        for tag, keywords in matched.items():
            reasons.append(f"{tag.value}:keywords={','.join(keywords[:5])}")

        if not dimension_tags:
            confidence = 0.35 if event_type else 0.0
        else:
            total_matches = sum(len(values) for values in matched.values())
            confidence = min(0.95, 0.52 + 0.08 * min(total_matches, 4) + 0.03 * min(len(matched) - 1, 3))
        needs_review = not dimension_tags or confidence < self.review_threshold
        return LabelResult(
            event_type=event_type,
            dimension_tags=dimension_tags,
            label_confidence=round(confidence, 3),
            label_reasons=reasons,
            needs_review=needs_review,
        )
