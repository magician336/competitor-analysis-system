"""Idempotently create the two local CodeRadar demonstration accounts."""

from __future__ import annotations

from sqlalchemy import select

from backend.auth import AuthRepository
from backend.database import Database, get_database
from backend.models import WorkflowRecord
from schemas.orchestration import MultiAgentAnalysisRequest


DEMO_USERS = (
    ("coderadar_test1", "CodeRadar@2026-1"),
    ("coderadar_test2", "CodeRadar@2026-2"),
)


def seed_demo_users(database: Database | None = None) -> dict[str, object]:
    database = database or get_database()
    repository = AuthRepository(database.session_factory)
    created: list[str] = []
    existing: list[str] = []
    first_user_id: str | None = None
    for index, (username, password) in enumerate(DEMO_USERS):
        user, was_created = repository.create_user_if_missing(
            username=username,
            password=password,
        )
        if index == 0:
            first_user_id = user.user_id
        (created if was_created else existing).append(username)

    legacy_workflows_assigned = 0
    if first_user_id is not None:
        with database.session_factory.begin() as session:
            records = list(
                session.scalars(
                    select(WorkflowRecord).where(WorkflowRecord.user_id.is_(None))
                )
            )
            for record in records:
                if record.submitted_at is None:
                    record.user_id = first_user_id
                    legacy_workflows_assigned += 1
                    continue
                try:
                    request = MultiAgentAnalysisRequest.model_validate(
                        record.request_payload
                    )
                except (TypeError, ValueError):
                    continue
                # Revision 0004 and later scope every new machine/user
                # fingerprint. Only the original unscoped value marks a legacy
                # report that should be assigned to demo account one.
                if record.request_fingerprint != request.fingerprint:
                    continue
                record.user_id = first_user_id
                legacy_workflows_assigned += 1
    return {
        "created": created,
        "existing": existing,
        "legacy_workflows_assigned": legacy_workflows_assigned,
    }


def main() -> int:
    result = seed_demo_users()
    print(
        "Demo users ready: "
        f"created={len(result['created'])}, "
        f"existing={len(result['existing'])}, "
        f"legacy_workflows_assigned={result['legacy_workflows_assigned']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["DEMO_USERS", "seed_demo_users"]
