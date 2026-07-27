"""Evidence-first LangChain foundation for the week-three specialist Agents."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mini_rag.evidence import CitationValidator
from mini_rag.models import Conflict, RAGQuery, RAGResponse
from schemas.document import DimensionTag, EventType, EvidenceLevel
from schemas.intelligence_card import (
    AgentAnalysisRequest,
    AgentKind,
    AgentRunResult,
    CapabilityImpact,
    EvidenceReference,
    FindingType,
    ImpactDirection,
    IntelligenceCard,
    PriorityBreakdown,
    RiskLevel,
    StructuredFinding,
)

from .callbacks import AgentTraceCallback
from .lcel import runnable_lambda, structured_tool
from .llm import LangChainLLMClient, LLMCardDraft


_EVIDENCE_WEIGHTS: dict[EvidenceLevel, float] = {
    EvidenceLevel.A: 1.0,
    EvidenceLevel.B: 0.82,
    EvidenceLevel.C: 0.62,
    EvidenceLevel.D: 0.36,
}


@dataclass(frozen=True)
class AgentConfig:
    kind: AgentKind
    event_type: EventType
    prompt_file: str
    default_question: str
    dimension_focus: tuple[DimensionTag, ...]
    event_label: str
    impact_label: str
    opportunity_label: str
    threat_label: str
    action_label: str
    base_impact: float = 0.68


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _compact(text: str | None, limit: int = 180) -> str:
    if not text:
        return "unknown"
    normalised = " ".join(text.split())
    return normalised if len(normalised) <= limit else f"{normalised[: limit - 3]}..."


def _risk_from_priority(score: int, *, has_evidence: bool) -> RiskLevel:
    if not has_evidence:
        return RiskLevel.UNKNOWN
    if score >= 80:
        return RiskLevel.CRITICAL
    if score >= 60:
        return RiskLevel.HIGH
    if score >= 40:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


class EvidenceBackedAgent:
    """LCEL chain with optional ReAct retrieval and guarded card generation."""

    config: AgentConfig

    def __init__(
        self,
        rag_service: Any,
        *,
        llm_client: LangChainLLMClient | None = None,
        auto_configure_llm: bool = True,
        llm_mode: str | None = None,
    ) -> None:
        self.rag_service = rag_service
        self.prompt_text = self._load_prompt()
        self.llm_client = (
            LangChainLLMClient.from_env(mode=llm_mode)
            if auto_configure_llm and llm_client is None
            else llm_client
        )
        self._rag_tool = structured_tool(
            func=self._query_rag,
            name=f"retrieve_{self.config.kind.value}_evidence",
            description=(
                "Query the local CodeRadar Mini-RAG index with strict competitor, "
                "event, dimension, version and time filters."
            ),
            args_schema=RAGQuery,
        ).with_retry(
            retry_if_exception_type=(ConnectionError, TimeoutError),
            wait_exponential_jitter=True,
            stop_after_attempt=2,
        )
        self.pipeline = (
            runnable_lambda(self._prepare_query, name=f"{self.config.kind.value}_prepare")
            | runnable_lambda(
                self._retrieve_evidence,
                name=f"{self.config.kind.value}_rag_tool",
            )
            | runnable_lambda(
                self._compose_result,
                name=f"{self.config.kind.value}_guard_and_structure",
            )
        ).with_config(
            {
                "tags": ["week3", self.config.kind.value, "evidence-backed"],
                "metadata": {"agent_kind": self.config.kind.value},
            }
        )

    @property
    def uses_langchain(self) -> bool:
        return self.pipeline.__class__.__module__.startswith("langchain_core")

    def run(self, request: AgentAnalysisRequest | dict[str, Any]) -> AgentRunResult:
        parsed = (
            request
            if isinstance(request, AgentAnalysisRequest)
            else AgentAnalysisRequest.model_validate(request)
        )
        if parsed.event_type and parsed.event_type != self.config.event_type:
            raise ValueError(
                f"{self.config.kind.value} Agent only accepts "
                f"event_type={self.config.event_type.value}"
            )
        callback = AgentTraceCallback()
        result: AgentRunResult = self.pipeline.invoke(
            {"request": parsed, "trace_callback": callback},
            config={
                "callbacks": [callback],
                "metadata": {
                    "competitor": parsed.competitor,
                    "correlation_id": parsed.correlation_id,
                },
            },
        )
        fallback_used = any("fallback" in warning.casefold() for warning in result.warnings)
        trace = callback.finish(
            agent_kind=self.config.kind,
            fallback_used=fallback_used,
            model_name=self.llm_client.model_name if self.llm_client else None,
        )
        return AgentRunResult.model_validate(
            {**result.model_dump(mode="python"), "trace": trace}
        )

    def _load_prompt(self) -> str:
        path = _repo_root() / "prompts" / self.config.prompt_file
        if not path.exists():
            raise FileNotFoundError(f"Agent prompt is missing: {path}")
        prompt = path.read_text(encoding="utf-8").strip()
        if not prompt:
            raise ValueError(f"Agent prompt is empty: {path}")
        return prompt

    def _query_rag(self, **payload: Any) -> RAGResponse:
        return self.rag_service.query(RAGQuery.model_validate(payload))

    def _prepare_query(self, payload: dict[str, Any]) -> dict[str, Any]:
        request: AgentAnalysisRequest = payload["request"]
        dimensions = request.dimension_tags or list(self.config.dimension_focus)
        question = request.question or self.config.default_question.format(
            competitor=request.competitor
        )
        query = RAGQuery(
            question=question,
            competitor=request.competitor,
            event_types=[self.config.event_type],
            dimension_tags=dimensions,
            product_versions=request.product_versions,
            evidence_levels=request.evidence_levels,
            source_types=request.source_types,
            start_time=request.start_time,
            end_time=request.end_time,
            current_only=request.current_only,
            top_k=request.top_k,
        )
        return {**payload, "query": query}

    def _retrieve_evidence(self, payload: dict[str, Any]) -> dict[str, Any]:
        query: RAGQuery = payload["query"]
        callback: AgentTraceCallback = payload["trace_callback"]
        retrieval_warnings: list[str] = []
        if self.llm_client is not None and self.llm_client.supports_react:
            try:
                react_result = self.llm_client.retrieve_with_react(
                    base_query=query,
                    specialist_prompt=self.prompt_text,
                    agent_kind=self.config.kind.value,
                    search=self.rag_service.query,
                    callbacks=[callback],
                )
                response = react_result.response
                callback.mark_react(react_result.tool_call_count)
            except Exception as exc:
                if self.llm_client.strict:
                    raise
                retrieval_warnings.append(
                    "ReAct retrieval fallback to deterministic RAG query: "
                    f"{type(exc).__name__}: {exc}"
                )
                response = self._invoke_rag_tool(query, callback)
        else:
            response = self._invoke_rag_tool(query, callback)
        return {
            **payload,
            "rag_response": response,
            "retrieval_warnings": retrieval_warnings,
        }

    def _invoke_rag_tool(
        self,
        query: RAGQuery,
        callback: AgentTraceCallback,
    ) -> RAGResponse:
        return self._rag_tool.invoke(
            query.model_dump(mode="python"),
            config={"callbacks": [callback]},
        )

    def _compose_result(self, payload: dict[str, Any]) -> AgentRunResult:
        request: AgentAnalysisRequest = payload["request"]
        response: RAGResponse = payload["rag_response"]
        callback: AgentTraceCallback = payload["trace_callback"]
        references = [EvidenceReference.from_evidence(item) for item in response.evidence]
        validation = CitationValidator().validate(
            references,
            response,
            allowed_chunk_ids=[item.chunk_id for item in response.evidence],
            require_quote=True,
        )
        if not validation.valid:
            raise ValueError(
                "retrieved evidence failed citation validation: "
                + "; ".join(validation.errors)
            )

        warnings = [
            *payload.get("retrieval_warnings", []),
            *response.retrieval_trace.warnings,
            *validation.warnings,
        ]
        warnings.extend(self._conflict_messages(validation.conflicts))
        clusters = self._event_clusters(references)
        if len(clusters) > request.max_cards:
            warnings.append(
                "event card limit applied: retained "
                f"{request.max_cards} of {len(clusters)} evidence events"
            )
        selected_clusters = clusters[: request.max_cards] or [[]]
        cards: list[IntelligenceCard] = []

        for event_references in selected_clusters:
            dimensions = self._dimension_tags(request, event_references)
            confidence = self._confidence(event_references)
            breakdown = self._priority_breakdown(
                event_references,
                dimensions,
                confidence,
            )
            card = self._build_card(
                request,
                event_references,
                dimensions,
                confidence,
                breakdown,
                rag_query_id=response.query_id,
            )

            if self.llm_client is not None and event_references:
                try:
                    draft = self.llm_client.draft_card(
                        prompt_text=self.prompt_text,
                        agent_kind=self.config.kind.value,
                        competitor=request.competitor,
                        question=payload["query"].question,
                        evidence=event_references,
                        callbacks=[callback],
                    )
                    card = self._apply_llm_draft(card, draft)
                except Exception as exc:
                    if self.llm_client.strict:
                        raise
                    warnings.append(
                        "LLM structured-output fallback to deterministic rules for "
                        f"event {card.card_id}: {type(exc).__name__}: {exc}"
                    )

            event_chunk_ids = {item.chunk_id for item in event_references}
            event_conflicts = self._conflict_messages(
                validation.conflicts,
                allowed_chunk_ids=event_chunk_ids,
            )
            if event_conflicts:
                card = IntelligenceCard.model_validate(
                    {
                        **card.model_dump(mode="python"),
                        "conflict_notes": list(
                            dict.fromkeys([*card.conflict_notes, *event_conflicts])
                        ),
                        "review_required": True,
                    }
                )
            cards.append(card)

        if not references:
            warnings.append("no RAG evidence matched the Agent request")
        return AgentRunResult(
            request=request,
            rag_query_id=response.query_id,
            cards=cards,
            warnings=warnings,
        )

    def _apply_llm_draft(
        self,
        card: IntelligenceCard,
        draft: LLMCardDraft,
    ) -> IntelligenceCard:
        allowed = {item.chunk_id for item in card.evidence}
        findings = [
            StructuredFinding(
                finding_type=item.finding_type,
                title=item.title,
                summary=item.summary,
                evidence_chunk_ids=item.evidence_chunk_ids,
                confidence_score=min(card.confidence_score, item.confidence_score),
            )
            for item in draft.findings
        ]
        impacts = [
            CapabilityImpact(
                dimension=item.dimension,
                direction=item.direction,
                magnitude=item.magnitude,
                confidence_score=min(card.confidence_score, item.confidence_score),
                rationale=item.rationale,
                evidence_chunk_ids=item.evidence_chunk_ids,
            )
            for item in draft.impact_details
        ]
        cited = {
            chunk_id
            for item in [*findings, *impacts]
            for chunk_id in item.evidence_chunk_ids
        }
        unknown = cited - allowed
        if unknown:
            raise ValueError(
                "model referenced chunk IDs outside the retrieval whitelist: "
                + ", ".join(sorted(unknown))
            )
        merged = {
            **card.model_dump(mode="python"),
            "card_id": "",
            "event_title": draft.event_title,
            "summary": draft.summary,
            "change_before": draft.change_before,
            "change_after": draft.change_after,
            "impact_analysis": draft.impact_analysis,
            "relevance_to_our_product": draft.relevance_to_our_product,
            "threat_level": draft.threat_level,
            "opportunity": draft.opportunity,
            "threat": draft.threat,
            "recommended_action": draft.recommended_action,
            "confidence_score": min(card.confidence_score, draft.confidence_score),
            "findings": findings,
            "impact_details": impacts or card.impact_details,
            "conflict_notes": draft.conflict_notes,
            "assumptions": draft.assumptions,
            "analysis_mode": self.llm_client.analysis_mode,
            "model_name": self.llm_client.model_name if self.llm_client else None,
        }
        # Full validation is intentional: model_copy(update=...) would skip
        # citation, alert and stable-ID invariants.
        return IntelligenceCard.model_validate(merged)

    def _dimension_tags(
        self,
        request: AgentAnalysisRequest,
        references: list[EvidenceReference],
    ) -> list[DimensionTag]:
        if request.dimension_tags:
            return request.dimension_tags
        values: list[DimensionTag] = []
        for reference in references:
            values.extend(reference.dimension_tags)
        if not values and references:
            values = list(self.config.dimension_focus)
        return list(dict.fromkeys(values))

    @staticmethod
    def _event_clusters(
        references: list[EvidenceReference],
    ) -> list[list[EvidenceReference]]:
        """Group ranked chunks into stable document-version events.

        A document version is the strongest event identity available in the
        Mini-RAG evidence contract.  A regular insertion-ordered dict preserves
        the rank of the first chunk for each event while merging later chunks
        from that same source version.
        """

        clusters: dict[tuple[str, str], list[EvidenceReference]] = {}
        for reference in references:
            key = (reference.document_id, reference.version_id)
            clusters.setdefault(key, []).append(reference)
        return list(clusters.values())

    def _confidence(self, references: list[EvidenceReference]) -> float:
        if not references:
            return 0.12
        weights = [_EVIDENCE_WEIGHTS[item.evidence_level] for item in references]
        source_score = sum(weights) / len(weights)
        volume_bonus = min(len({item.chunk_id for item in references}), 5) * 0.045
        return round(min(0.95, 0.24 + source_score * 0.55 + volume_bonus), 2)

    def _priority_breakdown(
        self,
        references: list[EvidenceReference],
        dimensions: list[DimensionTag],
        confidence: float,
    ) -> PriorityBreakdown:
        if not references:
            return PriorityBreakdown(
                event_impact=0.2,
                urgency=0.2,
                evidence_confidence=confidence,
                product_relevance=0.3,
            )
        latest = max(
            (item.publish_time for item in references if item.publish_time),
            default=None,
        )
        if latest:
            if latest.tzinfo is None:
                latest = latest.replace(tzinfo=timezone.utc)
            days = max(0, (datetime.now(timezone.utc) - latest).days)
            urgency = 0.95 if days <= 30 else 0.72 if days <= 120 else 0.45
        else:
            urgency = 0.5
        overlap = len(set(dimensions) & set(self.config.dimension_focus))
        relevance = min(
            1.0,
            0.35 + 0.65 * overlap / max(len(self.config.dimension_focus), 1),
        )
        return PriorityBreakdown(
            event_impact=self.config.base_impact,
            urgency=urgency,
            evidence_confidence=confidence,
            product_relevance=relevance,
        )

    def _default_direction(self) -> ImpactDirection:
        if self.config.kind == AgentKind.PRODUCT:
            return ImpactDirection.POSITIVE
        if self.config.kind == AgentKind.RISK:
            return ImpactDirection.NEGATIVE
        return ImpactDirection.MIXED

    def _build_card(
        self,
        request: AgentAnalysisRequest,
        references: list[EvidenceReference],
        dimensions: list[DimensionTag],
        confidence: float,
        breakdown: PriorityBreakdown,
        *,
        rag_query_id: str,
    ) -> IntelligenceCard:
        first = references[0] if references else None
        quote = _compact(first.quote if first else None, limit=220)
        title_source = first.title if first else request.competitor
        findings = [
            StructuredFinding(
                finding_type=FindingType.FACT,
                title=reference.title,
                summary=_compact(reference.quote, limit=180),
                evidence_chunk_ids=[reference.chunk_id],
                confidence_score=confidence,
            )
            for reference in references[:3]
        ]
        impacts: list[CapabilityImpact] = []
        for dimension in dimensions:
            dimension_evidence = [
                item.chunk_id for item in references if dimension in item.dimension_tags
            ]
            if not dimension_evidence and references and len(dimensions) == 1:
                dimension_evidence = [item.chunk_id for item in references]
            if not dimension_evidence:
                continue
            impacts.append(
                CapabilityImpact(
                    dimension=dimension,
                    direction=self._default_direction(),
                    magnitude=max(1, min(10, round(self.config.base_impact * confidence * 10))),
                    confidence_score=confidence,
                    rationale=(
                        f"由 {self.config.event_label}证据推导，方向采用 "
                        f"{self.config.kind.value} Agent 的保守规则，需结合原文复核。"
                    ),
                    evidence_chunk_ids=list(dict.fromkeys(dimension_evidence)),
                )
            )
        assumptions: list[str] = []
        if not references:
            assumptions.append("当前没有检索到可引用证据，结论仅作为待补证据的占位。")
        return IntelligenceCard(
            agent_kind=self.config.kind,
            competitor=request.competitor,
            event_type=self.config.event_type,
            dimension_tags=dimensions,
            event_title=f"{request.competitor} {self.config.event_label}: {_compact(title_source, 80)}",
            summary=self._summary(request, references),
            change_before="unknown",
            change_after=quote,
            impact_analysis=self.config.impact_label.format(
                competitor=request.competitor,
                evidence_count=len(references),
            ),
            relevance_to_our_product=(
                "结论围绕 CodeMate Campus 的中文教学、编程实验、Agent 辅助、"
                "成本可控和学术诚信边界进行评估。"
            ),
            threat_level=_risk_from_priority(
                breakdown.score,
                has_evidence=bool(references),
            ),
            opportunity=self.config.opportunity_label,
            threat=self.config.threat_label,
            recommended_action=self.config.action_label,
            confidence_score=confidence,
            priority_score=breakdown.score,
            priority_breakdown=breakdown,
            evidence=references,
            findings=findings,
            impact_details=impacts,
            assumptions=assumptions,
            analysis_mode="rules",
            rag_query_id=rag_query_id,
            prompt_name=self.config.prompt_file,
        )

    def _summary(
        self,
        request: AgentAnalysisRequest,
        references: list[EvidenceReference],
    ) -> str:
        if not references:
            return (
                f"未从 Mini-RAG 检索到 {request.competitor} 的"
                f"{self.config.event_label}证据，需要补充采集或放宽过滤条件。"
            )
        titles = "；".join(_compact(item.title, 72) for item in references[:3])
        return (
            f"基于 {len(references)} 条可追溯证据，{request.competitor} "
            f"存在与{self.config.event_label}相关的动态：{titles}。"
        )

    @staticmethod
    def _conflict_messages(
        conflicts: list[Conflict],
        *,
        allowed_chunk_ids: set[str] | None = None,
    ) -> list[str]:
        messages: list[str] = []
        for conflict in conflicts:
            if allowed_chunk_ids is not None and not (
                set(conflict.chunk_ids) & allowed_chunk_ids
            ):
                continue
            values = " | ".join(conflict.values)
            chunks = ", ".join(conflict.chunk_ids)
            messages.append(
                f"{conflict.field}: conflicting values [{values}], "
                f"chunks [{chunks}]. {conflict.reason}".strip()
            )
        return messages


__all__ = ["AgentConfig", "EvidenceBackedAgent"]
