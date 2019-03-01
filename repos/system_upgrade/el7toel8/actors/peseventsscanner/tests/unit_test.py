import pytest

from leapp.exceptions import StopActorExecutionError
from leapp.libraries.actor import library
from leapp.libraries.actor.library import (Event,
                                           parse_action,
                                           parse_packageset,
                                           parse_entry,
                                           parse_file,
                                           filter_events,
                                           notify_skipped_packages,
                                           filter_by_repositories,
                                           map_repositories,
                                           process_events,
                                           scan_events)
from leapp.libraries.stdlib import api
from leapp.models import InstalledRedHatSignedRPM, RPM, RpmTransactionTasks, RepositoriesSetupTasks


class show_message_mocked(object):
    def __init__(self):
        self.called = 0

    def __call__(self, msg):
        self.called += 1
        self.msg = msg


class produce_mocked(object):
    def __init__(self):
        self.called = 0
        self.model_instances = []

    def __call__(self, *model_instances):
        self.called += 1
        self.model_instances.append(model_instances[0])


def test_parse_action(current_actor_context):
    assert parse_action(0) == 'Present'
    assert parse_action(1) == 'Removed'
    assert parse_action(2) == 'Deprecated'
    assert parse_action(3) == 'Replaced'
    assert parse_action(4) == 'Split'
    assert parse_action(5) == 'Merged'
    assert parse_action(6) == 'Moved'
    assert parse_action(7) == 'Renamed'

    with pytest.raises(ValueError):
        parse_action(-1)
        parse_action(8)


def test_parse_packageset(current_actor_context):
    pkgset = {'package': [{'name': 'pkg1', 'repository': 'Repo'}]}

    assert parse_packageset(pkgset) == {'pkg1': 'repo'}

    assert parse_packageset({}) == {}
    assert parse_packageset({'setid': 0}) == {}


def test_parse_entry(current_actor_context):
    entry = {
        'action': 4,
        'in_packageset': {
            'package': [{'name': 'original', 'repository': 'repo'}]},
        'out_packageset': {
            'package': [
                {'name': 'split01', 'repository': 'repo'},
                {'name': 'split02', 'repository': 'repo'}]}}

    event = parse_entry(entry)
    assert event.action == 'Split'
    assert event.in_pkgs == {'original': 'repo'}
    assert event.out_pkgs == {'split01': 'repo', 'split02': 'repo'}

    entry = {
        'action': 1,
        'in_packageset': {
            'package': [{'name': 'removed', 'repository': 'repo'}]}}

    event = parse_entry(entry)
    assert event.action == 'Removed'
    assert event.in_pkgs == {'removed': 'repo'}
    assert event.out_pkgs == {}


def test_parse_file(current_actor_context):
    events = parse_file('files/tests/sample01.json')
    assert len(events) == 2
    assert events[0].action == 'Split'
    assert events[0].in_pkgs == {'original': 'repo'}
    assert events[0].out_pkgs == {'split01': 'repo', 'split02': 'repo'}
    assert events[1].action == 'Removed'
    assert events[1].in_pkgs == {'removed': 'repo'}
    assert events[1].out_pkgs == {}


def test_filter_events(monkeypatch):
    def consume_message_mocked(*models):
        pkgs = [
            RPM(name='original', epoch='', packager='', version='', release='', arch='', pgpsig='')]
        yield InstalledRedHatSignedRPM(items=pkgs)

    monkeypatch.setattr('leapp.libraries.stdlib.api.consume', consume_message_mocked)
    events = [
        Event('Split', {'original': 'repo'}, {'split01': 'repo', 'split02': 'repo'}),
        Event('Removed', {'removed': 'repo'}, {})]
    filtered = filter_events(events)

    assert len(filtered) == 1
    assert filtered[0].action == 'Split'
    assert filtered[0].in_pkgs == {'original': 'repo'}
    assert filtered[0].out_pkgs == {'split01': 'repo', 'split02': 'repo'}


def test_notify_skipped_packages(monkeypatch):
    monkeypatch.setattr(api, 'show_message', show_message_mocked())
    notify_skipped_packages('packages will not be installed:', ['skipped01', 'skipped02'])

    assert api.show_message.called == 1
    assert api.show_message.msg == '2 packages will not be installed:\n- skipped01\n- skipped02'


def test_filter_by_repositories(monkeypatch):
    monkeypatch.setattr(api, 'show_message', show_message_mocked())
    monkeypatch.setattr(library, 'REPOSITORIES_BLACKLIST', ['blacklisted'])

    to_install = {
        'pkg01': 'repo01',
        'pkg02': 'repo02',
        'skipped01': 'blacklisted',
        'skipped02': 'blacklisted'}
    filter_by_repositories(to_install)

    msgs = [
        '2 packages will not be installed due to blacklisted repositories:',
        '- skipped01',
        '- skipped02']
    assert api.show_message.called == 1
    assert api.show_message.msg == '\n'.join(msgs)

    assert to_install == {'pkg01': 'repo01', 'pkg02': 'repo02'}


def test_map_repositories(monkeypatch):
    monkeypatch.setattr(api, 'show_message', show_message_mocked())
    monkeypatch.setattr(library, 'REPOSITORIES_MAPPING', {'repo': 'mapped'})

    to_install = {
        'pkg01': 'repo',
        'pkg02': 'repo',
        'skipped01': 'not_mapped',
        'skipped02': 'not_mapped'}
    map_repositories(to_install)

    msgs = [
        '2 packages will not be installed due to not mapped repositories:',
        '- skipped01',
        '- skipped02']
    assert api.show_message.called == 1
    assert api.show_message.msg == '\n'.join(msgs)

    assert to_install == {'pkg01': 'mapped', 'pkg02': 'mapped'}


def test_process_events(monkeypatch):
    monkeypatch.setattr('leapp.libraries.stdlib.api.produce', produce_mocked())
    monkeypatch.setattr(library, 'REPOSITORIES_MAPPING', {'repo': 'mapped'})

    events = [
        Event('Split', {'original': 'repo'}, {'split01': 'repo', 'split02': 'repo'}),
        Event('Removed', {'removed': 'repo'}, {})]
    process_events(events)

    assert api.produce.called == 2
    assert len(api.produce.model_instances) == 2
    assert isinstance(api.produce.model_instances[0], RpmTransactionTasks)
    assert len(api.produce.model_instances[0].to_install) == 2
    assert api.produce.model_instances[0].to_install == ['split02', 'split01']
    assert len(api.produce.model_instances[0].to_remove) == 2
    assert api.produce.model_instances[0].to_remove == ['removed', 'original']
    assert isinstance(api.produce.model_instances[1], RepositoriesSetupTasks)
    assert api.produce.model_instances[1].to_enable == ['mapped']


def test_scan_events(monkeypatch):
    def consume_message_mocked(*models):
        pkgs = [
            RPM(name='original', epoch='', packager='', version='', release='', arch='', pgpsig=''),
            RPM(name='removed', epoch='', packager='', version='', release='', arch='', pgpsig='')]
        yield InstalledRedHatSignedRPM(items=pkgs)

    monkeypatch.setattr('leapp.libraries.stdlib.api.consume', consume_message_mocked)
    monkeypatch.setattr('leapp.libraries.stdlib.api.produce', produce_mocked())
    monkeypatch.setattr(library, 'REPOSITORIES_MAPPING', {'repo': 'mapped'})

    scan_events('files/tests/sample01.json')

    assert api.produce.called == 2
    assert len(api.produce.model_instances) == 2
    assert isinstance(api.produce.model_instances[0], RpmTransactionTasks)
    assert len(api.produce.model_instances[0].to_install) == 2
    assert api.produce.model_instances[0].to_install == ['split02', 'split01']
    assert len(api.produce.model_instances[0].to_remove) == 2
    assert api.produce.model_instances[0].to_remove == ['removed', 'original']
    assert isinstance(api.produce.model_instances[1], RepositoriesSetupTasks)
    assert api.produce.model_instances[1].to_enable == ['mapped']

    with pytest.raises(StopActorExecutionError):
        scan_events('files/tests/sample02.json')
