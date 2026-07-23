"""Run the single-slot durable Multi-Agent Workflow worker."""

from __future__ import annotations

import signal

from agents.orchestrator import MultiAgentOrchestrator
from backend.database import get_database
from backend.services.rag_service import get_rag_service
from backend.workflow_repository import AsyncWorkflowRepository
from backend.workflow_worker import WorkflowWorker


def main() -> int:
    database = get_database()
    database.check_connection()
    database.check_migration()
    worker = WorkflowWorker(
        None,
        AsyncWorkflowRepository(database.session_factory),
        orchestrator_factory=lambda mode: MultiAgentOrchestrator(
            get_rag_service(),
            llm_mode=mode,
        ),
    )

    def request_stop(_signum, _frame) -> None:
        worker.stop()

    for signal_name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, signal_name):
            signal.signal(getattr(signal, signal_name), request_stop)
    worker.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
