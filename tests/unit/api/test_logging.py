import json
import logging

from apps.api.logging_config import JsonFormatter, configure_logging


def test_records_are_single_line_json_with_extras() -> None:
    record = logging.LogRecord("architectos.request", logging.INFO, __file__, 1, "request", None, None)
    record.route = "/api/v1/me"
    record.status = 200
    record.request_id = "req_test_123456"

    line = JsonFormatter().format(record)

    assert "\n" not in line
    payload = json.loads(line)
    assert payload["message"] == "request"
    assert (payload["route"], payload["status"], payload["request_id"]) == (
        "/api/v1/me",
        200,
        "req_test_123456",
    )
    assert payload["level"] == "INFO"


def test_configuration_disables_the_raw_path_access_log() -> None:
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        configure_logging("INFO")
        assert logging.getLogger("uvicorn.access").disabled
        assert isinstance(root.handlers[0].formatter, JsonFormatter)
    finally:
        root.handlers, root.level = saved_handlers, saved_level
        logging.getLogger("uvicorn.access").disabled = False
