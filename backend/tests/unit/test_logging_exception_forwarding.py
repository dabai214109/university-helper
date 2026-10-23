"""The loguru -> stdlib bridge must carry the traceback, not just the message.

Every `logger.exception(...)` in the app went through this sink. It forwarded
only `record["message"]`, so production crashes printed a single summary line
with no traceback -- which made "Chapter processing crashed" impossible to
diagnose from the server logs.
"""

import logging
import sys
from types import SimpleNamespace

from app.core.logging_setup import _StdlibSink


def _record(exception):
    return SimpleNamespace(
        record={
            "name": "app.test.sink",
            "level": SimpleNamespace(no=logging.ERROR),
            "message": "chapter crashed",
            "exception": exception,
        }
    )


def _capture():
    captured = []

    class _Handler(logging.Handler):
        def emit(self, record):
            captured.append(record)

    logger = logging.getLogger("app.test.sink")
    logger.handlers = [_Handler()]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return captured


def test_the_sink_forwards_the_exception_tuple():
    try:
        raise ValueError("boom")
    except ValueError:
        exc_type, exc_value, exc_tb = sys.exc_info()

    captured = _capture()
    _StdlibSink()(_record(SimpleNamespace(type=exc_type, value=exc_value, traceback=exc_tb)))

    assert captured, "record must reach the stdlib logger"
    assert captured[0].exc_info is not None, "traceback must not be dropped"
    assert captured[0].exc_info[0] is ValueError
    assert str(captured[0].exc_info[1]) == "boom"


def test_the_sink_still_handles_records_without_an_exception():
    captured = _capture()

    _StdlibSink()(_record(None))

    assert captured and captured[0].exc_info is None
    assert captured[0].getMessage() == "chapter crashed"
