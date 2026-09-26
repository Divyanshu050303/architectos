"""Every hot query of projects and requirements is served by its index: found and delivered in
order, against a realistic volume of data with fresh statistics (on near-empty tables the
planner's choice says nothing). If an index were missing, or a query changed so it no longer
matched, the planner would fall back to a disabled Seq Scan or Sort, or another index, and this
fails."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


QUERIES = {
    "architecture of a project": (
        "SELECT * FROM architectures WHERE project_id = :p",
        "uq_architectures_project_id",
        "project",
    ),
    "architecture revision": (
        "SELECT * FROM architecture_revisions WHERE project_id = :p AND number = 7",
        "uq_architecture_revisions_project_id_number",
        "project",
    ),
    "architecture history": (
        "SELECT number, summary FROM architecture_revisions WHERE project_id = :p AND number < 20 "
        "ORDER BY number DESC LIMIT 51",
        "uq_architecture_revisions_project_id_number",
        "project",
    ),
    "project list": (
        "SELECT * FROM projects WHERE organization_id = :p AND deleted_at IS NULL "
        "ORDER BY created_at DESC, id DESC LIMIT 51",
        "ix_projects_organization_id_created_at_id",
        "organization",
    ),
    "project list, filtered by status": (
        "SELECT * FROM projects WHERE organization_id = :p AND deleted_at IS NULL AND status = 'archived' "
        "ORDER BY created_at DESC, id DESC LIMIT 51",
        "ix_projects_organization_id_created_at_id",
        "organization",
    ),
    "project slug lookup": (
        "SELECT id FROM projects WHERE organization_id = :p AND slug = 'x' AND deleted_at IS NULL",
        "uq_projects_organization_id_slug_live",
        "organization",
    ),
    "requirement list": (
        "SELECT * FROM requirements WHERE project_id = :p AND deleted_at IS NULL "
        "ORDER BY created_at DESC, id DESC LIMIT 51",
        "ix_requirements_project_id_created_at_id_live",
        "project",
    ),
    "requirement list, filtered and searched": (
        "SELECT * FROM requirements WHERE project_id = :p AND deleted_at IS NULL AND type = 'capacity' "
        "AND status = 'active' AND lower(title) LIKE '%x%' ORDER BY created_at DESC, id DESC LIMIT 51",
        "ix_requirements_project_id_created_at_id_live",
        "project",
    ),
    "requirements for analysis and sets": (
        "SELECT * FROM requirements WHERE project_id = :p AND deleted_at IS NULL "
        "AND status IN ('active', 'satisfied') ORDER BY number LIMIT 2001",
        "uq_requirements_project_id_number",
        "project",
    ),
    "next requirement number": (
        "SELECT coalesce(max(number), 0) + 1 FROM requirements WHERE project_id = :p",
        "uq_requirements_project_id_number",
        "project",
    ),
    "version history": (
        "SELECT * FROM requirement_versions WHERE requirement_id = :p AND version > 3 "
        "ORDER BY version LIMIT 51",
        "uq_requirement_versions_requirement_id_version",
        "requirement",
    ),
    "requirement set list": (
        "SELECT id FROM requirement_sets WHERE project_id = :p ORDER BY number DESC LIMIT 51",
        "uq_requirement_sets_project_id_number",
        "project",
    ),
    "pinned versions of a set": (
        "SELECT * FROM requirement_set_items WHERE requirement_set_id = :p",
        "pk_requirement_set_items",
        "set",
    ),
    "sets pinning a requirement": (
        "SELECT * FROM requirement_set_items WHERE requirement_id = :p",
        "ix_requirement_set_items_requirement_id_version",
        "requirement",
    ),
}


SEED = [
    # 40 organizations x 50 projects; requirements, versions and sets concentrated in 100 projects,
    # like a real tenant mix. Inserted inside the test transaction (rolled back afterwards).
    "INSERT INTO organizations (id, name) SELECT gen_random_uuid(), 'org ' || g "
    "FROM generate_series(1, 40) g",
    """
    INSERT INTO projects (id, organization_id, name, slug, status, archived_at)
    SELECT gen_random_uuid(), o.id, 'project ' || g, 'project-' || g,
           CASE WHEN g % 10 = 0 THEN 'archived' ELSE 'active' END,
           CASE WHEN g % 10 = 0 THEN now() END
    FROM organizations o CROSS JOIN generate_series(1, 50) g
    """,
    """
    INSERT INTO requirements (id, project_id, number, current_version, type, category, title, statement,
                              priority, status, source, created_at)
    SELECT gen_random_uuid(), p.id, g, 1, 'functional', 'order', 'title ' || g, 'statement ' || g,
           'low', CASE WHEN g % 3 = 0 THEN 'draft' ELSE 'active' END, 'user', now() - g * interval '1 minute'
    FROM (SELECT id FROM projects ORDER BY id LIMIT 100) p CROSS JOIN generate_series(1, 200) g
    """,
    """
    INSERT INTO requirement_versions (id, requirement_id, version, type, category, title, statement,
                                      priority, status, source)
    SELECT gen_random_uuid(), r.id, v, r.type, r.category, r.title, r.statement, r.priority,
           r.status, r.source
    FROM requirements r CROSS JOIN generate_series(1, 2) v
    """,
    """
    INSERT INTO requirement_sets (id, project_id, number, schema_version, planning_input, content_hash,
                                  requirement_count)
    SELECT gen_random_uuid(), p.id, g, 1, '{}'::jsonb, repeat('a', 64), 1
    FROM (SELECT id FROM projects ORDER BY id LIMIT 100) p CROSS JOIN generate_series(1, 20) g
    """,
    """
    INSERT INTO requirement_set_items (requirement_set_id, requirement_id, project_id, version)
    SELECT s.id, r.id, s.project_id, 1
    FROM requirement_sets s JOIN LATERAL (
        SELECT id FROM requirements WHERE project_id = s.project_id ORDER BY number LIMIT 20
    ) r ON true
    """,
    """
    INSERT INTO architectures (id, project_id, current_revision)
    SELECT gen_random_uuid(), id, 30 FROM (SELECT id FROM projects ORDER BY id LIMIT 100) p
    WHERE NOT EXISTS (SELECT 1 FROM architectures a WHERE a.project_id = p.id)
    """,
    """
    INSERT INTO architecture_revisions (id, architecture_id, project_id, number, parent_number, ir,
                                        ir_schema_version, content_hash, source, summary)
    SELECT gen_random_uuid(), a.id, a.project_id, g, NULLIF(g - 1, 0), '{}'::jsonb, 1, repeat('a', 64),
           'user', 'x'
    FROM architectures a CROSS JOIN generate_series(1, 30) g
    WHERE NOT EXISTS (SELECT 1 FROM architecture_revisions r WHERE r.architecture_id = a.id)
    """,
    "ANALYZE organizations, projects, requirements, requirement_versions, requirement_sets, "
    "requirement_set_items, architectures, architecture_revisions",
]


async def test_every_hot_query_uses_its_index(db: AsyncSession) -> None:
    for statement in SEED:
        await db.execute(text(statement))
    busy_project = await db.scalar(
        text(
            "SELECT project_id FROM requirements "
            "WHERE project_id IN (SELECT project_id FROM architectures) LIMIT 1"
        )
    )
    busy_org = await db.scalar(
        text("SELECT organization_id FROM projects WHERE id = :p"), {"p": busy_project}
    )
    busy_requirement = await db.scalar(
        text("SELECT id FROM requirements WHERE project_id = :p LIMIT 1"), {"p": busy_project}
    )
    busy_set = await db.scalar(
        text("SELECT id FROM requirement_sets WHERE project_id = :p LIMIT 1"), {"p": busy_project}
    )
    ids = {
        "project": busy_project,
        "organization": busy_org,
        "requirement": busy_requirement,
        "set": busy_set,
    }

    # Disabling sequential scans and sorts asks the planner: can an index find these rows *and*
    # deliver them in order? If not, it must fall back to a (penalized) Seq Scan or Sort.
    await db.execute(text("SET LOCAL enable_seqscan = off"))
    await db.execute(text("SET LOCAL enable_sort = off"))
    failures = []
    for name, (sql, index, subject) in QUERIES.items():
        plan = "\n".join(row[0] for row in await db.execute(text("EXPLAIN " + sql), {"p": ids[subject]}))
        if index not in plan or "Seq Scan" in plan or "Sort" in plan:
            failures.append(f"{name}: expected {index}\n{plan}")
    assert not failures, "\n\n".join(failures)
