"""Requirement analyses and the provenance of promoted requirements, in the database."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain.requirements.analyses import NewRequirementAnalysis, RequirementAnalysis
from core.domain.requirements.errors import CandidateAlreadyPromoted
from engines.requirements.extractor import extract
from persistence.models import (
    OrganizationRecord,
    ProjectRecord,
    RequirementAnalysisRecord,
    RequirementRecord,
    UserRecord,
)
from persistence.repositories.requirement_analyses import SqlAlchemyRequirementAnalysisRepository
from persistence.repositories.requirements import SqlAlchemyRequirementRepository

pytestmark = pytest.mark.integration

RAW = "  Support at least 2000 rps.\n"


async def add_in_savepoint(db: AsyncSession, record: object) -> None:
    async with db.begin_nested():
        db.add(record)
        await db.flush()


class Context:
    def __init__(self, user: UserRecord, project: ProjectRecord, other: ProjectRecord) -> None:
        self.user, self.project, self.other = user, project, other


@pytest.fixture
async def context(db: AsyncSession) -> Context:
    org = OrganizationRecord(name="Acme")
    user = UserRecord(email="ada@example.com", name="Ada", password_hash="$argon2id$x")
    db.add_all([org, user])
    await db.flush()
    project = ProjectRecord(organization_id=org.id, name="Food Delivery", slug="food-delivery")
    other = ProjectRecord(organization_id=org.id, name="Payments", slug="payments")
    db.add_all([project, other])
    await db.flush()
    return Context(user, project, other)


async def analysis(
    db: AsyncSession, context: Context, project: ProjectRecord | None = None
) -> RequirementAnalysis:
    return await SqlAlchemyRequirementAnalysisRepository(db).add(
        NewRequirementAnalysis(
            (project or context.project).id, RAW, "rules-1.0.0", {"candidates": []}, context.user.id
        )
    )


async def test_an_analysis_keeps_the_raw_input_exactly(db: AsyncSession, context: Context) -> None:
    stored = await analysis(db, context)
    assert (stored.raw_input, stored.engine_version) == (RAW, "rules-1.0.0")
    assert await SqlAlchemyRequirementAnalysisRepository(db).get(context.project.id, stored.id) == stored
    assert await SqlAlchemyRequirementAnalysisRepository(db).get(context.other.id, stored.id) is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"raw_input": ""},
        {"raw_input": "x" * 20_001},
        {"input_sha256": "abc"},
        {"engine_version": ""},
        {"result": []},
    ],
    ids=["empty", "too-long", "hash", "engine-version", "result-array"],
)
async def test_analysis_constraints(db: AsyncSession, context: Context, overrides: dict[str, object]) -> None:
    fields: dict[str, object] = {
        "project_id": context.project.id,
        "raw_input": "x",
        "input_sha256": "0" * 64,
        "engine_version": "rules-1.0.0",
        "result": {},
    }
    with pytest.raises(IntegrityError):
        await add_in_savepoint(db, RequirementAnalysisRecord(**(fields | overrides)))


@pytest.mark.parametrize(
    "statement",
    [
        update(RequirementAnalysisRecord).values(raw_input="rewritten"),
        delete(RequirementAnalysisRecord),
        text("TRUNCATE requirement_analyses CASCADE"),
    ],
    ids=["update", "delete", "truncate"],
)
async def test_analyses_are_append_only(db: AsyncSession, context: Context, statement: object) -> None:
    await analysis(db, context)
    with pytest.raises(DBAPIError, match="append-only"):
        async with db.begin_nested():
            await db.execute(statement)  # type: ignore[call-overload]


async def test_promotion_records_the_origin_and_never_duplicates(db: AsyncSession, context: Context) -> None:
    stored = await analysis(db, context)
    [candidate] = extract(RAW).candidates
    requirements = SqlAlchemyRequirementRepository(db)
    promoted = await requirements.add(
        candidate.to_new_requirement(
            project_id=context.project.id, created_by_user_id=context.user.id, analysis_id=stored.id
        )
    )
    assert promoted.origin is not None
    assert (promoted.origin.analysis_id, promoted.origin.candidate_key) == (stored.id, candidate.key)
    assert (await requirements.get(context.project.id, promoted.id)) == promoted

    with pytest.raises(CandidateAlreadyPromoted) as error:
        await requirements.add(
            candidate.to_new_requirement(
                project_id=context.project.id, created_by_user_id=context.user.id, analysis_id=stored.id
            )
        )
    assert error.value.details == {"requirement_id": str(promoted.id)}

    # The transaction is still usable, and after a deliberate delete the candidate can be promoted again.
    await requirements.save_deleted(promoted.delete(datetime.now(UTC)))
    again = await requirements.add(
        candidate.to_new_requirement(
            project_id=context.project.id, created_by_user_id=context.user.id, analysis_id=stored.id
        )
    )
    assert again.id != promoted.id


async def test_an_origin_must_be_an_analysis_of_the_same_project(db: AsyncSession, context: Context) -> None:
    foreign = await analysis(db, context, context.other)
    [candidate] = extract(RAW).candidates
    with pytest.raises(IntegrityError, match="fk_requirements_origin_requirement_analyses"):
        async with db.begin_nested():
            await SqlAlchemyRequirementRepository(db).add(
                candidate.to_new_requirement(
                    project_id=context.project.id, created_by_user_id=context.user.id, analysis_id=foreign.id
                )
            )


@pytest.mark.parametrize(
    ("column", "value"),
    [("origin_candidate_key", "cand_0123456789abcdef"), ("origin_analysis_id", uuid.uuid4())],
    ids=["key-without-analysis", "analysis-without-key"],
)
async def test_an_origin_is_complete_or_absent(
    db: AsyncSession, context: Context, column: str, value: object
) -> None:
    requirements = SqlAlchemyRequirementRepository(db)
    [candidate] = extract(RAW).candidates
    created = await requirements.add(
        candidate.to_new_requirement(project_id=context.project.id, created_by_user_id=context.user.id)
    )
    await db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await db.execute(
                update(RequirementRecord).where(RequirementRecord.id == created.id).values({column: value})
            )


async def test_a_candidate_key_has_the_engine_format(db: AsyncSession, context: Context) -> None:
    stored = await analysis(db, context)
    requirements = SqlAlchemyRequirementRepository(db)
    [candidate] = extract(RAW).candidates
    created = await requirements.add(
        candidate.to_new_requirement(project_id=context.project.id, created_by_user_id=context.user.id)
    )
    with pytest.raises(IntegrityError, match="origin_candidate_key_format"):
        async with db.begin_nested():
            await db.execute(
                update(RequirementRecord)
                .where(RequirementRecord.id == created.id)
                .values(origin_analysis_id=stored.id, origin_candidate_key="REQ-1")
            )
