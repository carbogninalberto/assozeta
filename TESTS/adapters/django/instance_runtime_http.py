"""Parent-only Phase 1 runtime switch HTTP runner (Django == Go); imports are inert.

Reuses the backup actions runner verbatim: its Unix-socket runner stub, the
per-case owner/administrator password hashes, the shared password-attempt
cache checks and the audit effect check (the authored UPDATE entry on the
instance configuration and its index row, nothing else changed). Production
mode with ASSOZETA_UPDATER_DIRECTORY on the stub directory is set for Django
(override_settings) and Go (GO_FIXTURE_ENVIRONMENT).
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import types

from TESTS.contracts import test_instance_runtime_http as base_contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django import instance_backups_actions_http as actions

SOURCES = ('TESTS/fixtures/instance_runtime_http.json', 'TESTS/contracts/test_instance_runtime_http.py',
           'TESTS/adapters/django/instance_runtime_http.py', 'TESTS/adapters/django/instance_backups_actions_http.py',
           'TESTS/contracts/test_instance_backups_actions_http.py', 'BE/instance/backups.py', 'BE/instance/updater_client.py',
           'BE/instance/permissions.py', 'BE/instance/models.py', 'BE/instance/urls.py', 'BE/core/settings.py') + harness.SOURCES
require = base_contract.require


@contextmanager
def service_context(report, log):
    from django.conf.global_settings import PASSWORD_HASHERS
    from django.test.utils import override_settings
    source = base_contract.load_source()
    saved = os.environ.get('GO_FIXTURE_ENVIRONMENT')
    directory = Path(tempfile.mkdtemp(prefix='azrt-', dir='/tmp'))
    stub = actions.RunnerStub(directory)
    try:
        (directory / 'token').write_text(source['runner_token'] + '\n')
        actions.STATE.update(stub=stub, directory=directory)
        try:
            with override_settings(ASSOZETA_DEPLOYMENT_MODE='production', ASSOZETA_UPDATER_DIRECTORY=str(directory),
                                   PASSWORD_HASHERS=PASSWORD_HASHERS):
                os.environ['GO_FIXTURE_ENVIRONMENT'] = json.dumps({
                    **json.loads(saved or '{}'), 'ASSOZETA_DEPLOYMENT_MODE': 'production',
                    'ASSOZETA_UPDATER_DIRECTORY': str(directory)})
                yield
        finally:
            if saved is None:
                os.environ.pop('GO_FIXTURE_ENVIRONMENT', None)
            else:
                os.environ['GO_FIXTURE_ENVIRONMENT'] = saved
            actions.STATE.clear()
    finally:
        stub.close()
        shutil.rmtree(directory, ignore_errors=True)


def seed_rows(source, created):
    from instance.models import InstanceConfiguration
    spec = dict(source['instance'])
    require(not InstanceConfiguration._base_manager.exists(), 'fixture.instance-exists')
    spec['primary_association_id'] = source['ids'][spec.pop('primary_association')]
    InstanceConfiguration._base_manager.bulk_create([InstanceConfiguration(**spec)])
    created.append((InstanceConfiguration, spec['id']))


def _assert_case(case, base_url, *, token, sessions):
    from django_redis import get_redis_connection
    source = base_contract.load_source()
    raw = get_redis_connection('default')
    preset = actions.apply_cache(case, raw)
    try:
        with actions.passwords(case, source), actions.runner_state(case, source):
            status = base_contract.assert_case(case, base_url, token=token, sessions=sessions)
    finally:
        actions.check_cache(case, raw, preset)
    return status


contract = types.SimpleNamespace(load_source=base_contract.load_source, require=base_contract.require, assert_case=_assert_case,
                                 ResponseFailure=base_contract.ResponseFailure)
fixture_context = harness.fixture_context_for(contract, seed_rows)
run_workload = harness.run_workload_for(contract, actions.assert_effect, prepare=lambda source: {'started': time.time()})
