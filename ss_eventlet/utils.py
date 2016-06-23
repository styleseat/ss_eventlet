from __future__ import absolute_import

import sys

from eventlet.support import six


def iter_descendent_module_names(package):
    """Iterate over the modules descending from package, including package.

    Only considers modules contained in sys.modules.
    """
    prefix = package + '.'
    for name, module in six.iteritems(sys.modules):
        if name == package or name.startswith(prefix):
            yield name


def iter_ancestor_module_names(module_name):
    """Iterate over the given module's ancestors, starting at the root."""
    if not module_name:
        raise ValueError('Invalid module name: %s' % (module_name,))
    ancestor = None
    for name in module_name.split('.'):
        if ancestor is None:
            ancestor = name
        else:
            ancestor = '.'.join((ancestor, name))
        yield ancestor


def delete_sys_modules(names):
    """Remove all of the given modules from sys.modules, if present."""
    for name in names:
        sys.modules.pop(name, None)
