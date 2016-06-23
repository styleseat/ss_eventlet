from __future__ import absolute_import

import functools
import inspect
import logging
import sys

import eventlet
import six

from .timeout import Timeout

logger = logging.getLogger(__name__)


def safe_timeouts(fn):
    """Wrap a callable, casting eventlet.Timeout to an Exception subclass.

    This wrapper provides a tool of last resort, transforming
    `eventlet.Timeout` into `ss_eventlet.timeout.Timeout` to ensure that
    eventlet Timeouts do not propagate outside of run loops and crash
    processes. See `ss_eventlet.timeout.Timeout` for further details.
    """
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except eventlet.Timeout as e:
            if isinstance(e, Exception):
                raise
            logging.error(
                'Caught timeout of class %s.%s deriving from BaseException'
                ' rather than Exception' % (
                    e.__module__, e.__class__.__name__,))
            six.reraise(Timeout, None, sys.exc_info()[2])

    if inspect.isfunction(fn):
        wrapped = functools.wraps(fn)(wrapped)
    return wrapped
