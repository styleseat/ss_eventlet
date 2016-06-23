from __future__ import absolute_import

import unittest

import eventlet

from ss_eventlet import Timeout


class TimeoutTestCase(unittest.TestCase):
    def test_catch_as_eventlet_timeout(self):
        with self.assertRaises(eventlet.Timeout):
            with Timeout(0.0):
                eventlet.sleep()

    def test_catch_as_exception(self):
        with self.assertRaises(Exception):
            with Timeout(0.0):
                eventlet.sleep()
