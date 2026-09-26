"""Configuration rules: combinations of properties that each pass the IR's property definitions
(type, range, applicability) but contradict one another. Only the IR's own property definitions
are used: no component catalog is available (see ``CATALOG_UNAVAILABLE``), so nothing here claims
what a particular technology supports."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from core.architecture_ir.dependency import ConnectionKind, Interaction
from core.domain.validation.results import Category, Finding, Severity

from ..context import ValidationContext
from ..engine import Outcome, RuleMeta
from ..findings import finding
from .consistency import ALL_PROFILES


@dataclass(frozen=True, slots=True)
class ReplicasWithinAutoscaling:
    meta = RuleMeta(
        "configuration.replicas-autoscaling",
        1,
        "Replicas within the autoscaling range",
        "A stated replica count lies between the autoscaler's minimum and maximum.",
        Category.CONFIGURATION,
        Severity.MEDIUM,
        profiles=ALL_PROFILES,
    )

    def evaluate(self, context: ValidationContext, parameters: Mapping[str, Any]) -> Outcome:
        found: list[Finding] = []
        for node in context.ir.nodes:
            config = node.configuration
            replicas = config.get("replicas")
            low, high = config.get("autoscaling_min_replicas"), config.get("autoscaling_max_replicas")
            if not isinstance(replicas, int):
                continue
            expected = f"{low if low is not None else '…'} ≤ replicas ≤ {high if high is not None else '…'}"
            for bound, value, outside in (
                ("autoscaling_min_replicas", low, isinstance(low, int) and replicas < low),
                ("autoscaling_max_replicas", high, isinstance(high, int) and replicas > high),
            ):
                if outside:
                    found.append(
                        finding(
                            self.meta,
                            "replicas_outside_autoscaling",
                            title=f"{node.name} runs {replicas} replicas outside its autoscaling range",
                            explanation=(
                                f"replicas is {replicas} but {bound} is {value}: the autoscaler "
                                "would change the replica count as soon as it runs, so one of the "
                                "values does not describe the system."
                            ),
                            remediation="Align replicas with the autoscaling range, or correct the range.",
                            entity_ids=[node.id],
                            field_paths=["configuration.replicas", f"configuration.{bound}"],
                            expected=expected,
                            actual=str(replicas),
                        )
                    )
        return Outcome(tuple(found))


@dataclass(frozen=True, slots=True)
class ZonesMatchMultiAz:
    meta = RuleMeta(
        "configuration.availability-zones",
        1,
        "Availability zones agree with multi-AZ",
        "multi_az and availability_zones, when both are stated, say the same thing.",
        Category.CONFIGURATION,
        Severity.MEDIUM,
        profiles=ALL_PROFILES,
    )

    def evaluate(self, context: ValidationContext, parameters: Mapping[str, Any]) -> Outcome:
        found: list[Finding] = []
        for node in context.ir.nodes:
            multi_az = node.configuration.get("multi_az")
            zones = node.configuration.get("availability_zones")
            if not isinstance(multi_az, bool) or not isinstance(zones, tuple):
                continue
            paths = ["configuration.multi_az", "configuration.availability_zones"]
            if multi_az and len(zones) < 2:
                found.append(
                    finding(
                        self.meta,
                        "multi_az_single_zone",
                        title=f"{node.name} is multi-AZ in a single zone",
                        explanation=(
                            "multi_az is true but only one availability zone is listed, so a zone "
                            "failure would take the component down despite the multi-AZ claim."
                        ),
                        remediation="List every zone it runs in, or set multi_az to false.",
                        entity_ids=[node.id],
                        field_paths=paths,
                        expected="at least 2 zones",
                        actual=f"{len(zones)} zone",
                    )
                )
            elif not multi_az and len(zones) >= 2:
                found.append(
                    finding(
                        self.meta,
                        "zones_without_multi_az",
                        title=f"{node.name} lists {len(zones)} zones but is not multi-AZ",
                        explanation="multi_az is false while several availability zones are listed.",
                        remediation="Set multi_az to true, or list the one zone it runs in.",
                        entity_ids=[node.id],
                        field_paths=paths,
                        severity=Severity.LOW,
                        expected="multi_az true",
                        actual="false",
                    )
                )
        return Outcome(tuple(found))


@dataclass(frozen=True, slots=True)
class BackupsRetained:
    meta = RuleMeta(
        "configuration.backups",
        1,
        "Backups and their retention agree",
        "Enabled backups are kept for some time; a retention period implies backups.",
        Category.CONFIGURATION,
        Severity.MEDIUM,
        profiles=ALL_PROFILES,
    )

    def evaluate(self, context: ValidationContext, parameters: Mapping[str, Any]) -> Outcome:
        found: list[Finding] = []
        for node in context.ir.nodes:
            enabled = node.configuration.get("backup_enabled")
            retention = node.configuration.get("backup_retention_seconds")
            if not isinstance(enabled, bool) or not isinstance(retention, int):
                continue
            paths = ["configuration.backup_enabled", "configuration.backup_retention_seconds"]
            if enabled and retention == 0:
                found.append(
                    finding(
                        self.meta,
                        "backups_not_retained",
                        title=f"{node.name} takes backups but keeps none",
                        explanation="backup_enabled is true while backup_retention_seconds is 0.",
                        remediation="Set how long backups are kept.",
                        entity_ids=[node.id],
                        field_paths=paths,
                        expected="a retention above 0 seconds",
                        actual="0",
                    )
                )
            elif not enabled and retention > 0:
                found.append(
                    finding(
                        self.meta,
                        "retention_without_backups",
                        title=f"{node.name} has a backup retention but no backups",
                        explanation="backup_retention_seconds is set while backup_enabled is false.",
                        remediation="Enable backups, or remove the retention.",
                        entity_ids=[node.id],
                        field_paths=paths,
                        severity=Severity.LOW,
                    )
                )
        return Outcome(tuple(found))


_WAITING = (ConnectionKind.REQUEST, ConnectionKind.DATA_ACCESS)


@dataclass(frozen=True, slots=True)
class RetriesNeedTimeouts:
    meta = RuleMeta(
        "configuration.retries-without-timeout",
        1,
        "Retries have a timeout",
        "A connection the source waits on and retries also states how long it waits.",
        Category.CONFIGURATION,
        Severity.MEDIUM,
        profiles=ALL_PROFILES,
    )

    def evaluate(self, context: ValidationContext, parameters: Mapping[str, Any]) -> Outcome:
        return Outcome(tuple(self._findings(context)))

    def _findings(self, context: ValidationContext) -> Iterator[Finding]:
        for connection in context.ir.connections:
            config = connection.configuration
            retries = config.get("retries")
            if (
                connection.kind not in _WAITING
                or connection.interaction is Interaction.ASYNCHRONOUS
                or not isinstance(retries, int)
                or retries == 0
                or config.get("timeout_seconds") is not None
                or config.is_unknown("timeout_seconds")  # reported as unknown, not as missing
            ):
                continue
            yield finding(
                self.meta,
                "retries_without_timeout",
                title=f"{connection.name or connection.id} retries without a timeout",
                explanation=(
                    f"The connection retries {retries} times but states no timeout, so a target "
                    "that stops answering holds the caller for as long as the platform default, "
                    "once per retry."
                ),
                remediation="State timeout_seconds for this connection.",
                entity_ids=[connection.id],
                field_paths=["configuration.retries", "configuration.timeout_seconds"],
                expected="a timeout",
                actual="none stated",
            )


@dataclass(frozen=True, slots=True)
class DeadLetterOnConsumers:
    meta = RuleMeta(
        "configuration.dead-letter",
        1,
        "Dead-letter queues on consumers",
        "dead_letter is stated only where messages are consumed.",
        Category.CONFIGURATION,
        Severity.LOW,
        profiles=ALL_PROFILES,
    )

    def evaluate(self, context: ValidationContext, parameters: Mapping[str, Any]) -> Outcome:
        return Outcome(
            tuple(
                finding(
                    self.meta,
                    "dead_letter_not_consumer",
                    title=f"{c.name or c.id} has a dead-letter setting but consumes nothing",
                    explanation=(
                        f"dead_letter is set on a {c.kind} connection; only a consumer has messages "
                        "that can keep failing and be set aside."
                    ),
                    remediation="Move the setting to the connection that consumes the messages.",
                    entity_ids=[c.id],
                    field_paths=["configuration.dead_letter", "kind"],
                    expected="a consume connection",
                    actual=str(c.kind),
                )
                for c in context.ir.connections
                if c.configuration.get("dead_letter") is True and c.kind is not ConnectionKind.CONSUME
            )
        )


RULES = (
    ReplicasWithinAutoscaling(),
    ZonesMatchMultiAz(),
    BackupsRetained(),
    RetriesNeedTimeouts(),
    DeadLetterOnConsumers(),
)
