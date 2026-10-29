"""Exercise the race-instrumented Go manual MCP fixture over stdio pipes.

This callable is intended for manual_reader_http.py after its private
PostgreSQL/filesystem fixture is ready. Parent will dispatch with
`--mcp --backend go`:

  if options.mcp and options.backend == 'go':
      from TESTS.adapters.django.manual_mcp_go import measure_go_mcp
      measure_go_mcp(work, fixture, report, log)

and validate it with TESTS.contracts.test_manual_mcp_go.validate_report.
No installed manual, real configuration, customer data or production command
is used.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

from TESTS.adapters.django.manual_tools import fingerprints, prepare
from TESTS.contracts.test_manual_mcp_go import validate_report as validate_portable_report
from TESTS.mcp_client import StdioClient

APPLICATION_NAME = 'manual-mcp-go-contract'
SERVER_VERSION = 'go-manual-fixture'
CANCELLATION_DRAIN_TIMEOUT_SECONDS = 5
CANCELLATION_DRAIN_POLL_SECONDS = .02
SETTINGS_FIELDS = {
    'MANUAL_BUNDLE_PATH': 'BundlePath',
    'MANUAL_BUNDLE_REQUIRED': 'BundleRequired',
    'MANUAL_INDEX_PATH': 'IndexPath',
    'MANUAL_ASSET_ROOT': 'AssetRoot',
    'MANUAL_SOURCE_ROOT': 'SourceRoot',
    'MANUAL_CORPUS_BASE_URL': 'CorpusBaseURL',
    'MANUAL_APPLICATION_REVISION': 'ApplicationRevision',
    'RUNNING_VERSION': 'Release',
    'MANUAL_RUN_ID': 'RunID',
    'MANUAL_DEVELOPMENT_ROOT': 'DevelopmentRoot',
    'ASSOZETA_DEPLOYMENT_MODE': 'DeploymentMode',
}


def measure_go_mcp(work, fixture, report, log):
    from django.db import connection, transaction
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from application.models import SportAssociation, User
    from application.models.audit_models import AuditLogIndex
    from instance.models import InstanceConfiguration
    from TESTS.adapters.django.manual_reader_http import ROOT

    with disable_auditlog():
        owner = User.original_objects.create(
            username='mcp-go-owner',
            email='mcp-go@example.test',
            password='!fixture',
            role=User.ASSOCIATION,
        )
        association = SportAssociation.original_objects.create(
            user=owner,
            denomination='MCP Go Fixture ASD',
        )
        InstanceConfiguration.objects.create(
            domain='mcp-go.example.test',
            name='MCP Go fixture',
            self_hosted=True,
            primary_association=association,
        )

    def snapshot():
        return {
            model._meta.label: list(model._base_manager.order_by('pk').values())
            for model in (User, SportAssociation, InstanceConfiguration, LogEntry, AuditLogIndex)
        }

    base = work / 'mcp-go-source'
    python_settings, corpus = prepare(base, {})
    settings = {SETTINGS_FIELDS[key]: value for key, value in python_settings.items()}
    manifest = work / 'mcp-go-fixture.json'
    manifest.write_text(json.dumps({
        'Settings': settings,
        'AssociationID': str(association.pk),
        'ServerVersion': SERVER_VERSION,
    }))
    binary = work / 'manual-mcp-go'
    tool_env = {
        key: value
        for key, value in os.environ.items()
        if key in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')
    }
    tool_env['LC_ALL'] = 'C'
    tool_env.setdefault('GOCACHE', str(work / 'go-cache'))
    subprocess.run(
        [
            'go', 'test', '-race', '-c', '-tags', 'manualmcpintegration',
            '-o', str(binary), './internal/handler/mcpstdio',
        ],
        cwd=ROOT / 'BE_GO',
        env=tool_env,
        check=True,
        timeout=180,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    report['scope'] = (
        'Race-instrumented Go stdio transport, authored manual schemas, real manual service '
        'and PostgreSQL context over pipes. Exactly the four manual tools; no business-tool '
        'or full-registry parity claim.'
    )
    report['source_hashes'] = {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (
            *sorted((ROOT / 'BE_GO/internal/handler/mcpstdio').glob('*.go')),
            *sorted((ROOT / 'BE_GO/internal/handler/manualmcp').glob('*.go')),
            ROOT / 'BE_GO/internal/service/manual_tools.go',
            ROOT / 'BE_GO/internal/repository/manual_tools.go',
            ROOT / 'BE_GO/internal/domain/manual.go',
            ROOT / 'TESTS/adapters/django/manual_mcp_go.py',
            ROOT / 'TESTS/contracts/test_manual_mcp_go.py',
        )
    }
    process_env = {
        **tool_env,
        'ASSOZETA_MANUAL_MCP_GO_PROCESS': '1',
        'ASSOZETA_MANUAL_MCP_GO_FIXTURE': str(manifest),
        'ASSOZETA_MANUAL_MCP_GO_DSN': (
            f'host={work} port=5432 user=contracts dbname=assozeta_contract_manual_http '
            f'sslmode=disable application_name={APPLICATION_NAME}'
        ),
    }
    report['protocol_runtime'] = {'implementation': 'go', 'race': True}
    report['server_version'] = SERVER_VERSION
    report['initializations'] = {}
    report['session_checks'] = {}

    def sql_since(offset):
        with (work / 'postgres.log').open('rb') as stream:
            stream.seek(offset)
            data = stream.read().decode()
        return re.findall(r'(?:statement:|execute [^:]*:)\s*(.*?)(?=^\d{4}-|\Z)', data, re.M | re.S)

    before = snapshot()
    files = fingerprints(base)
    rpc = StdioClient([str(binary)], process_env, log, ROOT)
    clean_exits = 0
    try:
        protocol = fixture['protocol']
        response = rpc.call('initialize', {
            'protocolVersion': protocol,
            'capabilities': {},
            'clientInfo': {'name': 'portable-go-contract', 'version': '1'},
        }, 'initialize')
        report['initializations'][protocol] = response
        rpc.send('notifications/initialized', {})
        assert snapshot() == before and fingerprints(base) == files, 'MCP startup mutated state'

        listed = rpc.call('tools/list', {}, 'list')
        report['tools'] = listed['result']['tools']
        for name, spec in fixture['cases'].items():
            params = json.loads(json.dumps(spec['params']))
            arguments = params.get('arguments')
            if isinstance(arguments, dict):
                substitutions = {
                    '$owner': str(owner.pk),
                    '$association': str(association.pk),
                    '$long_query': 'à' * 2001,
                    '$long_page': 'x' * 257,
                }
                for key, value in list(arguments.items()):
                    if isinstance(value, str) and value in substitutions:
                        arguments[key] = substitutions[value]
            receipt = base / 'bundle/bundle.json'
            original = receipt.read_bytes()
            try:
                if spec.get('corrupt'):
                    receipt.write_text('{}')
                case_before = snapshot()
                case_files = fingerprints(base)
                offset = (work / 'postgres.log').stat().st_size
                response = rpc.call(spec['method'], params, name)
                statements = sql_since(offset)
                report['cases'][name] = {
                    'response': response,
                    'database_unchanged': snapshot() == case_before,
                    'files_unchanged': fingerprints(base) == case_files,
                    'context_selects': sum(
                        bool(re.search(r'FROM\s+"?instance_instanceconfiguration"?', sql, re.I))
                        for sql in statements
                    ),
                    'total_selects': sum(
                        sql.lstrip().upper().startswith(('SELECT', 'WITH'))
                        for sql in statements
                    ),
                    'connection_setup_selects': sum(
                        sql.strip() == "SELECT set_config('TimeZone', 'UTC', false)"
                        for sql in statements
                    ),
                    'connection_health_probes': sum(sql.strip() == '-- ping' for sql in statements),
                    'write_statements': sum(
                        sql.strip() != '-- ping' and not sql.lstrip().upper().startswith(
                            ('SELECT', 'SET', 'SHOW', 'BEGIN', 'COMMIT', 'ROLLBACK', 'SAVEPOINT', 'RELEASE')
                        )
                        for sql in statements
                    ),
                    'fixture_identity': corpus['identity'],
                }
                validate_portable_report(
                    {'schema_version': 1, 'cases': {name: report['cases'][name]}},
                    {'cases': {name: spec}},
                )
                print('PASS Go MCP stdio: ' + name, flush=True)
            finally:
                receipt.write_bytes(original)

        rpc.raw(b'{invalid-json\n')
        malformed = rpc.receive()
        assert malformed == {
            'jsonrpc': '2.0',
            'method': 'notifications/message',
            'params': {
                'level': 'error',
                'data': 'Internal Server Error',
                'logger': 'mcp.server.exception_handler',
            },
        }
        report['session_checks']['malformed_notification'] = malformed
        assert rpc.call('ping', {}, 'after-malformed') == {
            'jsonrpc': '2.0', 'id': 'after-malformed', 'result': {},
        }
        report['session_checks']['malformed_line_then_ping'] = True

        for identifier in ('pipeline-a', 'pipeline-b'):
            rpc.send(
                'tools/call',
                {'name': 'get_manual_section', 'arguments': {'section_id': 'guide#public00'}},
                identifier,
            )
        responses = [rpc.receive(), rpc.receive()]
        assert {response['id'] for response in responses} == {'pipeline-a', 'pipeline-b'}
        for response in responses:
            assert not response['result']['isError']
            body = json.loads(response['result']['content'][0]['text'])
            assert body['status'] == 'verified' and body['section']['id'] == 'guide#public00'
        report['session_checks']['pipeline_responses'] = 2

        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('LOCK TABLE instance_instanceconfiguration IN ACCESS EXCLUSIVE MODE')
            rpc.send(
                'tools/call',
                {'name': 'search_manual', 'arguments': {'query': 'fixture'}},
                'blocked',
            )
            deadline = time.monotonic() + 10
            while True:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_stat_clear_snapshot()')
                    cursor.execute(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE application_name=%s AND wait_event_type='Lock'",
                        [APPLICATION_NAME],
                    )
                    blocked = cursor.fetchone()[0]
                if blocked:
                    break
                if time.monotonic() > deadline:
                    raise AssertionError('Go MCP child did not reach the locked SQL read')
                time.sleep(.02)
            report['session_checks']['blocked_sql_observed'] = True
            rpc.send(
                'notifications/cancelled',
                {'requestId': 'blocked', 'reason': 'fixture cancellation'},
            )
            cancelled = rpc.receive(timeout=5)
            assert cancelled['id'] == 'blocked'
            assert cancelled['error'] == {'code': 0, 'message': 'Request cancelled'}
            report['session_checks']['cancellation_error'] = cancelled['error']
            drain_deadline = time.monotonic() + CANCELLATION_DRAIN_TIMEOUT_SECONDS
            drain_polls = 0
            while True:
                # The parent lock remains held so a lock wait is direct evidence
                # that the cancelled Go SELECT has not drained.
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_stat_clear_snapshot()')
                    cursor.execute(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE application_name=%s AND wait_event_type='Lock'",
                        [APPLICATION_NAME],
                    )
                    lock_wait_remaining = cursor.fetchone()[0]
                drain_polls += 1
                if lock_wait_remaining == 0:
                    break
                if time.monotonic() >= drain_deadline:
                    raise AssertionError(
                        'Go SQL cancellation did not drain while parent lock was held: '
                        f'{lock_wait_remaining} Lock wait(s) remain after {drain_polls} polls'
                    )
                time.sleep(CANCELLATION_DRAIN_POLL_SECONDS)
            report['session_checks']['cancellation_drain'] = {
                'lock_wait_remaining': 0,
                'parent_lock_held': True,
                'pg_stat_snapshot_cleared': True,
                'polls': drain_polls,
                'timeout_ms': CANCELLATION_DRAIN_TIMEOUT_SECONDS * 1000,
            }

        assert rpc.call('ping', {}, 'after-cancel') == {
            'jsonrpc': '2.0', 'id': 'after-cancel', 'result': {},
        }
        report['session_checks']['ping_after_cancel'] = True

        before_recovery = (work / 'postgres.log').stat().st_size
        recovery = rpc.call(
            'tools/call',
            {'name': 'get_manual_section', 'arguments': {'section_id': 'guide#public00'}},
            'after-cancel-call',
        )
        recovery_body = json.loads(recovery['result']['content'][0]['text'])
        recovery_statements = sql_since(before_recovery)
        assert recovery_body['status'] == 'verified'
        assert sum(
            bool(re.search(r'FROM\s+"?instance_instanceconfiguration"?', sql, re.I))
            for sql in recovery_statements
        ) == 1
        report['session_checks']['recovery_status'] = recovery_body['status']
        report['session_checks']['recovery_context_selects'] = 1

        code = rpc.close()
        assert code == 0, 'Go MCP fixture exited unsuccessfully'
        clean_exits += 1
        report['session_checks']['clean_process_exits'] = clean_exits
        assert snapshot() == before and fingerprints(base) == files, 'MCP session mutated state'
    finally:
        if rpc.process.poll() is None:
            rpc.close()
