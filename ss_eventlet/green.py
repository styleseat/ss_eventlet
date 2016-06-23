from __future__ import absolute_import

import functools
import importlib
import sys
from contextlib import contextmanager

import eventlet

from . import utils

# If the New Relic agent package is installed, instrument eventlet
# modules. Config file instrumentation isn't suitable, as the execute
# property for import-hook sections requires each identified module to be
# registered in sys.modules, which may conflict with regular, unpatched
# modules, as eventlet-patched modules have __name__, __package__, and
# __file__ attributes identical to their unpatched equivalents.
try:
    importlib.import_module('newrelic')
except ImportError:
    def _nr_instrument(target_module, instrument_name):
        pass
else:
    def _nr_instrument(target_module, instrument_name):
        instrument_parts = instrument_name.split(':', 1)
        if len(instrument_parts) == 1:
            instrument_parts.append('instrument')
        instrument_module = importlib.import_module(instrument_parts[0])
        instrument = getattr(instrument_module, instrument_parts[1])
        instrument(target_module)


_currently_patched_packages = {}


@contextmanager
def _patch_package_module(module_name, additional_modules=None):
    """Patch a module residing in a package and install it in sys.modules.

    Upon exiting the context, the original contents of sys.modules will be
    restored.

    Packages frustrate eventlet's default patching strategy in many ways, some
    of which render it effectively unusable on package modules:
    * Module collisions: When patching the root of a package, all of its
    descendents must be expunged from sys.modules, otherwise relative imports
    raise an ImportError because the existing entries in sys.modules do not
    match the patched package.
    * Orphan modules: The patcher caches patched modules, but not their
    ancestors. If a new patch requires an already-patched module to be
    installed in sys.modules, the patched ancestors must also be installed,
    otherwise the sys.modules entry would be rootless.
    * Child hoisting: When the patcher unloads a child module and imports a
    patched version of it, the import system assigns the patched version
    as a member of its containing package, even if the __init__ module does
    not import the child. The patcher fails to undo package member assignments
    which occur as side-effects of importing patched modules, causing patched
    modules to leak into unpatched modules. In the worst case, exception
    classes from patched modules may be used instead of the unpatched versions,
    such that except statements fail to trap exceptions thrown by the unpatched
    modules.

    To address the deficits in eventlet's patching strategy, unload not only
    the module to be patched, but all currently loaded modules descending from
    the module's root package, then import a patched version of the module,
    thereby ensuring the module has a valid lineage separate from the original
    package.

    Note that creating an internally consistent, patched copy of a package, in
    which every module has been patched and contains only references to other
    patched modules, would be an onerous undertaking. Ensuring consistent
    references to a patched module throughout a package requires registering
    the patched module in sys.modules and loading every one of the package's
    other modules in dependency order. As packages can contain dependency
    cycles between the __init__ module and other modules, in which __init__
    imports other modules at the end of its body to expose certain symbols at
    the package level, package-level symbols would also need to be inspected
    and patched.
    """
    if additional_modules is None:
        additional_modules = []
    ancestors = list(utils.iter_ancestor_module_names(module_name))
    root_module = ancestors[0]
    # Initialize the saver prior to examining sys.modules in order to acquire
    # the import lock, preventing other threads from mutating sys.modules
    # and other shared state during the patching process.
    saver = eventlet.patcher.SysModulesSaver()
    try:
        # The implementation does not support nesting patches of modules in the
        # same root package. Properly supporting such nested patches would
        # require not resetting the entire package state in sys.modules, so
        # that modules containing classes such as exceptions can be shared
        # within the package.
        if root_module in _currently_patched_packages:
            raise RuntimeError(
                'Cannot patch module %s in root package %s'
                ' while module %s is already patched' % (
                    module_name, root_module,
                    _currently_patched_packages[root_module]))
        _currently_patched_packages[root_module] = module_name
        try:
            original_modules = set(sys.modules)
            original_modules.add(module_name)
            saver.save(*original_modules)
            # Determine which modules (apart from the target module) the
            # patching process will add to sys.modules, and freeze their state.
            # Avoid freezing all of sys.modules, as eventlet caches patched
            # modules in sys.modules.
            importlib.import_module(module_name)
            new_modules = set(sys.modules) - original_modules
            utils.delete_sys_modules(new_modules)
            saver.save(*new_modules)

            utils.delete_sys_modules(
                list(utils.iter_descendent_module_names(root_module)))
            # Patch the target module and all of its ancestors, rather than
            # just the target module, because import_patched caches patched
            # modules, so subsequent calls to this function must restore the
            # cached module's ancestry tree to avoid creating a rootless
            # module.
            for name in ancestors:
                sys.modules[name] = eventlet.import_patched(name)
            # Due to patched module caching, patches only import a module's
            # dependencies the first time. Make repeat calls as consistent
            # as possible by deregistering all unpatched modules in the root
            # package from sys.modules.
            utils.delete_sys_modules(
                set(utils.iter_descendent_module_names(root_module)) -
                set(ancestors))
            yield sys.modules[module_name]
        finally:
            del _currently_patched_packages[root_module]
    finally:
        saver.restore()


try:
    importlib.import_module('http.client')
except ImportError:
    @contextmanager
    def _patch_httplib():
        yield importlib.import_module('eventlet.green.httplib')
else:
    """
    Resolve the side effects of using eventlet.green.httplib in python3.
    For example:
    >>> import http.client
    >>> http.client.socket
    <module 'socket' from '/Users/jgaren/.pyenv/versions/3.4.4/lib/python3.4/socket.py'>
    >>> from eventlet.green import httplib
    >>> http.client.socket
    <module 'eventlet.green.socket' from '/Users/jgaren/.pyenv/versions/ss_eventlet/lib/python3.4/site-packages/eventlet/green/socket.py'>
    See: https://github.com/eventlet/eventlet/pull/329/files
    """  # noqa
    _patch_httplib = functools.partial(_patch_package_module, 'http.client')


httplib = _patch_httplib().__enter__()

try:
    importlib.import_module('httplib2')
except ImportError:
    pass
else:
    with _patch_httplib():
        httplib2 = eventlet.import_patched('httplib2')
    _nr_instrument(httplib2, 'newrelic.hooks.external_httplib2')


try:
    importlib.import_module('requests')
except ImportError:
    pass
else:
    requests = _patch_package_module('requests').__enter__()
    _nr_instrument(
        requests.api,
        'newrelic.hooks.external_requests:instrument_requests_api')
    _nr_instrument(
        requests.sessions,
        'newrelic.hooks.external_requests:instrument_requests_sessions')
