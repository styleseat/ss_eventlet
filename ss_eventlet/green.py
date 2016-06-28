from __future__ import absolute_import

import functools
import importlib
import sys
from contextlib import contextmanager

import eventlet
from eventlet.support import six

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


_module_dependency_cache = {}

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
    # during the patching process.
    saver = eventlet.patcher.SysModulesSaver()
    try:
        original_modules = set(sys.modules)
        original_modules.add(module_name)
        saver.save(*original_modules)
        try:
            dependency_cache = _module_dependency_cache[module_name]
        except KeyError:
            dependency_cache = None
            # Determine which modules, if any, will be added to sys.modules while
            # patching the target module, and freeze their state. Avoid freezing
            # all of sys.modules, as eventlet caches patched modules in
            # sys.modules.
            utils.delete_sys_modules(
                list(utils.iter_descendent_module_names(root_module)))
            pre_import_modules = set(sys.modules)
            importlib.import_module(module_name)
            new_modules = set(sys.modules) - pre_import_modules
            utils.delete_sys_modules(new_modules)
            saver.save(*(new_modules - original_modules))
            dependencies = set(
                x for x in new_modules if
                x.startswith(root_module) and x not in ancestors)
        else:
            saver.save(*dependency_cache.keys())

        if dependency_cache is None:
            utils.delete_sys_modules(
                list(utils.iter_descendent_module_names(root_module)))
        else:
            utils.delete_sys_modules(
                list(utils.iter_descendent_module_names(module_name)))
            for name, module in six.iteritems(dependency_cache):
                sys.modules[name] = module
        # Patch the target module and all of its ancestors, rather than just
        # the target module, because import_patched caches patched modules, so
        # subsequent calls to this function must restore the cached module's
        # ancestry tree to avoid creating a rootless module.
        for name in ancestors:
            sys.modules[name] = eventlet.import_patched(name)
        if dependency_cache is None:
            _module_dependency_cache[module_name] = {
                name: sys.modules[name] for name in dependencies}
        yield sys.modules[module_name]
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
