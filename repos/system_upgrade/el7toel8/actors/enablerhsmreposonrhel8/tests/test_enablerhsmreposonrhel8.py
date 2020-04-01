from collections import namedtuple
import os
import sys

import pytest

from leapp.exceptions import StopActorExecutionError
from leapp.libraries.actor import library
from leapp.libraries.common import config, mounting, rhsm
from leapp.libraries.common.config import architecture
from leapp.libraries.stdlib import CalledProcessError, api
from leapp.models import UsedTargetRepositories, UsedTargetRepository, EnvVar


def not_isolated_actions(raise_err=False):
    commands_called = []

    class MockNotIsolatedActions(object):
        def __init__(self, base_dir=None):
            pass

        def call(self, cmd, **kwargs):
            commands_called.append((cmd, kwargs))
            if raise_err:
                raise_call_error()

    return (commands_called, MockNotIsolatedActions)


def raise_call_error(args=None):
    raise CalledProcessError(
        message='A Leapp Command Error occured.',
        command=args,
        result={'signal': None, 'exit_code': 1, 'pid': 0, 'stdout': 'fake', 'stderr': 'fake'}
    )


class run_mocked(object):
    def __init__(self, raise_err=False):
        self.called = 0
        self.args = []
        self.raise_err = raise_err

    def __call__(self, *args):
        self.called += 1
        self.args.append(args)
        if self.raise_err:
            raise_call_error(args)


class logger_mocked(object):
    def __init__(self):
        self.warnmsg = None
        self.dbgmsg = None
        self.infomsg = None

    def info(self, *args):
        self.infomsg = args

    def warning(self, *args):
        self.warnmsg = args

    def debug(self, *args):
        self.dbgmsg = args

    def __call__(self):
        return self


# TODO: this is kinda overpowered mock, but we plan to provide something like
# that in the testutils library in future, so using as it is.
class CurrentActorMocked(object):
    def __init__(self, kernel='3.10.0-957.43.1.el7.x86_64', release_id='rhel',
                 src_ver='7.6', dst_ver='8.1', arch=architecture.ARCH_X86_64,
                 envars=None):

        if envars:
            envarsList = [EnvVar(name=key, value=value) for key, value in envars.items()]
        else:
            envarsList = []

        version = namedtuple('Version', ['source', 'target'])(src_ver, dst_ver)
        os_release = namedtuple('OS_release', ['release_id', 'version_id'])(release_id, src_ver)
        args = (version, kernel, os_release, arch, envarsList)
        conf_fields = ['version', 'kernel', 'os_release', 'architecture', 'leapp_env_vars']
        self.configuration = namedtuple('configuration', conf_fields)(*args)
        self._common_folder = '../../files'
        self.log = logger_mocked()

    def __call__(self):
        return self

    def get_common_folder_path(self, folder):
        return os.path.join(self._common_folder, folder)


def test_setrelease(monkeypatch):
    commands_called, klass = not_isolated_actions()
    monkeypatch.setattr(mounting, 'NotIsolatedActions', klass)
    monkeypatch.setattr(api, 'current_actor', CurrentActorMocked(dst_ver='8.0'))
    monkeypatch.setattr(config, 'get_product_type', lambda dummy: 'ga')
    library.set_rhsm_release()
    assert commands_called and len(commands_called) == 1
    assert commands_called[0][0][-1] == '8.0'


def test_setrelease_submgr_throwing_error(monkeypatch):
    _, klass = not_isolated_actions(raise_err=True)
    monkeypatch.setattr(mounting, 'NotIsolatedActions', klass)
    monkeypatch.setattr(api, 'current_actor', CurrentActorMocked(dst_ver='8.0', envars={'LEAPP_NO_RHSM': '0'}))
    monkeypatch.setattr(config, 'get_product_type', lambda dummy: 'ga')
    # free the set_release funtion from the @_rhsm_retry decorator which would otherwise cause 25 sec delay of the test
    if sys.version_info.major < 3:
        monkeypatch.setattr(rhsm, 'set_release',
                            rhsm.set_release.func_closure[0].cell_contents.func_closure[0].cell_contents)
    else:
        monkeypatch.setattr(rhsm, 'set_release', rhsm.set_release.__wrapped__.__wrapped__)
    with pytest.raises(StopActorExecutionError):
        library.set_rhsm_release()


@pytest.mark.parametrize('product', ['beta', 'htb'])
def test_setrelease_skip_rhsm(monkeypatch, product):
    commands_called, _ = not_isolated_actions()
    monkeypatch.setattr(api, 'current_actor', CurrentActorMocked(envars={'LEAPP_NO_RHSM': '1'}))
    monkeypatch.setattr(config, 'get_product_type', lambda dummy: product)
    # To make this work we need to re-apply the decorator, so it respects the environment variable
    monkeypatch.setattr(rhsm, 'set_release', rhsm.with_rhsm(rhsm.set_release))
    library.set_rhsm_release()
    assert not commands_called


def construct_UTRepo_consume(repoids):
    repos = [UsedTargetRepository(repoid=repoid) for repoid in repoids]
    return lambda *x: (x for x in (UsedTargetRepositories(repos=repos),))


def test_get_unique_repoids(monkeypatch):
    repoids = (['some-repo', 'some-repo', 'another-repo'])
    monkeypatch.setattr(api, 'consume', construct_UTRepo_consume(repoids))
    assert library.get_repos_to_enable() == {'some-repo', 'another-repo'}


def test_get_submgr_cmd():
    assert library.get_submgr_cmd({'some-repo'}) == ['subscription-manager', 'repos', '--enable', 'some-repo']


def test_running_submgr_ok(monkeypatch):
    monkeypatch.setattr(api, 'current_actor', CurrentActorMocked(dst_ver='8.0', envars={'LEAPP_NO_RHSM': '0'}))
    monkeypatch.setattr(library, 'get_repos_to_enable', lambda: {'some-repo'})
    monkeypatch.setattr(library, 'run', run_mocked())
    library.enable_rhsm_repos()
    assert library.run.called
    assert 'subscription-manager' in library.run.args[0][0]


def test_running_submgr_fail(monkeypatch):
    monkeypatch.setattr(api, 'current_actor', CurrentActorMocked(dst_ver='8.0', envars={'LEAPP_NO_RHSM': '0'}))
    monkeypatch.setattr(library, 'get_repos_to_enable', lambda: {'some-repo'})
    monkeypatch.setattr(library, 'run', run_mocked(raise_err=True))
    monkeypatch.setattr(api, 'current_logger', logger_mocked())
    library.enable_rhsm_repos()
    assert library.run.called
    assert api.current_logger.warnmsg


def test_enable_repos_skip_rhsm(monkeypatch):
    monkeypatch.setattr(api, 'current_actor', CurrentActorMocked(envars={'LEAPP_NO_RHSM': '1'}))
    monkeypatch.setattr(library, 'run', run_mocked())
    monkeypatch.setattr(api, 'current_logger', logger_mocked())
    library.enable_rhsm_repos()
    assert not library.run.called
    assert api.current_logger.dbgmsg
