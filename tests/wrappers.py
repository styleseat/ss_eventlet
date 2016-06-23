from __future__ import absolute_import

import unittest

import eventlet
import mock

from ss_eventlet import Timeout
from ss_eventlet.wrappers import safe_timeouts


class SafeTimeoutsTestCase(unittest.TestCase):
    def test_timeout_inheriting_from_exception(self):
        self.pass_through_exception_test(Timeout())

    def test_timeout_not_inheriting_from_exception(self):
        raiser = self.create_raiser(eventlet.Timeout())
        with self.assertRaises(Exception):
            raiser()

    def test_non_timeout_exception(self):
        self.pass_through_exception_test(ValueError())

    def test_wrap_function(self):
        called = mock.Mock()

        def fn():
            called()

        safe_timeouts(fn)()
        self.assertTrue(called.called)

    def test_wrap_lambda(self):
        called = mock.Mock()
        safe_timeouts(lambda: called())()
        self.assertTrue(called.called)

    def test_wrap_object(self):
        called = mock.Mock()

        class Caller(object):
            def __call__(self):
                called()

        safe_timeouts(Caller())()
        self.assertTrue(called())

    def pass_through_exception_test(self, exception):
        raiser = self.create_raiser(exception)
        try:
            raiser()
        except exception.__class__ as e:
            self.assertIs(exception, e)
        else:
            self.fail('Wrapped function failed to raise expected exception')

    def create_raiser(self, exception):
        def raiser():
            raise exception

        return safe_timeouts(raiser)
