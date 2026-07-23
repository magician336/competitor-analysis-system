from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from agents.orchestrator import MultiAgentOrchestrator
from agents.callbacks import AgentTraceCallback
from backend.main import create_app
from backend.models import WorkflowAttemptRecord, WorkflowRecord
from backend.services.workflow_service import WorkflowService, get_workflow_service
from backend.workflow_repository import AsyncWorkflowRepository
from backend.workflow_worker import WorkflowWorker, WorkflowWorkerSettings
from schemas.capability_snapshot import CapabilitySnapshot
from schemas.intelligence_card import (
    AgentAnalysisRequest,
    AgentExecutionTrace,
    AgentKind,
    AgentRunResult,
)
from schemas.orchestration import (
    BranchExecutionStatus,
    BranchOutcome,
    SpecialistBranch,
)
from schemas.workflow import WorkflowStatus, WorkflowSubmitRequest


class FakeSpecialist:
    def __init__(
        self,
        branch: SpecialistBranch,
        *,
        fail_times: int = 0,
        delay: float = 0.0,
        started: threading.Event | None = None,
        release: threading.Event | None = None,
    ) -> None:
        self.branch = branch
        self.fail_times = fail_times
        self.delay = delay
        self.started = started
        self.release = release
        self.calls = 0
        self.started_at: list[float] = []
        self.completed_at: list[float] = []

    def run(self, request) -> AgentRunResult:
        self.calls += 1
        self.started_at.append(time.perf_counter())
        try:
            if self.started is not None:
                self.started.set()
            if self.release is not None:
                assert self.release.wait(timeout=5)
            if self.delay:
                time.sleep(self.delay)
            if self.calls <= self.fail_times:
                raise TimeoutError(f"{self.branch.value} failed")
            return AgentRunResult(
                request=request,
                trace=AgentExecutionTrace(
                    trace_id=f"trace_{self.branch.value}_{self.calls}",
                    agent_kind=self.branch.value,
                    duration_ms=self.delay * 1_000,
                ),
            )
        finally:
            self.completed_at.append(time.perf_counter())


class FakeCompare:
    def __init__(self, *, fail_times: int = 0) -> None:
        self.calls = 0
        self.fail_times = fail_times

    def build_snapshot(self, competitor, cards, **_kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("compare failed")
        return CapabilitySnapshot(competitor=competitor)


class FakeBriefing:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, competitor, cards, snapshot, **_kwargs):
        self.calls += 1
        return f"# {competitor} briefing"


def _orchestrator(
    *,
    price: FakeSpecialist | None = None,
    product: FakeSpecialist | None = None,
    risk: FakeSpecialist | None = None,
    compare: FakeCompare | None = None,
):
    price = price or FakeSpecialist(SpecialistBranch.PRICE)
    product = product or FakeSpecialist(SpecialistBranch.PRODUCT)
    risk = risk or FakeSpecialist(SpecialistBranch.RISK)
    compare = compare or FakeCompare()
    briefing = FakeBriefing()
    orchestrator = MultiAgentOrchestrator(
        price_agent=price,
        product_agent=product,
        risk_agent=risk,
        compare_agent=compare,
        briefing_agent=briefing,
        benchmark_agent=object(),
    )
    return orchestrator, price, product, risk, compare, briefing


def _repository(analysis_repository) -> AsyncWorkflowRepository:
    return AsyncWorkflowRepository(analysis_repository.session_factory)


def _worker(
    analysis_repository,
    orchestrator: MultiAgentOrchestrator,
    *,
    worker_id: str = "test-worker",
    timeout: int = 30,
) -> WorkflowWorker:
    return WorkflowWorker(
        orchestrator,
        _repository(analysis_repository),
        worker_id=worker_id,
        settings=WorkflowWorkerSettings(
            poll_seconds=0.01,
            lease_seconds=3,
            heartbeat_seconds=1,
            timeout_seconds=timeout,
        ),
    )


def test_workflow_api_submit_is_idempotent_and_benchmark_free(
    analysis_repository,
) -> None:
    repository = _repository(analysis_repository)
    app = create_app()
    app.dependency_overrides[get_workflow_service] = lambda: WorkflowService(repository)
    client = TestClient(app)
    payload = {"competitor": "Cursor", "correlation_id": "async-api-1"}

    first = client.post("/api/workflows", json=payload)
    second = client.post("/api/workflows", json=payload)

    assert first.status_code == second.status_code == 202
    assert first.json()["workflow_id"] == second.json()["workflow_id"]
    assert first.json()["deduplicated"] is False
    assert second.json()["deduplicated"] is True
    workflow_id = first.json()["workflow_id"]
    status = client.get(f"/api/workflows/{workflow_id}")
    assert status.status_code == 200
    assert status.json()["status"] == "queued"
    assert len(status.json()["branches"]) == 3
    assert client.get("/api/workflows/workflow_missing").status_code == 404

    rejected = client.post(
        "/api/workflows",
        json={"competitor": "Cursor", "benchmark_tasks": []},
    )
    assert rejected.status_code == 422

    request = WorkflowSubmitRequest(
        competitor="Cursor",
        correlation_id="concurrent-idempotency",
    ).to_analysis_request()
    repositories = [
        AsyncWorkflowRepository(analysis_repository.session_factory)
        for _ in range(8)
    ]
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(lambda repo: repo.submit(request), repositories)
        )
    assert len({item.workflow_id for item in results}) == 1
    assert sum(not item.deduplicated for item in results) == 1


def test_worker_runs_one_parallel_workflow_and_persists_result(
    analysis_repository,
) -> None:
    specialists = [
        FakeSpecialist(branch, delay=0.12) for branch in SpecialistBranch
    ]
    orchestrator, price, product, risk, compare, briefing = _orchestrator(
        price=specialists[0],
        product=specialists[1],
        risk=specialists[2],
    )
    repository = _repository(analysis_repository)
    submission = repository.submit(
        WorkflowSubmitRequest(
            competitor="Cursor",
            correlation_id="parallel-workflow",
        ).to_analysis_request()
    )
    worker = _worker(analysis_repository, orchestrator)

    started = time.perf_counter()
    assert worker.run_once() is True
    elapsed = time.perf_counter() - started
    worker.close()

    # Prove that all three specialist calls overlapped instead of relying on a
    # machine-speed threshold that becomes flaky during the complete suite.
    assert max(item.started_at[0] for item in specialists) < min(
        item.completed_at[0] for item in specialists
    )
    assert elapsed < 1.0
    assert [price.calls, product.calls, risk.calls] == [1, 1, 1]
    assert compare.calls == briefing.calls == 1
    result = repository.get(submission.workflow_id)
    assert result.status == WorkflowStatus.SUCCESS
    assert result.progress == 100
    assert result.result is not None
    assert {branch.status.value for branch in result.branches} == {"success"}
    with analysis_repository.session_factory() as session:
        attempt = session.get(WorkflowAttemptRecord, (submission.workflow_id, 1))
        assert attempt is not None
        assert attempt.metrics == {
            "llm_call_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }


def test_worker_selects_orchestrator_from_persisted_analysis_mode(
    analysis_repository,
) -> None:
    repository = _repository(analysis_repository)
    submission = repository.submit(
        WorkflowSubmitRequest(
            competitor="Cursor",
            correlation_id="hybrid-workflow",
            analysis_mode="hybrid",
        ).to_analysis_request()
    )
    selected_modes: list[str] = []

    def factory(mode: str) -> MultiAgentOrchestrator:
        selected_modes.append(mode)
        return _orchestrator()[0]

    worker = WorkflowWorker(
        None,
        repository,
        orchestrator_factory=factory,
        worker_id="mode-aware-worker",
        settings=WorkflowWorkerSettings(
            poll_seconds=0.01,
            lease_seconds=3,
            heartbeat_seconds=1,
            timeout_seconds=30,
        ),
    )

    assert worker.run_once() is True
    worker.close()
    status = repository.get(submission.workflow_id)
    assert selected_modes == ["hybrid"]
    assert status.analysis_mode == "hybrid"
    assert status.status == WorkflowStatus.SUCCESS


def test_partial_failure_retry_runs_only_failed_branch(
    analysis_repository,
) -> None:
    product = FakeSpecialist(SpecialistBranch.PRODUCT, fail_times=1)
    orchestrator, price, product, risk, compare, briefing = _orchestrator(
        product=product
    )
    repository = _repository(analysis_repository)
    submission = repository.submit(
        WorkflowSubmitRequest(
            competitor="Cursor",
            correlation_id="retry-failed-branch",
        ).to_analysis_request()
    )
    worker = _worker(analysis_repository, orchestrator)

    assert worker.run_once() is True
    first = repository.get(submission.workflow_id)
    assert first.status == WorkflowStatus.PARTIAL_FAILURE
    assert [price.calls, product.calls, risk.calls] == [1, 1, 1]

    retry = repository.retry(submission.workflow_id)
    assert retry.status == WorkflowStatus.QUEUED
    assert worker.run_once() is True
    worker.close()

    final = repository.get(submission.workflow_id)
    assert final.status == WorkflowStatus.SUCCESS
    assert final.attempt_count == 2
    assert [price.calls, product.calls, risk.calls] == [1, 2, 1]
    assert compare.calls == briefing.calls == 2


def test_downstream_retry_reuses_all_specialist_results(
    analysis_repository,
) -> None:
    compare = FakeCompare(fail_times=1)
    orchestrator, price, product, risk, compare, _ = _orchestrator(compare=compare)
    repository = _repository(analysis_repository)
    submission = repository.submit(
        WorkflowSubmitRequest(
            competitor="Cursor",
            correlation_id="retry-downstream",
        ).to_analysis_request()
    )
    worker = _worker(analysis_repository, orchestrator)

    worker.run_once()
    assert repository.get(submission.workflow_id).status == (
        WorkflowStatus.PARTIAL_FAILURE
    )
    repository.retry(submission.workflow_id)
    worker.run_once()
    worker.close()

    assert repository.get(submission.workflow_id).status == WorkflowStatus.SUCCESS
    assert [price.calls, product.calls, risk.calls] == [1, 1, 1]
    assert compare.calls == 2


def test_queued_and_running_workflow_cancellation(analysis_repository) -> None:
    repository = _repository(analysis_repository)
    queued = repository.submit(
        WorkflowSubmitRequest(
            competitor="Cursor",
            correlation_id="cancel-queued",
        ).to_analysis_request()
    )
    assert repository.cancel(queued.workflow_id).status == WorkflowStatus.CANCELLED
    assert repository.cancel(queued.workflow_id).status == WorkflowStatus.CANCELLED

    started = threading.Event()
    release = threading.Event()
    specialists = [
        FakeSpecialist(branch, started=started, release=release)
        for branch in SpecialistBranch
    ]
    orchestrator, *_ = _orchestrator(
        price=specialists[0], product=specialists[1], risk=specialists[2]
    )
    running = repository.submit(
        WorkflowSubmitRequest(
            competitor="Cursor",
            correlation_id="cancel-running",
        ).to_analysis_request()
    )
    worker = _worker(analysis_repository, orchestrator)
    thread = threading.Thread(target=worker.run_once)
    thread.start()
    assert started.wait(timeout=3)
    waiting = repository.submit(
        WorkflowSubmitRequest(
            competitor="Cursor",
            correlation_id="wait-for-running-workflow",
        ).to_analysis_request()
    )
    assert repository.get(waiting.workflow_id).status == WorkflowStatus.QUEUED
    second_worker = _worker(
        analysis_repository,
        orchestrator,
        worker_id="second-worker",
    )
    assert second_worker.run_once() is False
    assert repository.cancel(running.workflow_id).status == WorkflowStatus.CANCELLING
    release.set()
    thread.join(timeout=5)
    worker.close()
    second_worker.close()

    assert repository.get(running.workflow_id).status == WorkflowStatus.CANCELLED


def test_stale_lease_requeues_and_singleton_blocks_second_worker(
    analysis_repository,
) -> None:
    repository = _repository(analysis_repository)
    submission = repository.submit(
        WorkflowSubmitRequest(
            competitor="Cursor",
            correlation_id="stale-worker",
        ).to_analysis_request()
    )
    assert repository.acquire_worker_lease("worker-a", lease_seconds=30)
    claim = repository.claim_next(
        "worker-a", lease_seconds=30, timeout_seconds=30
    )
    assert claim is not None
    repository.branch_started(
        claim.workflow_id,
        claim.attempt,
        SpecialistBranch.PRICE,
    )
    repository.branch_completed(
        claim.workflow_id,
        claim.attempt,
        BranchOutcome(
            branch=SpecialistBranch.PRICE,
            status=BranchExecutionStatus.SUCCESS,
            duration_ms=1,
            result=AgentRunResult(
                request=AgentAnalysisRequest(competitor="Cursor"),
                trace=AgentExecutionTrace(
                    trace_id="trace_recovered_price",
                    agent_kind=AgentKind.PRICE,
                ),
            ),
        ),
    )
    assert not repository.acquire_worker_lease("worker-b", lease_seconds=30)
    with analysis_repository.transaction() as store:
        record = store.session.get(WorkflowRecord, submission.workflow_id)
        assert record is not None
        record.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    assert repository.recover_stale() == 1
    recovered = repository.get(submission.workflow_id)
    assert recovered.status == WorkflowStatus.QUEUED
    assert recovered.attempt_count == 1
    second_claim = repository.claim_next(
        "worker-a", lease_seconds=30, timeout_seconds=30
    )
    assert second_claim is not None
    reusable = repository.reusable_outcomes(
        second_claim.workflow_id,
        second_claim.request,
    )
    assert set(reusable) == {SpecialistBranch.PRICE}
    repository.release_worker_lease("worker-a")


def test_workflow_timeout_is_terminal_and_retry_budget_is_bounded(
    analysis_repository,
) -> None:
    specialists = [
        FakeSpecialist(branch, delay=1.1) for branch in SpecialistBranch
    ]
    orchestrator, *_ = _orchestrator(
        price=specialists[0], product=specialists[1], risk=specialists[2]
    )
    repository = _repository(analysis_repository)
    submission = repository.submit(
        WorkflowSubmitRequest(
            competitor="Cursor",
            correlation_id="workflow-timeout",
        ).to_analysis_request()
    )
    worker = _worker(analysis_repository, orchestrator, timeout=1)
    worker.run_once()
    assert repository.get(submission.workflow_id).status == WorkflowStatus.TIMED_OUT
    worker.close()

    failing = FakeSpecialist(SpecialistBranch.PRODUCT, fail_times=2)
    retry_orchestrator, *_ = _orchestrator(product=failing)
    retry_submission = repository.submit(
        WorkflowSubmitRequest(
            competitor="Cursor",
            correlation_id="retry-budget",
        ).to_analysis_request()
    )
    retry_worker = _worker(
        analysis_repository,
        retry_orchestrator,
        worker_id="retry-worker",
    )
    retry_worker.run_once()
    repository.retry(retry_submission.workflow_id)
    retry_worker.run_once()
    retry_worker.close()
    assert repository.get(retry_submission.workflow_id).attempt_count == 2
    try:
        repository.retry(retry_submission.workflow_id)
    except Exception as exc:
        assert "budget" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("exhausted retry budget must reject another retry")


def test_trace_callback_records_provider_token_usage_once_per_run() -> None:
    callback = AgentTraceCallback()
    run_id = uuid4()
    callback.on_chat_model_start(None, [], run_id=run_id)
    callback.on_llm_start(None, [], run_id=run_id)
    callback.on_llm_end(
        SimpleNamespace(
            llm_output={
                "token_usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "total_tokens": 18,
                }
            },
            generations=[],
        ),
        run_id=run_id,
    )
    trace = callback.finish(
        agent_kind=AgentKind.PRICE,
        fallback_used=False,
        model_name="test-model",
    )

    assert trace.llm_call_count == 1
    assert trace.input_tokens == 11
    assert trace.output_tokens == 7
    assert trace.total_tokens == 18
