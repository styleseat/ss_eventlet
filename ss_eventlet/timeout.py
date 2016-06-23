from __future__ import absolute_import

import eventlet


class Timeout(eventlet.Timeout, Exception):
    """Timeout that inherits from Exception, so as to be catchable.

    The eventlet.Timeout class does not inherit from Exception, breaking
    the convention that user-defined exceptions can be caught with
    `except Exception:`, reserving bare except clauses for low-level
    exceptions like keyboard interrupts and exit syscalls. Many libraries
    depend on the Exception semantics, causing eventlet exceptions to wreak
    havoc on their programming models. Therefore, application code should
    prefer this class to eventlet.Exception.
    """

    pass
