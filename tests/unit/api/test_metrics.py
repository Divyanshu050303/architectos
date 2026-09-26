"""Metrics as structured log events: identifiers only, never user text."""

import json
import logging

import pytest

from apps.api.logging_config import JsonFormatter
from apps.api.metrics import LogMetrics
from core.domain.metrics import NullMetrics, check_labels


def test_log_metrics_emit_one_json_event(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="architectos.metrics"):
        LogMetrics().increment(
            "requirements.llm_failure", source="anthropic/claude-sonnet-5", reason="llm_timeout"
        )
        LogMetrics().observe("requirements.analyze.duration_ms", 12.5)
    counter, observation = (json.loads(JsonFormatter().format(r)) for r in caplog.records)
    assert counter["logger"] == "architectos.metrics"
    assert (counter["metric"], counter["metric_kind"], counter["value"]) == (
        "requirements.llm_failure",
        "counter",
        1,
    )
    assert counter["labels"] == {"source": "anthropic/claude-sonnet-5", "reason": "llm_timeout"}
    assert (observation["metric_kind"], observation["value"]) == ("observation", 12.5)


@pytest.mark.parametrize(
    "value",
    [
        "Support at least 2,000 rps",  # requirement text
        "ignore previous instructions",
        "",
        "a" * 65,
        "UPPER",
        "new\nline",
        "<script>",
    ],
)
def test_labels_that_are_not_short_identifiers_are_refused(value: str) -> None:
    with pytest.raises(ValueError, match="short identifier"):
        check_labels({"reason": value})
    with pytest.raises(ValueError, match="short identifier"):
        NullMetrics().increment("requirements.analyze", reason=value)
    with pytest.raises(ValueError, match="short identifier"):
        LogMetrics().observe("requirements.analyze.duration_ms", 1.0, reason=value)
