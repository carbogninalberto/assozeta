"""Source-authored HTTP contract for collaborator deletion; no ORM dependency."""
import gzip
import json
from TESTS.contracts.test_impersonation_management import assert_json_equal
from TESTS.http_client import Client


def assert_case(test, base, case):
    headers={'Accept-Language':'en','X-Correlation-ID':case['name'],**case.get('headers',{})}
    status,body,received=Client(base,case.get('token')).request(case.get('method','DELETE'),case['path'],
        case.get('body'),headers,raw=True,include_headers=True)
    if received.get('Content-Encoding')=='gzip':body=gzip.decompress(body)
    try:value=json.loads(body)
    except (ValueError,UnicodeDecodeError):value=body.decode(errors='replace')
    test.assertEqual(status,case['status'],case['name'])
    assert_json_equal(test,value,case['response'],case['name'])
    test.assertIsNone(received.get('Set-Cookie'))
    test.assertEqual(received.get('Content-Language'),'en')
    test.assertEqual(received.get('Content-Type','').split(';')[0],'text/html' if status>=500 else 'application/json')
    if status<500:test.assertEqual(set(received.get('Allow','').replace(' ','').split(',')),{'DELETE','OPTIONS'})
    test.assertEqual(received.get('WWW-Authenticate'),'Bearer realm="api"' if status==401 else None)
    return status,value
