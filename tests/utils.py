from __future__ import absolute_import

import imp
import inspect

import mock
import pytest

import ss_eventlet.utils
from ss_eventlet.utils import (
    delete_sys_modules, iter_ancestor_module_names,
    iter_descendent_module_names)


@pytest.yield_fixture
def mock_sys():
    with mock.patch.object(ss_eventlet.utils, 'sys') as sys:
        yield sys


@pytest.yield_fixture
def mock_sys_modules(mock_sys, sys_modules):
    mock_sys.modules = sys_modules
    yield sys_modules

dummy_module = imp.new_module('dummy_module')


@pytest.mark.parametrize('package,sys_modules,expected', [
    # empty sys.modules
    ('x', {}, []),
    # module is package
    ('x', {'x': dummy_module}, ['x']),
    # module is a descendant of package
    ('x', {'x.y': dummy_module}, ['x.y']),
    # multiple matches
    ('x', {'x': dummy_module, 'x.y': dummy_module}, ['x', 'x.y']),
    # module name starts with package name minus trailing dot
    ('x', {'xy': dummy_module}, []),
    # module name ends with package name
    ('x', {'a.x': dummy_module}, []),
])
def test_iter_descendent_module_names(mock_sys_modules, package, expected):
    assert sorted(iter_descendent_module_names(package)) == sorted(expected)


@pytest.mark.parametrize('module_name,expected', [
    (None, ValueError),
    ('', ValueError),
    ('x', ['x']),
    ('x.y', ['x', 'x.y']),
    ('x.y.z', ['x', 'x.y', 'x.y.z']),
])
def test_iter_ancestor_module_names(module_name, expected):
    if inspect.isclass(expected):
        with pytest.raises(expected):
            list(iter_ancestor_module_names(module_name))
    else:
        assert list(iter_ancestor_module_names(module_name)) == expected


@pytest.mark.parametrize('to_delete,sys_modules', [
    # module to delete has not been loaded
    (['x'], {'a': None}),
    # module to delete has been loaded
    (['x', 'y'], {'a': None, 'x': None, 'y': None}),
])
def test_delete_sys_modules(mock_sys_modules, to_delete):
    delete_sys_modules(to_delete)
    for module in to_delete:
        assert module not in mock_sys_modules, module
