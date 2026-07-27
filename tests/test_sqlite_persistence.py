from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, inspect, select, text
from sqlalchemy.engine import make_url

from backend.artifact_import import initialize_database, load_artifact_bundle
from backend.config import PROJECT_ROOT, database_url
from backend.database import ALEMBIC_HEAD_REVISION, Database
from backend.models import (
    AgentTraceRecord,
    ArtifactImportRecord,
    Base,
    BriefingRecord,
    CapabilitySnapshotRecord,
    CardEvidenceRecord,
    CompetitorRecord,
    IntelligenceCardRecord,
    SnapshotCardRecord,
    WorkflowRecord,
)
from backend.repositories import AnalysisRepository, CardRepository
from schemas.capability_snapshot import CapabilitySnapshot
from schemas.intelligence_card import AgentAnalysisRequest, AgentRunResult


def _counts(repository: AnalysisRepository) -> dict[str, int]:
    models = {
        "competitors": CompetitorRecord,
        "cards": IntelligenceCardRecord,
        "evidence": CardEvidenceRecord,
        "snapshots": CapabilitySnapshotRecord,
        "snapshot_cards": SnapshotCardRecord,
        "workflows": WorkflowRecord,
        "traces": AgentTraceRecord,
        "briefings": BriefingRecord,
        "imports": ArtifactImportRecord,
    }
    with repository.session_factory() as session:
        return {
            name: session.scalar(select(func.count()).select_from(model)) or 0
            for name, model in models.items()
        }


def test_relative_sqlite_url_is_rooted_at_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "CODERADAR_DATABASE_URL",
        "sqlite:///data/runtime/coderadar.db",
    )

    parsed = make_url(database_url())

    assert parsed.database is not None
    assert Path(parsed.database) == (
        PROJECT_ROOT / "data" / "runtime" / "coderadar.db"
    ).resolve()


def test_non_sqlite_database_url_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = "postgresql+psycopg://coderadar@example.test/coderadar"
    monkeypatch.setenv("CODERADAR_DATABASE_URL", configured)

    assert database_url() == configured


def test_alembic_upgrade_downgrade_upgrade(tmp_path: Path, monkeypatch) -> None:
    database_path = tmp_path / "migration.db"
    monkeypatch.setenv(
        "CODERADAR_DATABASE_URL",
        f"sqlite:///{database_path.as_posix()}",
    )
    config = Config(str(PROJECT_ROOT / "alembic.ini"))

    command.upgrade(config, "head")
    database = Database(f"sqlite:///{database_path.as_posix()}")
    with database.engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            ALEMBIC_HEAD_REVISION
        )
        assert "intelligence_cards" in inspect(connection).get_table_names()

    command.downgrade(config, "base")
    with database.engine.connect() as connection:
        assert "intelligence_cards" not in inspect(connection).get_table_names()

    command.upgrade(config, "head")
    with database.engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
        assert "intelligence_cards" in tables
        assert not any("benchmark" in table.casefold() for table in tables)
    database.dispose()


def test_manifest_import_roundtrip_and_idempotency(
    analysis_repository: AnalysisRepository,
) -> None:
    artifact_root = PROJECT_ROOT / "artifacts" / "week3"
    config_path = PROJECT_ROOT / "config" / "competitors.yaml"

    first = initialize_database(
        analysis_repository,
        competitor_config=config_path,
        artifact_root=artifact_root,
    )
    first_counts = _counts(analysis_repository)
    bundle = load_artifact_bundle(artifact_root)
    second = initialize_database(
        analysis_repository,
        competitor_config=config_path,
        artifact_root=artifact_root,
    )

    assert first.artifact_status == "imported"
    assert second.artifact_status == "already_imported"
    assert first_counts == _counts(analysis_repository)
    assert first_counts["competitors"] == 5
    assert first_counts["cards"] == len(bundle.cards)
    assert first_counts["snapshots"] == len(bundle.snapshots)
    assert first_counts["workflows"] == 5
    assert first_counts["traces"] == 15
    assert first_counts["briefings"] == 5
    assert first_counts["imports"] == 1

    assert {
        item.card_id: item for item in analysis_repository.list_cards()
    } == {item.card_id: item for item in bundle.cards}
    assert {item.snapshot_id for item in analysis_repository.list_snapshots()} == {
        item.snapshot_id for item in bundle.snapshots
    }

    evidenced = next(card for card in bundle.cards if card.evidence)
    links = CardRepository(analysis_repository.session_factory).evidence(
        evidenced.card_id
    )
    assert links == sorted(evidenced.evidence, key=lambda item: item.chunk_id)
    assert all(link.document_id and link.version_id and link.url for link in links)


def test_transaction_rollback_and_concurrent_idempotent_write(
    analysis_repository: AnalysisRepository,
) -> None:
    card = next(
        item
        for item in load_artifact_bundle(PROJECT_ROOT / "artifacts" / "week3").cards
        if item.evidence
    )
    result = AgentRunResult(
        request=AgentAnalysisRequest(competitor=card.competitor),
        cards=[card],
    )

    with pytest.raises(RuntimeError, match="rollback probe"):
        with analysis_repository.transaction() as store:
            store.upsert_cards([card])
            raise RuntimeError("rollback probe")
    assert analysis_repository.get_card(card.card_id) is None

    atomic_snapshot = CapabilitySnapshot(
        snapshot_id="snap_atomic_rollback",
        competitor="Cursor",
    )
    with pytest.raises(ValueError, match="must not be blank"):
        analysis_repository.save_snapshot_and_briefing(
            snapshot=atomic_snapshot,
            competitor="Cursor",
            markdown="   ",
        )
    assert analysis_repository.get_snapshot(atomic_snapshot.snapshot_id) is None

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(analysis_repository.save_agent_result, result)
            for _ in range(8)
        ]
        for future in futures:
            future.result()

    counts = _counts(analysis_repository)
    assert counts["cards"] == 1
    assert counts["evidence"] == len(card.evidence)

    with analysis_repository.session_factory() as session:
        assert session.scalar(text("PRAGMA foreign_keys")) == 1
        assert session.scalar(text("PRAGMA journal_mode")).casefold() == "wal"
        assert session.scalar(text("PRAGMA busy_timeout")) == 5000


def test_repository_revalidates_payloads(
    analysis_repository: AnalysisRepository,
) -> None:
    card = load_artifact_bundle(PROJECT_ROOT / "artifacts" / "week3").cards[0]
    with analysis_repository.transaction() as store:
        store.upsert_cards([card])
    with analysis_repository.session_factory.begin() as session:
        record = session.get(IntelligenceCardRecord, card.card_id)
        assert record is not None
        record.payload = {**record.payload, "competitor": ""}

    with pytest.raises(ValueError):
        analysis_repository.get_card(card.card_id)
