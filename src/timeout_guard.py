# -*- coding: utf-8 -*-
"""Timeout guard for stage-level execution.

Provides a context manager that sets a timer on a stage.
If the stage exceeds the timeout, timed_out is set to True.
Python 2.7 compatible.
"""

import threading


class TimeoutGuard(object):
    """Context manager that enforces a timeout on a code block.

    Usage:
        with TimeoutGuard(30, "execution") as guard:
            result = do_something()
        if guard.timed_out:
            handle_timeout()
    """

    def __init__(self, seconds, stage_name="unknown"):
        self.seconds = seconds
        self.stage_name = stage_name
        self.timed_out = False
        self._timer = None

    def __enter__(self):
        if self.seconds and self.seconds > 0:
            self._timer = threading.Timer(self.seconds, self._timeout)
            self._timer.daemon = True
            self._timer.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._timer:
            self._timer.cancel()
            self._timer = None
        # Do not suppress exceptions
        return False

    def _timeout(self):
        self.timed_out = True


__all__ = ["TimeoutGuard"]
