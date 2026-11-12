"""The log file follows the day: a program left running overnight writes
the next day into that day's file (GUI-15)."""

import logging
import time

from micropick.gui import log_bridge


def test_a_record_of_a_new_day_goes_to_that_days_file(tmp_path):
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        today = log_bridge.install_file(tmp_path)
        assert today == log_bridge.day_file(tmp_path, time.time())
        handler = next(h for h in root.handlers if h not in before)
        tomorrow = time.time() + 86_400
        record = logging.LogRecord("micropick", logging.WARNING, __file__, 1,
                                   "a line of the next day", None, None)
        record.created = tomorrow
        handler.handle(record)
        handler.flush()
        later = log_bridge.day_file(tmp_path, tomorrow)
        assert later != today
        assert "a line of the next day" in later.read_text(encoding="utf-8")
        assert "a line of the next day" not in today.read_text(
            encoding="utf-8")
    finally:
        for handler in list(root.handlers):
            if handler not in before:
                root.removeHandler(handler)
                handler.close()
