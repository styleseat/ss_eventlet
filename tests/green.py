from __future__ import absolute_import

import abc
import collections
import functools
import importlib
import inspect
import math
import operator
import sys
import time
from multiprocessing import Process, Queue

import mock
import pytest
from eventlet.support import six

from ss_eventlet import Timeout, green, utils

try:
    # python 2
    import Queue as queue
except ImportError:
    # python 3
    import queue

try:
    # python 2
    from BaseHTTPServer import (
        BaseHTTPRequestHandler, HTTPServer as _HTTPServer)
except ImportError:
    # python 3
    from http.server import BaseHTTPRequestHandler, HTTPServer as _HTTPServer


HTTPResponse = collections.namedtuple(
    'HTTPResponse', ('status_code', 'headers', 'content'))


class HTTPServerStartupTimeout(Exception):
    pass


class HTTPServer(_HTTPServer):
    allow_reuse_address = False

    def handle_error(self, request, client_address):
        pass


class SlowRequestHandler(BaseHTTPRequestHandler):
    OUTPUT_RATE = 0.1

    def do_GET(self):
        nbytes = int(math.ceil(
            float(self.server.min_response_time)/self.OUTPUT_RATE))
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.send_header('Content-Length', str(nbytes))
        self.end_headers()
        for _ in range(nbytes):
            time.sleep(self.OUTPUT_RATE)
            self.wfile.write(b'1')
            self.wfile.flush()

    def log_message(self, fmt, *args):
        pass


class HTTPServerProcess(Process):
    DEFAULT_PROCESS_TIMEOUT = 5.0

    def __init__(self, min_response_time, **kwargs):
        super(HTTPServerProcess, self).__init__()
        self.min_response_time = min_response_time
        self.max_request_wait = kwargs.get(
            'max_request_wait', self.DEFAULT_PROCESS_TIMEOUT)

    def run(self):
        server = HTTPServer(('', 0), SlowRequestHandler)
        server.min_response_time = self.min_response_time
        server.timeout = self.max_request_wait
        self.queue.put(server.server_port)
        server.handle_request()

    def start(self, timeout=NotImplemented):
        if timeout is NotImplemented:
            timeout = self.DEFAULT_PROCESS_TIMEOUT
        if hasattr(self, 'process'):
            raise RuntimeError('The process has already been started')
        self.queue = Queue()
        super(HTTPServerProcess, self).start()
        try:
            self.port = self.queue.get(timeout)
        except queue.Empty:
            raise HTTPServerStartupTimeout()

    def stop(self, timeout=NotImplemented):
        if timeout is NotImplemented:
            timeout = self.DEFAULT_PROCESS_TIMEOUT
        self.join(timeout)
        if self.is_alive():
            self.terminate()

    def __enter__(self):
        self.start()
        return 'http://localhost:{}/'.format(self.port)

    def __exit__(self, exc_type, value, traceback):
        self.terminate()


class HTTPModuleTests(object):
    __metaclass__ = abc.ABCMeta

    @pytest.fixture
    def eventlet_requestor(self):
        raise NotImplementedError()

    @pytest.fixture
    def standard_requestor(self):
        raise NotImplementedError()

    @pytest.fixture
    def http_server_factory(self, request_timeout):
        return functools.partial(HTTPServerProcess, request_timeout + 0.1)

    @pytest.mark.parametrize('request_timeout', [0, 0.1])
    def test_eventlet_requests_honor_timeout(
            self, http_server_factory, eventlet_requestor, request_timeout):
        with http_server_factory() as server_root,\
                pytest.raises(Timeout),\
                Timeout(request_timeout):
            eventlet_requestor(server_root)

    @pytest.mark.parametrize('request_timeout', [0])
    def test_standard_requests_ignore_timeout(
            self, http_server_factory, standard_requestor, request_timeout):
        with http_server_factory() as server_root,\
                Timeout(request_timeout):
            response = standard_requestor(server_root)
            assert response.status_code == 200


@pytest.yield_fixture
def restore_sys_modules():
    with mock.patch.dict(sys.modules):
        yield


class GreenModuleSandboxingTests(object):
    __metaclass__ = abc.ABCMeta

    @pytest.fixture
    def module_to_green(self):
        raise NotImplementedError()

    def test_module_sandboxing(self, restore_sys_modules, module_to_green):
        ModuleInfo = collections.namedtuple(
            'ModuleInfo', ('names', 'required'))
        core_modules = tuple(ModuleInfo(*i) for i in (
            ('os', True),
            (('Queue', 'queue'), True),
            ('select', True),
            ('selectors', False),
            ('socket', True),
            ('ssl', False),
            ('subprocess', True),
            ('time', True),
            (('thread', '_thread'), True),
            ('threading', True),
        ))
        affected_modules = set()
        for info in core_modules:
            names = info.names
            if isinstance(names, six.string_types):
                names = [names]
            for name in names:
                try:
                    importlib.import_module(name)
                except ImportError:
                    pass
                else:
                    affected_modules.add(name)
                    break
            else:
                if info.required:
                    raise RuntimeError(
                        'Unable to locate any module matching names: %s' %
                        ', '.join(names))
        root_module = module_to_green.split('.', 1)[0]
        descendent_modules = set(
            utils.iter_descendent_module_names(root_module))
        affected_modules.update(
            m for m in descendent_modules if sys.modules[m] is not None)
        affected_modules = list(affected_modules)
        # Eventlet's patcher won't patch a module if a patched version
        # already exists in sys.modules, so all patched modules must be
        # deregistered in order to test the normal patching behavior.
        patched_modules = set(
            m for m in sys.modules if m.startswith('__patched_module_'))
        utils.delete_sys_modules(
            descendent_modules |
            set(affected_modules) |
            set(utils.iter_descendent_module_names('eventlet')) |
            set(utils.iter_descendent_module_names('greenlet')) |
            set([green.__name__]) |
            patched_modules
        )
        real_modules = [
            importlib.import_module(m) for m in affected_modules]
        refs_by_module_name = {}
        for module in real_modules:
            module_refs = refs_by_module_name[module.__name__] = {}
            for attr, value in inspect.getmembers(module):
                if value in real_modules:
                    module_refs[attr] = value
        importlib.import_module(green.__name__)
        current_modules = [
            importlib.import_module(m) for m in affected_modules]
        assert [id(m) for m in current_modules] == \
               [id(m) for m in real_modules]
        for module_name, refs in six.iteritems(refs_by_module_name):
            for attr, expected_value in six.iteritems(refs):
                actual_value = getattr(sys.modules[module_name], attr)
                assert id(expected_value) == id(actual_value), \
                    '%s.%s changed after import' % (module_name, attr)


class TestRequestsModule(HTTPModuleTests, GreenModuleSandboxingTests):
    @pytest.fixture
    def module_to_green(self):
        return 'requests'

    @classmethod
    @pytest.fixture(scope='class', autouse=True)
    def requests(cls):
        cls.requests_module = pytest.importorskip('requests')

    @pytest.fixture
    def eventlet_requestor(self):
        return self.make_requestor(green.requests)

    @pytest.fixture
    def standard_requestor(self):
        return self.make_requestor(self.requests_module)

    def make_requestor(self, module):
        def requestor(url):
            response = module.get(url)
            return HTTPResponse(
                response.status_code, response.headers, response.content)

        return requestor


class TestHttplibModule(HTTPModuleTests, GreenModuleSandboxingTests):
    @pytest.fixture
    def module_to_green(self):
        try:
            importlib.import_module('http.client')
        except ImportError:
            return 'httplib'
        else:
            return 'http.client'

    @pytest.fixture
    def eventlet_requestor(self):
        return self.make_requestor(green.httplib)

    @pytest.fixture
    def standard_requestor(self, module_to_green):
        return self.make_requestor(importlib.import_module(module_to_green))

    def make_requestor(self, module):
        def requestor(url):
            parsed_url = six.moves.urllib.parse.urlparse(url)
            conn = module.HTTPConnection(parsed_url.hostname, parsed_url.port)
            conn.request('GET', url)
            try:
                response = conn.getresponse()
                content = response.read()
            finally:
                conn.close()
            return HTTPResponse(
                response.status, response.getheaders(), content)

        return requestor


class TestHttplib2Module(HTTPModuleTests, GreenModuleSandboxingTests):
    @pytest.fixture
    def module_to_green(self):
        return 'httplib2'

    @classmethod
    @pytest.fixture(scope='class', autouse=True)
    def httplib2(cls):
        cls.httplib2_module = pytest.importorskip('httplib2')

    @pytest.fixture
    def eventlet_requestor(self):
        return self.make_requestor(green.httplib2)

    @pytest.fixture
    def standard_requestor(self):
        return self.make_requestor(self.httplib2_module)

    def make_requestor(self, module):
        def requestor(url):
            response, content = module.Http().request(url)
            return HTTPResponse(response.status, content, response)

        return requestor


def get_patched_module(name):
    return sys.modules.get('_'.join(('__patched_module', name)))


class PatchPackageModuleValidationContext(object):
    def __init__(
            self, package_module_name, child_name,
            package_exports_child, child_imports_package):
        self.package_module_name = package_module_name
        self.child_module_name = '.'.join((package_module_name, child_name))
        self.package_name = package_module_name.rsplit('.', 1)[-1]
        self.child_name = child_name
        self.package_exports_child = package_exports_child
        self.child_imports_package = child_imports_package

    @classmethod
    def predicate(cls, equals):
        return operator.eq if equals else operator.ne

    def assert_sys_modules_cmp(self, should_match, package, child):
        pred = self.predicate(should_match)
        sys_package = sys.modules.get(self.package_module_name)
        assert pred(id(sys_package), id(package))
        sys_child = sys.modules.get(self.child_module_name)
        assert pred(id(sys_child), id(child))

    def assert_internal_members_cmp(self, should_match, package, child):
        pred = self.predicate(should_match)
        if package is not None:
            try:
                assert pred(id(getattr(package, self.child_name)), id(child))
            except AttributeError:
                if self.package_exports_child:
                    raise
        if child is not None:
            try:
                assert pred(id(getattr(child, self.package_name)), id(package))
            except AttributeError:
                if self.child_imports_package:
                    raise

    def validate_package(self, should_match_sys_modules, package, child):
        self.assert_sys_modules_cmp(should_match_sys_modules, package, child)
        self.assert_internal_members_cmp(True, package, child)

    def validate_patch(self, patched_module_name, patched_module):
        assert id(patched_module) == id(sys.modules[patched_module_name])
        patched_package = get_patched_module(self.package_module_name)
        patched_child = get_patched_module(self.child_module_name)
        assert patched_package is not None
        if patched_module_name == self.child_module_name:
            assert patched_child is not None
            self.validate_package(True, patched_package, patched_child)
        else:
            assert patched_child is None
            self.assert_sys_modules_cmp(True, patched_package, None)
            self.assert_internal_members_cmp(
                False, patched_package, self.child)
            if self.package_exports_child and self.child_imports_package:
                assert (
                    id(getattr(
                        getattr(patched_package, self.child_name),
                        self.package_module_name)) ==
                    id(patched_package))
        if self.package is not None:
            self.validate_package(False, self.package, self.child)

    def __enter__(self):
        if hasattr(self, 'package'):
            raise RuntimeError('Cannot re-enter an active validation context')
        self.package = sys.modules.get(self.package_module_name)
        self.child = sys.modules.get(self.child_module_name)

    def __exit__(self, exc_type, value, traceback):
        try:
            if exc_type is not None:
                self.validate_package(True, self.package, self.child)
        finally:
            del self.package
            del self.child


class TestPatchPackageModule(object):
    @staticmethod
    @pytest.yield_fixture(scope='class', autouse=True)
    def auto_add_test_modules():
        sys.path.append('test_modules')
        yield
        sys.path.remove('test_modules')

    @staticmethod
    @pytest.fixture(autouse=True)
    def auto_restore_sys_modules(restore_sys_modules):
        pass

    @staticmethod
    @pytest.fixture
    def validation_context_builder():
        return PatchPackageModuleValidationContext

    @staticmethod
    @pytest.fixture
    def validation_context(
            validation_context_builder, package_module_name, child_name,
            package_exports_child, child_imports_package):
        return validation_context_builder(
            package_module_name, child_name,
            package_exports_child, child_imports_package)

    @staticmethod
    @pytest.fixture
    def child_name():
        return 'child'

    @pytest.mark.parametrize(
        'package_module_name, package_exports_child, child_imports_package',
        [
            ('cyclical_pkg', True, True),
            ('child_lifting_pkg', True, False),
            ('parent_referencing_module', False, True),
        ])
    @pytest.mark.parametrize('patch_child', [False, True])
    @pytest.mark.parametrize('repeat_patch', [False, True])
    def test_previously_imported_package(
            self, validation_context, patch_child, repeat_patch):
        assert validation_context.package_module_name not in sys.modules,\
            'Test package must be unimported'
        importlib.import_module(validation_context.package_module_name)
        importlib.import_module(validation_context.child_module_name)
        for _ in range(1 + int(repeat_patch)):
            with validation_context:
                self.parent_child_test(validation_context, patch_child)

    @pytest.mark.parametrize(
        'package_module_name, package_exports_child, child_imports_package',
        [
            ('cyclical_pkg', True, True),
            ('child_lifting_pkg', True, False),
            ('parent_referencing_module', False, True),
        ])
    @pytest.mark.parametrize('patch_child', [False, True])
    @pytest.mark.parametrize('repeat_patch', [False, True])
    def test_unimported_package(
            self, validation_context, patch_child, repeat_patch):
        for _ in range(1 + int(repeat_patch)):
            with validation_context:
                self.parent_child_test(validation_context, patch_child)

    def parent_child_test(self, validation_context, patch_child):
        module_name = (
            validation_context.child_module_name if patch_child else
            validation_context.package_module_name)
        with green._patch_package_module(module_name, []) as patched_module:
            validation_context.validate_patch(module_name, patched_module)

    @pytest.mark.parametrize(
        'package_module_name, package_exports_child, child_imports_package',
        [
            ('cyclical_pkg', True, True),
        ])
    def test_nesting_same_root(
            self, validation_context_builder, validation_context):
        nested_validation_context = validation_context_builder(
            **{k: getattr(validation_context, k) for k in (
                'package_module_name',
                'child_name',
                'package_exports_child',
                'child_imports_package')})
        module_name = validation_context.child_module_name
        with validation_context:
            with green._patch_package_module(module_name) as patched_module:
                with nested_validation_context, pytest.raises(RuntimeError),\
                        green._patch_package_module(
                            nested_validation_context.package_module_name):
                    pass
                validation_context.validate_patch(module_name, patched_module)

    @pytest.mark.parametrize(
        'package_module_name, package_exports_child, child_imports_package',
        [
            ('cyclical_pkg', True, True),
        ])
    def test_nesting_different_root(
            self, validation_context_builder, validation_context, child_name):
        nested_validation_context = validation_context_builder(
            package_module_name='parent_referencing_module',
            child_name=child_name,
            package_exports_child=False,
            child_imports_package=True)
        validation_contexts = [validation_context, nested_validation_context]
        module_names = [
            validation_context.child_module_name,
            nested_validation_context.package_module_name]
        with validation_contexts[0]:
            with green._patch_package_module(module_names[0]) as outer:
                with validation_contexts[1], green._patch_package_module(
                        module_names[1]) as inner:
                    validation_contexts[1].validate_patch(
                        module_names[1], inner)
                validation_contexts[0].validate_patch(module_names[0], outer)
