"""Business MCP tool functions: actual Django tool functions versus the Go port.

Private PostgreSQL with the current Django schema. Each authored call resets
the same seed, runs the Django function the way create_mcp_server's
call_tool does (exceptions become {'error': str(e)}; the result goes through
json.dumps(default=str)), records the result and every managed table, resets,
runs the Go BusinessMCPTools.Execute on the same arguments and requires the
identical result and state. New saved report ids and timestamps are checked
against the run window and normalized. Calls whose Django behaviour Go does
not port must be refused by Go before any write (state unchanged). This is
function-level parity: MCP transport, schema validation and the remaining
business tools are not exercised here.
"""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from uuid import UUID

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.go.age_worker_fixture import initialize_current_schema

DATABASE = 'assozeta_contract_business_mcp'
OWNER, OTHER, DELETED, SECOND_OWNER = (UUID(int=95001 + i) for i in range(4))
ASSOCIATION, SECOND = UUID(int=95101), UUID(int=95102)
REPORTS = [UUID(int=95201 + i) for i in range(4)]

# (tool, arguments, authored expectation): a result dict, or 'refused' for a
# call Go must refuse (Django's answer is recorded, not reproduced).
CASES = [
    ('sanitize_text', {'text': 'Report: 2025/26 <draft>?'}, {'result': 'Report- 2025-26 -draft'}),
    ('sanitize_text', {'text': ''}, {'result': ''}),
    ('sanitize_text', {'text': '  --a//b--  '}, {'result': 'a-b'}),
    ('sanitize_text', {'text': 'a/b', 'replacement': ''}, {'result': 'ab'}),
    ('sanitize_text', {'text': 'x:::y', 'replacement': '_'}, {'result': 'x_y'}),
    ('sanitize_text', {'text': 'a/b', 'replacement': 'ab'}, {'result': ''}),
    ('sanitize_text', {'text': 'tab\there\x01', 'replacement': '.'}, {'result': 'tab.here'}),
    ('sanitize_text', {'text': '　 é<>ü 　', 'replacement': '*'}, {'result': 'é*ü'}),
    ('sanitize_text', {'text': 'a/b', 'replacement': '\\'}, 'refused'),
    ('list_reports', {}, {'error': 'user_id is required to list reports'}),
    ('list_reports', {'user_id': ''}, {'error': 'user_id is required to list reports'}),
    ('list_reports', {'user_id': str(OWNER)}, 'owner reports'),
    ('list_reports', {'user_id': '{' + str(OWNER).upper() + '}'}, 'owner reports'),
    ('list_reports', {'user_id': 'abc'}, {'error': '[\'"abc" non è uno UUID valido.\']'}),
    ('list_reports', {'user_id': 95001}, 'owner reports'),
    ('list_reports', {'user_id': True}, {'reports': [], 'count': 0}),
    ('list_reports', {'user_id': [1]}, 'refused'),
    ('save_report', {'name': 'Presenze 2025/26', 'tool_name': 'query_data', 'params': {'model_name': 'Tags', 'limit': 10},
                     'description': 'Tutte le presenze', 'ui_config': {'fields': [{'key': 'limit', 'type': 'number'}]},
                     'user_id': str(OWNER)}, 'saved'),
    ('save_report', {'name': 'Minimo', 'tool_name': 'count_data', 'params': {}, 'user_id': str(OWNER)}, 'saved'),
    ('save_report', {'name': 'Numeri', 'tool_name': 'aggregate_data', 'params': {
        'b': 1.0e2, 'a': 123456789012345678901234567890, 'z': [0.1, -0.0, 'é '], 'nested': {'k': None, 'f': False}},
        'ui_config': {}, 'user_id': str(OTHER)}, 'saved'),
    ('save_report', {'name': 'x', 'tool_name': 'query_data', 'params': {}, 'user_id': str(UUID(int=99999))},
     {'error': 'User not found'}),
    ('save_report', {'name': 'x', 'tool_name': 'query_data', 'params': {}, 'user_id': str(DELETED)}, {'error': 'User not found'}),
    ('save_report', {'name': 'x', 'tool_name': 'query_data', 'params': {}}, {'error': 'user_id is required to save a report'}),
    ('save_report', {'name': 'x', 'tool_name': 'query_data', 'params': {}, 'user_id': 'nope'},
     {'error': '[\'"nope" non è uno UUID valido.\']'}),
    ('save_report', {'name': 'n' * 201, 'tool_name': 'query_data', 'params': {}, 'user_id': str(OWNER)}, 'refused'),
]


class Fixture:
    def __init__(self):
        from application.models import User, SportAssociation
        from application.models.report_models import SavedReport
        from auditlog.context import disable_auditlog
        with disable_auditlog():
            User.original_objects.bulk_create([
                User(user_id=OWNER, username='MCP-OWNER', role=1, password='!private-fixture'),
                User(user_id=OTHER, username='MCP-OTHER', role=3, connected_user_id=OWNER, password='!private-fixture'),
                User(user_id=DELETED, username='MCP-DELETED', role=1, deleted=True, password='!private-fixture'),
                User(user_id=SECOND_OWNER, username='MCP-SECOND', role=1, password='!private-fixture'),
            ])
            SportAssociation.original_objects.bulk_create([
                SportAssociation(sport_association_id=ASSOCIATION, user_id=OWNER, denomination='MCP fixture'),
                SportAssociation(sport_association_id=SECOND, user_id=SECOND_OWNER, denomination='MCP second'),
            ])
            base = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)
            rows = [(REPORTS[0], ASSOCIATION, OWNER, 'Vecchio', 1), (REPORTS[1], ASSOCIATION, OWNER, 'Recente', 5),
                    (REPORTS[2], SECOND, OWNER, 'Altra associazione', 9), (REPORTS[3], ASSOCIATION, OTHER, 'Altro utente', 7)]
            SavedReport.objects.bulk_create([SavedReport(saved_report_id=pk, sport_association_id=association,
                created_by_id=user, name=name, description='d & <b>', tool_name='query_data', params={'model_name': 'Tags'},
                ui_config={}, created_at=base) for pk, association, user, name, minutes in rows])
            for pk, association, user, name, minutes in rows:  # bulk_create applies auto_now
                SavedReport.objects.filter(pk=pk).update(updated_at=base.replace(microsecond=minutes * 1000, minute=minutes))
        self.seed = self.snapshot()

    def reset(self):
        from application.models.report_models import SavedReport
        SavedReport.objects.exclude(pk__in=REPORTS).delete()
        assert self.snapshot() == self.seed, 'fixture reset'

    @staticmethod
    def snapshot():
        from django.apps import apps
        return json.loads(json.dumps({m._meta.label: list(m._base_manager.order_by('pk').values())
                                      for m in apps.get_models() if m._meta.managed and not m._meta.proxy}, default=str))


def django_call(tool, arguments):
    """create_mcp_server.call_tool's dispatch for the ported tools."""
    from application.mcp_server import server
    functions = {'sanitize_text': server.tool_sanitize_text, 'list_reports': server.tool_list_reports,
                 'save_report': server.tool_save_report}
    kwargs = dict(arguments) if tool == 'sanitize_text' else dict(arguments, sport_association_id=str(ASSOCIATION))
    try:
        result = functions[tool](**kwargs)
    except Exception as error:  # call_tool returns the exception text
        result = {'error': str(error)}
    return json.loads(json.dumps(result, default=str))


def normalized(state, result, since, until):
    """Replace a new report's generated id and timestamps after checking them."""
    created = [row for row in state['application.SavedReport'] if UUID(row['saved_report_id']) not in REPORTS]
    for row in created:
        for key in ('created_at', 'updated_at'):
            assert since <= datetime.fromisoformat(row[key]) <= until, 'saved report timestamp inside the call'
        if isinstance(result, dict) and result.get('saved_report_id') == row['saved_report_id']:
            result = dict(result, saved_report_id='new-report')
        row.update(saved_report_id='new-report', created_at='run', updated_at='run')
    return state, result, len(created)


def main():
    require_production_python()
    output = ROOT / 'quality-reports/go-migration/business-mcp-tools'
    output.mkdir(parents=True, exist_ok=True)
    report = output / 'results.json'
    result = {'success': False, 'scope': __doc__.strip()}
    report.write_text(json.dumps(result) + '\n')
    postgres = shutil.which('postgres') or str(Path('/opt/homebrew/opt/postgresql@16/bin/postgres'))
    binaries = Path(postgres).parent
    base = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    base.update(LC_ALL='C', LANG='C')
    with tempfile.TemporaryDirectory(prefix='az-business-mcp-', dir='/tmp') as directory, (output / 'services.log').open('w') as log:
        work = Path(directory)
        attempted = False

        def run(args):
            return subprocess.run([str(a) for a in args], env=base, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
        try:
            driver = work / 'business-mcp.test'
            built = subprocess.run(['go', 'test', '-race', '-c', '-tags', 'businessmcpintegration', '-o', str(driver),
                                    './internal/service'], cwd=ROOT / 'BE_GO', env=base, stdout=log, stderr=subprocess.STDOUT,
                                   timeout=600)
            if built.returncode:
                driver = None  # RED: Django still runs every authored call.
            run([binaries / 'initdb', '-D', work / 'postgres', '-A', 'trust', '-U', 'contracts', '--no-sync', '--no-locale', '-E', 'UTF8'])
            attempted = True
            run([binaries / 'pg_ctl', '-D', work / 'postgres', '-l', work / 'postgres.log', '-o', f"-k {work} -h '' -F", '-w', 'start'])
            run([binaries / 'createdb', '-h', work, '-U', 'contracts', DATABASE])
            initialize_current_schema(work, work, '5432', DATABASE, log)
            fixture = Fixture()
            owner_reports = {'reports': [
                {'saved_report_id': str(REPORTS[1]), 'name': 'Recente', 'description': 'd & <b>', 'tool_name': 'query_data',
                 'updated_at': '2026-03-01T09:05:00.005000+00:00'},
                {'saved_report_id': str(REPORTS[0]), 'name': 'Vecchio', 'description': 'd & <b>', 'tool_name': 'query_data',
                 'updated_at': '2026-03-01T09:01:00.001000+00:00'}], 'count': 2}
            outcomes = []
            for index, (tool, arguments, expected) in enumerate(CASES):
                label = f'{index:02d} {tool} {json.dumps(arguments)[:60]}'
                fixture.reset()
                since = datetime.now(timezone.utc)
                django_result = django_call(tool, arguments)
                django_state, django_result, created = normalized(fixture.snapshot(), django_result, since,
                                                                  datetime.now(timezone.utc))
                if expected == 'owner reports':
                    expected = owner_reports
                if expected == 'saved':
                    assert created == 1 and django_result == {'saved_report_id': 'new-report', 'name': arguments['name'],
                        'message': f'Report "{arguments["name"]}" salvato.'}, (label, django_result)
                elif expected != 'refused':
                    assert django_result == expected and created == 0, (label, django_result)
                outcome = {'case': label, 'django': 'pass', 'django_result': django_result}
                if driver is None:
                    outcomes.append(dict(outcome, go='missing driver'))
                    continue
                fixture.reset()
                answer = work / 'answer.json'
                since = datetime.now(timezone.utc)
                completed = subprocess.run([str(driver), '-test.run', '^TestBusinessMCPToolCall$', '-test.count=1'], env=dict(base,
                    ASSOZETA_BUSINESS_MCP_DSN=f'host={work} port=5432 user=contracts dbname={DATABASE} sslmode=disable',
                    ASSOZETA_BUSINESS_MCP_ARGUMENTS=json.dumps(arguments), ASSOZETA_BUSINESS_MCP_TOOL=tool,
                    ASSOZETA_BUSINESS_MCP_ASSOCIATION=str(ASSOCIATION), ASSOZETA_BUSINESS_MCP_OUTPUT=str(answer)),
                    capture_output=True, text=True, timeout=120)
                assert completed.returncode == 0, (label, completed.stdout[-2000:])
                go = json.loads(answer.read_text())
                go_state, go_result, _ = normalized(fixture.snapshot(), go.get('result'), since, datetime.now(timezone.utc))
                if expected == 'refused':
                    assert go == {'refused': True} and go_state == fixture.seed, f'{label}: Go must refuse before writing'
                else:
                    if (go_result, go_state) != (django_result, django_state):
                        (output / 'difference.json').write_text(json.dumps({'case': label, 'django': [django_result, django_state],
                                                                            'go': [go_result, go_state]}, indent=1) + '\n')
                    assert go_result == django_result, f'{label}: Go result differs from Django'
                    assert go_state == django_state, f'{label}: Go state differs from Django'
                outcomes.append(dict(outcome, go='refused' if expected == 'refused' else 'pass'))
                print(f'PASS: {label}', flush=True)
            result['cases'] = outcomes
            result['django_passed'] = sum(o['django'] == 'pass' for o in outcomes)
            result['go_passed'] = sum(o['go'] in ('pass', 'refused') for o in outcomes)
            print(json.dumps({'django': f"{result['django_passed']}/{len(CASES)}", 'go': f"{result['go_passed']}/{len(CASES)}"}))
            assert result['go_passed'] == len(CASES), 'Go business MCP tools missing or failing'
        finally:
            try:
                from django.db import connections
                connections.close_all()
            finally:
                if attempted:
                    run([binaries / 'pg_ctl', '-D', work / 'postgres', '-m', 'immediate', '-w', 'stop'])
                report.write_text(json.dumps(result, indent=2, default=str) + '\n')
    paths = ['TESTS/adapters/go/business_mcp_tools.py', 'BE/application/mcp_server/server.py',
             'BE_GO/internal/service/business_mcp.go', 'BE_GO/internal/repository/business_mcp.go',
             'BE_GO/internal/service/business_mcp_integration_test.go']
    result.update(success=True, case_count=len(CASES), race_instrumented=True,
                  source_hashes={p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths})
    report.write_text(json.dumps(result, indent=2, default=str) + '\n')
    print(json.dumps({'success': True, 'case_count': len(CASES)}))


if __name__ == '__main__':
    main()
