"""Portable authored MCP stdio expectations; no backend or SDK imports."""
import hashlib
import copy
import json
import os
from pathlib import Path
import unittest

from TESTS.contracts.test_manual_tools import FIXTURE as TOOL_FIXTURE, validate_report as validate_tool

FIXTURE=Path(__file__).resolve().parents[1]/'fixtures/manual-mcp.json'


def validate_report(report,fixture):
    assert report.get('schema_version')==1
    assert set(report['cases'])==set(fixture['cases']), 'MCP cases differ; no skips'
    functions=json.loads(TOOL_FIXTURE.read_text())
    for name,spec in fixture['cases'].items():
        case=report['cases'][name];response=case['response']
        assert case['database_unchanged'] and case['files_unchanged'], name+': mutated state'
        assert case['write_statements']==0,name+': unexpected database mutation'
        assert 0<=case['connection_setup_selects']<=1,name+': repeated connection setup'
        reads=case['total_selects']-case['connection_setup_selects']
        assert reads==case['context_selects'],name+': unexpected database read'
        assert response['jsonrpc']=='2.0' and response['id']==name,name+': response correlation'
        if 'error' in spec:
            assert response=={'jsonrpc':'2.0','id':name,'error':spec['error']},name
            assert case['context_selects']==0,name
            continue
        result=response['result']
        if 'result' in spec:
            assert result==spec['result'],name
            assert case['context_selects']==0,name
            continue
        assert set(result)=={'content','isError'},name+': unexpected tool envelope'
        assert len(result['content'])==1 and set(result['content'][0])=={'type','text'},name
        assert result['content'][0]['type']=='text',name
        if 'validation_error' in spec:
            assert result['isError'] is True and result['content'][0]['text']==spec['validation_error'],name
            assert case['context_selects']==0,name
            continue
        assert result['isError'] is False,name
        body=json.loads(result['content'][0]['text'])
        if 'body' in spec:
            assert body==spec['body'],name
            assert case['context_selects']==0,name
            continue
        function=functions['cases'][spec['function_case']]
        assert case['context_selects']==(0 if body['status']=='invalid_query' else 1),name
        sections=body.get('results',[])+([body['section']] if isinstance(body.get('section'),dict) else [])
        observed=dict(result=body,ids=[s['id'] for s in sections],database_unchanged=True,files_unchanged=True,
            writes=case['write_statements'],selects=reads,source_canary_absent='never-public-canary' not in json.dumps(body),fixture_identity=case['fixture_identity'])
        validate_tool({'schema_version':1,'cases':{name:observed}},{'cases':{name:function}})


class ManualMCPContracts(unittest.TestCase):
    def test_extra_database_read_exceeds_budget(self):
        path=os.environ.get('TESTS_MANUAL_MCP_REPORT')
        self.assertTrue(path,'Actual stdio process report required')
        report=json.loads(Path(path).read_text());fixture=json.loads(FIXTURE.read_text())
        changed=copy.deepcopy(report)
        name=next(name for name,case in report['cases'].items() if case['context_selects']==1)
        changed['cases'][name]['total_selects']+=1
        with self.assertRaises(AssertionError):
            validate_report(changed,fixture)

    def test_actual_stdio_protocol(self):
        path=os.environ.get('TESTS_MANUAL_MCP_REPORT')
        self.assertTrue(path,'Actual stdio process report required')
        report=json.loads(Path(path).read_text());fixture=json.loads(FIXTURE.read_text())
        self.assertIs(report.get('success'),True)
        self.assertIs(report.get('private_services_stopped'),True)
        self.assertEqual(report['fixture_sha256'],hashlib.sha256(FIXTURE.read_bytes()).hexdigest())
        validate_report(report,fixture)
        tools=report['tools'];self.assertEqual(sorted(t['name'] for t in tools),sorted(fixture['tool_names']))
        for name,schema in fixture['manual_schemas'].items():
            tool=next(t for t in tools if t['name']==name)
            self.assertEqual(tool['inputSchema'],schema,name)
            self.assertTrue(tool['description'])
        for protocol,response in report['initializations'].items():
            self.assertEqual(response['result']['protocolVersion'],protocol if protocol in ['2024-11-05','2025-03-26','2025-06-18','2025-11-25'] else fixture['protocol'])
            self.assertEqual(response['result']['serverInfo'],{'name':'bakney-sport-data','version':report['protocol_runtime']['mcp']})
            self.assertEqual(response['result']['capabilities'],{'experimental':{},'tools':{'listChanged':False}})
        self.assertEqual(set(report['initializations']),{'2024-11-05',fixture['protocol'],'unsupported-fixture-version'})
        self.assertEqual(report['session_checks'],{'malformed_line_then_ping':True,'pipeline_responses':2,
            'malformed_notification':{'jsonrpc':'2.0','method':'notifications/message','params':{'level':'error','data':'Internal Server Error','logger':'mcp.server.exception_handler'}},
            'blocked_sql_observed':True,'cancellation_error':{'code':0,'message':'Request cancelled'},'ping_after_cancel':True,'clean_process_exits':3})


if __name__=='__main__':unittest.main()
