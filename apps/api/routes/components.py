"""The component catalog: categories, entries, exact specification versions with every claim's
provenance, and the evaluation of a configuration against a specification. Read-only reference data
shared by every organization: any signed-in user may read it; no endpoint changes it. Route
handlers only translate HTTP to the service and back."""

from typing import Annotated

from fastapi import APIRouter, Path, Query

from apps.api.dependencies.auth import CurrentUser
from apps.api.dependencies.services import ComponentServiceDep, RateLimitsDep
from apps.api.schemas.common import ErrorResponse
from apps.api.schemas.components import (
    CategoryList,
    CategoryModel,
    ComponentList,
    ComponentSpecificationModel,
    ComponentSummary,
    ConstraintEvaluationModel,
    EvaluateConfigurationRequest,
    VersionList,
    VersionModel,
)
from core.domain.components.entities import SupportStatus

router = APIRouter(tags=["components"])

SEGMENT = r"^[a-z0-9][a-z0-9_.-]{0,63}$"
Directory = Annotated[str, Path(pattern=SEGMENT, description="The category directory, e.g. databases.")]
Entry = Annotated[str, Path(pattern=SEGMENT, description="The entry, e.g. postgresql.")]
_ONE = "/components/{directory}/{entry}"
ERRORS: dict[int | str, dict[str, object]] = {401: {"model": ErrorResponse}}
NOT_FOUND = ERRORS | {404: {"model": ErrorResponse, "description": "component_not_found"}}


@router.get(
    "/components/categories",
    response_model=CategoryList,
    responses=ERRORS,
    summary="Component categories",
    description="Each category with the IR node kinds it models and its entries counted by support status.",
)
async def list_component_categories(current: CurrentUser, components: ComponentServiceDep) -> CategoryList:
    return CategoryList(
        categories=[CategoryModel.of(c) for c in components.categories()],
        catalog_fingerprint=components.fingerprint,
    )


@router.get(
    "/components",
    response_model=ComponentList,
    responses=ERRORS | {422: {"model": ErrorResponse, "description": "validation_error"}},
    summary="Catalog entries",
    description="The current version of each entry, by id, optionally of one category or support status. "
    "An entry's presence is not a claim that it is supported: see supportStatus.",
)
async def list_components(
    current: CurrentUser,
    components: ComponentServiceDep,
    category: Annotated[str | None, Query(max_length=64)] = None,
    status: SupportStatus | None = None,
) -> ComponentList:
    return ComponentList(
        components=[ComponentSummary.of(s) for s in components.list(category=category, status=status)],
        catalog_fingerprint=components.fingerprint,
    )


@router.get(
    _ONE,
    response_model=ComponentSpecificationModel,
    responses=NOT_FOUND | {422: {"model": ErrorResponse, "description": "validation_error"}},
    summary="A component specification",
    description="The current version, or an exact one (version): capabilities, configuration fields, "
    "capacity dimensions, scaling, failure modes, signals, security, billing dimensions (never prices), "
    "operations and constraints, each with its provenance, and the sources cited.",
)
async def get_component(
    directory: Directory,
    entry: Entry,
    current: CurrentUser,
    components: ComponentServiceDep,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> ComponentSpecificationModel:
    component_id = f"{directory}/{entry}"
    spec = components.get(component_id, version)
    return ComponentSpecificationModel.of_version(spec, current=components.get(component_id).ref == spec.ref)


@router.get(
    _ONE + "/versions",
    response_model=VersionList,
    responses=NOT_FOUND,
    summary="Every version of a component specification",
    description="Oldest first; each version stays readable, so an evaluation that used it can be re-read.",
)
async def list_component_versions(
    directory: Directory, entry: Entry, current: CurrentUser, components: ComponentServiceDep
) -> VersionList:
    history = components.history(f"{directory}/{entry}")
    return VersionList(
        component=history[-1].id,
        current=history[-1].version,
        versions=[
            VersionModel(
                version=s.version, ref=s.ref, content_hash=s.content_hash, support_status=s.support_status
            )
            for s in history
        ],
    )


@router.post(
    _ONE + "/evaluate",
    response_model=ConstraintEvaluationModel,
    responses=NOT_FOUND
    | {
        422: {
            "model": ErrorResponse,
            "description": "invalid_architecture (the configuration), validation_error",
        },
        429: {"model": ErrorResponse, "description": "rate_limited"},
    },
    summary="Evaluate a configuration against a specification",
    description="The configuration, as an architecture would state it, against the specification's "
    "documented constraints: pass, warning, violation, cannot_evaluate or not_applicable per check. Nothing "
    "is stored; no throughput or capacity is concluded from the configuration.",
)
async def evaluate_component_configuration(
    directory: Directory,
    entry: Entry,
    body: EvaluateConfigurationRequest,
    current: CurrentUser,
    components: ComponentServiceDep,
    limits: RateLimitsDep,
) -> ConstraintEvaluationModel:
    await limits.enforce("evaluate_component_configuration", user_id=current.user.id)
    evaluation = components.evaluate(
        f"{directory}/{entry}",
        version=body.version,
        kind=body.node_kind,
        technology_version=body.technology_version,
        values=body.configuration.values,
        unknown=body.configuration.unknown,
    )
    return ConstraintEvaluationModel.of(evaluation)
