"""Exercise the actual management command and MCP SDK over subprocess pipes."""
import contextlib
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import sys
import time

from TESTS.adapters.django.manual_tools import prepare, fingerprints
from TESTS.mcp_client import StdioClient


def measure_mcp(work,fixture,report,log):
    from django.db import connection,transaction
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from application.models import User,SportAssociation
    from application.models.audit_models import AuditLogIndex
    from instance.models import InstanceConfiguration
    from TESTS.contracts.test_manual_mcp import validate_report
    from TESTS.adapters.django.manual_reader_http import ROOT
    with disable_auditlog():
        owner=User.original_objects.create(username='mcp-owner',email='mcp@example.test',password='!fixture',role=User.ASSOCIATION)
        association=SportAssociation.original_objects.create(user=owner,denomination='MCP Fixture ASD')
        InstanceConfiguration.objects.create(domain='mcp.example.test',name='MCP fixture',self_hosted=True,primary_association=association)
    def snapshot():
        return {m._meta.label:list(m._base_manager.order_by('pk').values()) for m in (User,SportAssociation,InstanceConfiguration,LogEntry,AuditLogIndex)}
    base=work/'mcp-source';settings,corpus=prepare(base,{})
    settings_path=work/'mcp-settings.json';settings_path.write_text(json.dumps(settings))
    env={**os.environ,'DJANGO_SETTINGS_MODULE':'TESTS.adapters.django.manual_mcp_settings',
         'TESTS_MANUAL_MCP_SETTINGS':str(settings_path),'PYTHONPATH':str(ROOT)+os.pathsep+str(ROOT/'BE'),
         'PYTHONUNBUFFERED':'1'}
    command=[sys.executable,str(ROOT/'BE/manage.py'),'run_mcp_server','--association-id',str(association.pk)]
    report['protocol_runtime']={name:importlib.metadata.version(name) for name in ('mcp','jsonschema','pydantic')}
    import mcp
    sdk=Path(mcp.__file__).parent
    report['protocol_source_hashes']={path:hashlib.sha256((sdk/path).read_bytes()).hexdigest() for path in (
        'server/stdio.py','server/lowlevel/server.py','server/session.py','shared/session.py','shared/version.py','types.py')}
    report['initializations']={};report['session_checks']={}
    clean_exits=0

    @contextlib.contextmanager
    def client(protocol):
        nonlocal clean_exits
        before=snapshot();files=fingerprints(base)
        rpc=StdioClient(command,env,log,ROOT)
        try:
            response=rpc.call('initialize',{'protocolVersion':protocol,'capabilities':{},'clientInfo':{'name':'portable-contract','version':'1'}},'initialize')
            report['initializations'][protocol]=response
            rpc.send('notifications/initialized',{})
            assert snapshot()==before and fingerprints(base)==files,'MCP startup mutated state'
            yield rpc
        finally:
            code=rpc.close()
            assert code==0,'MCP command exited unsuccessfully'
            assert snapshot()==before and fingerprints(base)==files,'MCP session mutated state'
            clean_exits+=1

    def sql_since(offset):
        with (work/'postgres.log').open('rb') as stream:stream.seek(offset);data=stream.read().decode()
        return re.findall(r'(?:statement:|execute [^:]*:)\s*(.*?)(?=^\d{4}-|\Z)',data,re.M|re.S)

    with client(fixture['protocol']) as rpc:
        listed=rpc.call('tools/list',{},'list')
        report['tools']=listed['result']['tools']
        for name,spec in fixture['cases'].items():
            params=json.loads(json.dumps(spec['params']))
            arguments=params.get('arguments')
            if isinstance(arguments,dict):
                substitutions={'$owner':str(owner.pk),'$association':str(association.pk),'$long_query':'à'*2001,'$long_page':'x'*257}
                for key,value in list(arguments.items()):
                    if isinstance(value,str) and value in substitutions:arguments[key]=substitutions[value]
            receipt=base/'bundle/bundle.json';original=receipt.read_bytes()
            try:
                if spec.get('corrupt'):receipt.write_text('{}')
                before=snapshot();files=fingerprints(base);offset=(work/'postgres.log').stat().st_size
                response=rpc.call(spec['method'],params,name)
                statements=sql_since(offset)
                item={'response':response,'database_unchanged':snapshot()==before,'files_unchanged':fingerprints(base)==files,
                      'context_selects':sum(bool(re.search(r'FROM\s+"?instance_instanceconfiguration"?',sql,re.I)) for sql in statements),
                      'total_selects':sum(sql.lstrip().upper().startswith(('SELECT','WITH')) for sql in statements),
                      'connection_setup_selects':sum(sql.strip()=="SELECT set_config('TimeZone', 'UTC', false)" for sql in statements),
                      'write_statements':sum(not sql.lstrip().upper().startswith(('SELECT','SET','SHOW','BEGIN','COMMIT','ROLLBACK','SAVEPOINT','RELEASE')) for sql in statements),
                      'fixture_identity':corpus['identity']}
                report['cases'][name]=item
                validate_report({'schema_version':1,'cases':{name:item}},{'cases':{name:spec}})
                print('PASS MCP stdio: '+name,flush=True)
            finally:receipt.write_bytes(original)
        rpc.raw(b'{invalid-json\n')
        malformed=rpc.receive()
        assert malformed=={'jsonrpc':'2.0','method':'notifications/message','params':{'level':'error','data':'Internal Server Error','logger':'mcp.server.exception_handler'}}
        report['session_checks']['malformed_notification']=malformed
        assert rpc.call('ping',{},'after-malformed')=={'jsonrpc':'2.0','id':'after-malformed','result':{}}
        report['session_checks']['malformed_line_then_ping']=True
        for identifier in ('pipeline-a','pipeline-b'):rpc.send('tools/call',{'name':'get_manual_section','arguments':{'section_id':'guide#public00'}},identifier)
        responses=[rpc.receive(),rpc.receive()]
        assert {r['id'] for r in responses}=={'pipeline-a','pipeline-b'}
        for response in responses:
            assert not response['result']['isError']
            body=json.loads(response['result']['content'][0]['text'])
            assert body['status']=='verified' and body['section']['id']=='guide#public00'
        report['session_checks']['pipeline_responses']=2
        # Observe a real child SQL wait before cancelling its MCP request.
        with transaction.atomic():
            with connection.cursor() as cursor:cursor.execute('LOCK TABLE instance_instanceconfiguration IN ACCESS EXCLUSIVE MODE')
            rpc.send('tools/call',{'name':'search_manual','arguments':{'query':'fixture'}},'blocked')
            deadline=time.monotonic()+10
            while True:
                with connection.cursor() as cursor:
                    # pg_stat_activity is cached within this lock-holding
                    # transaction; each poll must observe the child's new wait.
                    cursor.execute('SELECT pg_stat_clear_snapshot()')
                    cursor.execute("SELECT count(*) FROM pg_stat_activity WHERE application_name='manual-mcp-contract' AND wait_event_type='Lock'")
                    blocked=cursor.fetchone()[0]
                if blocked:break
                if time.monotonic()>deadline:raise AssertionError('MCP child did not reach the locked SQL read')
                time.sleep(.02)
            report['session_checks']['blocked_sql_observed']=True
            rpc.send('notifications/cancelled',{'requestId':'blocked','reason':'fixture cancellation'})
            cancelled=rpc.receive(timeout=5)
            assert cancelled['id']=='blocked' and cancelled['error']=={'code':0,'message':'Request cancelled'}
            report['session_checks']['cancellation_error']=cancelled['error']
        assert rpc.call('ping',{},'after-cancel')=={'jsonrpc':'2.0','id':'after-cancel','result':{}}
        report['session_checks']['ping_after_cancel']=True
    for protocol in ('2024-11-05','unsupported-fixture-version'):
        with client(protocol) as rpc:
            assert rpc.call('ping',{},'version-ping')['result']=={}
    report['session_checks']['clean_process_exits']=clean_exits
