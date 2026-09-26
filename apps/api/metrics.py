"""Metrics as structured log events (logger ``architectos.metrics``), one JSON line each, with the
request id added by the formatter. A metrics exporter can replace this without touching the domain."""

import logging

from core.domain.metrics import check_labels

log = logging.getLogger("architectos.metrics")


class LogMetrics:
    def increment(self, name: str, value: int = 1, **labels: str) -> None:
        check_labels(labels)
        log.info(name, extra={"metric": name, "metric_kind": "counter", "value": value, "labels": labels})

    def observe(self, name: str, value: float, **labels: str) -> None:
        check_labels(labels)
        log.info(name, extra={"metric": name, "metric_kind": "observation", "value": value, "labels": labels})
