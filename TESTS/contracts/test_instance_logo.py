"""Portable authored logo HTTP contract; no backend/framework imports."""
import copy
import gzip
import hashlib
import unittest

ALLOW = 'GET, HEAD, OPTIONS'
PATH = '/instance/logo.png'
CONTENT_TYPES = {'png': 'image/png', 'jpg': 'image/jpeg', 'svg': 'image/svg+xml', 'webp': 'image/webp'}
# FileResponse serves the exact stored bytes; it does not validate/rasterize them.
PAYLOADS = {'png': b'\x89PNG\r\n\x1a\nprivate fixture PNG\x00', 'jpg': b'\xff\xd8\xffprivate fixture JPG\x00',
            'svg': b'<svg xmlns="http://www.w3.org/2000/svg"><text>logo</text></svg>',
            'webp': b'RIFF\x10\x00\x00\x00WEBPprivate fixture'}
DESCRIPTION = 'GET /instance/logo.png (or .jpg, .svg, .webp)\nServe the instance logo.'


def cases():
    result = []
    def add(name, *, files=None, selected='png', method='GET', headers=None, path=PATH,
            status=200, body=None, setup=None, mode='both', gzip_response=False):
        request_headers = {'Accept-Language': 'en', 'Accept-Encoding': 'identity', **(headers or {})}
        configuration = {'files': files if files is not None else {'png': 'png'}, **(setup or {})}
        expected_headers = {'Allow': ALLOW, 'Content-Language': 'en', 'WWW-Authenticate': None,
                            'Cache-Control': None, 'Content-Disposition': None, 'ETag': None,
                            'Last-Modified': None, 'Accept-Ranges': None, 'Content-Range': None,
                            'Content-Encoding': 'gzip' if gzip_response else None,
                            'Access-Control-Allow-Origin':None,'Access-Control-Allow-Credentials':None,'Retry-After':None,
                            'X-Frame-Options':None,'Strict-Transport-Security':None,
                            'X-Content-Type-Options':'nosniff','Referrer-Policy':'same-origin','Cross-Origin-Opener-Policy':'same-origin'}
        vary = ['accept-language', 'cookie', 'origin']
        expected = {'status': status, 'headers': expected_headers, 'vary_tokens': vary}
        if status == 200 and method in ('GET', 'HEAD'):
            payload = payload_bytes(configuration['files'][selected])
            expected_headers.update({'Content-Type': CONTENT_TYPES[selected], 'Cache-Control': 'no-cache',
                                     'Content-Disposition': f'inline; filename="logo.{selected}"',
                                     'Content-Length': None if gzip_response else str(len(payload))})
            vary.append('accept-encoding'); vary.sort()
            expected.update(body_sha256=hashlib.sha256(payload if method != 'HEAD' else b'').hexdigest(),
                            decoded_body_bytes=0 if method == 'HEAD' else len(payload))
        elif status in (400, 500):
            title = 'Bad Request (400)' if status == 400 else 'Server Error (500)'
            payload = ('\n<!doctype html>\n<html lang="en">\n<head>\n  <title>'+title+'</title>\n</head>\n<body>\n  <h1>'+title+'</h1><p></p>\n</body>\n</html>\n').encode()
            expected_headers.update({'Content-Type':'text/html; charset=utf-8','Allow':None})
            expected['body_sha256'] = hashlib.sha256(payload if method != 'HEAD' else b'').hexdigest()
        else:
            expected_headers['Content-Type'] = 'application/json'
            expected['json'] = body
            if method == 'HEAD': expected['json'] = None; expected['decoded_body_bytes'] = 0
            if method == 'OPTIONS' and status == 200: vary.append('accept-encoding'); vary.sort()
        result.append({'name': name, 'mode': mode, 'request': {'method': method, 'path': path, 'headers': request_headers},
                       'setup': configuration, 'expected': expected})
    for ext in CONTENT_TYPES:
        add('only-'+ext, files={ext: ext}, selected=ext)
        add('head-'+ext, files={ext: ext}, selected=ext, method='HEAD')
        add('gzip-'+ext, files={ext: ext}, selected=ext, headers={'Accept-Encoding': 'gzip'}, gzip_response=True)
    add('priority-all', files={ext: ext for ext in CONTENT_TYPES})
    add('priority-jpg-before-svg', files={ext: ext for ext in ('jpg', 'svg', 'webp')}, selected='jpg')
    add('priority-svg-before-webp', files={'svg': 'svg', 'webp': 'webp'}, selected='svg')
    add('empty', files={'png': 'empty'})
    add('empty-gzip', files={'png': 'empty'}, headers={'Accept-Encoding': 'gzip'}, gzip_response=True)
    add('legacy-over-upload-limit', files={'png': 'large'})
    add('gzip-head', method='HEAD', headers={'Accept-Encoding': 'gzip'}, gzip_response=True)
    add('gzip-zero-quality', headers={'Accept-Encoding': 'gzip;q=0'}, gzip_response=True)
    for name, lines, compressed, method in (
        ('duplicate-accept-image-first',[['Accept','image/png'],['Accept','application/json']],False,'GET'),
        ('duplicate-accept-json-first',[['Accept','application/json'],['Accept','image/png']],False,'GET'),
        ('duplicate-encoding-identity-first',[['Accept-Encoding','identity'],['Accept-Encoding','gzip']],True,'GET'),
        ('duplicate-encoding-gzip-first',[['Accept-Encoding','gzip'],['Accept-Encoding','identity']],True,'GET'),
        ('duplicate-encoding-head',[['Accept-Encoding','identity'],['Accept-Encoding','gzip']],True,'HEAD')):
        add(name,method=method,gzip_response=compressed)
        result[-1]['request']['header_lines']=lines
    add('range-ignored', headers={'Range': 'bytes=0-2'})
    add('conditional-ignored', headers={'If-None-Match': '*', 'If-Modified-Since': 'Thu, 01 Jan 2099 00:00:00 GMT'})
    add('bearer-disabled', headers={'Authorization': 'Bearer invalid-private-storage-canary'})
    add('scope-headers-ignored', headers={'User-Id': 'forged', 'X-Impersonation-Id': 'forged', 'X-Group-Id': 'forged'})
    add('accept-json', headers={'Accept': 'application/json'})
    add('accept-wildcard', headers={'Accept': '*/*'})
    add('accept-json-zero-quality', headers={'Accept': 'application/json;q=0'})
    add('query-does-not-select-key', path=PATH+'?path=secret.txt&filename=logo.jpg&ext=jpg', files={'png': 'png', 'jpg': 'jpg'})
    add('format-json', path=PATH+'?format=json')
    add('format-last-blank', path=PATH+'?format=xml&format=')
    for name, path, headers, code, detail in [
        ('format-invalid', PATH+'?format=xml', {}, 404, 'Not found.'),
        ('format-before-accept', PATH+'?format=xml', {'Accept': 'image/png'}, 404, 'Not found.'),
        ('accept-image-rejected', PATH, {'Accept': 'image/png'}, 406, 'Could not satisfy the request Accept header.'),
        ('missing', PATH, {}, 404, 'Logo not found')]:
        add(name, files={}, path=path, headers=headers, status=code, body={'detail': detail})
    add('missing-head', files={}, method='HEAD', status=404, body=None)
    for method in ('POST', 'PUT', 'PATCH', 'DELETE', 'TRACE'):
        add('method-'+method.lower(), method=method, status=405, body={'detail': f'Method "{method}" not allowed.'})
    add('options', method='OPTIONS', files={}, body={'name': 'Instance Logo Serve', 'description': DESCRIPTION,
        'renders': ['application/json'], 'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']})
    optional=copy.deepcopy(result[-1])
    optional['name']='options-duplicate-encoding'
    optional['request']['header_lines']=[['Accept-Encoding','identity'],['Accept-Encoding','gzip']]
    # Random gzip filename padding may make this small finite metadata response
    # unprofitable. Both outcomes are valid; compressed bytes must be smaller.
    optional['expected']['headers']['Content-Encoding']=[None,'gzip']
    result.append(optional)
    for ext in ('jpg','svg','webp'):
        add('unregistered-route-'+ext,path='/instance/logo.'+ext,status=404)
        # These are outside the registered view; only absence of an alias and
        # file-serving headers is contracted, not unrelated global 404 bodies.
        result[-1]['expected']={'status':404,'headers':{'Content-Disposition':None,'Cache-Control':None}}
    add('italian-success', headers={'Accept-Language':'it'})
    result[-1]['expected']['headers']['Content-Language']='it'
    add('italian-method', method='POST', headers={'Accept-Language':'it'}, status=405, body={'detail':'Metodo "POST" non consentito'})
    result[-1]['expected']['headers']['Content-Language']='it'
    for cookie in ('valid','stale','missing'):
        add('session-'+cookie,setup={'cookie':cookie})
    add('restore-lock', status=503, body={'error':'Un’altra operazione sui dati è in corso. Riprova tra poco.'}, setup={'lock':True})
    result[-1]['expected']['headers'].update({'Allow':None,'Retry-After':'10'})
    result[-1]['expected']['vary_tokens']=['accept-language','origin']
    add('pending-restore-read', setup={'pending':True})
    add('pending-restore-post', method='POST',status=503,body={'error':'Ripristino dei dati in corso. Le modifiche sono temporaneamente sospese.'},setup={'pending':True})
    result[-1]['expected']['headers'].update({'Allow':None,'Retry-After':'10'})
    result[-1]['expected']['vary_tokens']=['accept-language','origin']
    for origin in ('https://allowed.example.test','https://denied.example.test'):
        add('cors-'+('allowed' if 'allowed' in origin else 'denied'),headers={'Origin':origin})
        if 'allowed' in origin: result[-1]['expected']['headers'].update({'Access-Control-Allow-Origin':origin,'Access-Control-Allow-Credentials':None})
    add('cors-preflight',method='OPTIONS',headers={'Origin':'https://allowed.example.test','Access-Control-Request-Method':'GET'})
    expected=result[-1]['expected'];expected.pop('json');expected['body_sha256']=hashlib.sha256(b'').hexdigest()
    expected['vary_tokens']=['accept-language','origin']
    expected['headers'].update({'Allow':None,'Content-Type':'text/html; charset=utf-8','Access-Control-Allow-Origin':'https://allowed.example.test','Access-Control-Allow-Credentials':None})
    add('disk-directory-falls-through', files={'png': 'directory', 'jpg': 'jpg'}, selected='jpg', mode='disk')
    add('disk-broken-symlink-falls-through', files={'png': 'broken-symlink', 'webp': 'webp'}, selected='webp', mode='disk')
    add('disk-symlink-readable', files={'png': 'readable-symlink'}, mode='disk')
    add('s3-metadata-not-forwarded', mode='s3', setup={'metadata': {'ContentType': 'text/plain',
        'CacheControl': 'public, max-age=99999', 'ContentDisposition': 'attachment; filename="private-storage-canary"'}})
    add('s3-encoded-bytes-not-decoded', files={'png': 'stored-gzip'}, mode='s3', setup={'metadata': {'ContentEncoding': 'gzip'}})
    add('s3-exists-denied', mode='s3', status=500, setup={'fault':'head-denied'})
    add('s3-exists-persistent500', mode='s3', status=500, setup={'fault':'head-always500'})
    add('s3-get-denied-falls-through', mode='s3', files={'png':'png','jpg':'jpg'}, selected='jpg', setup={'fault':'get-png-denied'})
    add('s3-all-open-denied-is404', mode='s3', files={ext:ext for ext in CONTENT_TYPES}, status=404, body={'detail':'Logo not found'}, setup={'fault':'get-all-denied'})
    add('s3-location-empty', mode='s3', setup={'location':''})
    add('s3-location-trailing-slash', mode='s3', setup={'location':'private-root/'})
    add('s3-location-dot', mode='s3', files={}, status=400, setup={'location':'.'})
    add('s3-location-unnormalized', mode='s3', files={}, status=400, setup={'location':'a/../b'})
    add('s3-transient-head-recovers', mode='s3', setup={'fault':'head-once500'})
    add('s3-transient-get-recovers', mode='s3', setup={'fault':'get-once500'})
    add('s3-transient-truncated-get-recovers', mode='s3', setup={'fault':'get-once-truncated'})
    add('s3-open-disappearance-falls-through', files={'png': 'png', 'jpg': 'jpg'}, selected='jpg', mode='s3', setup={'fault': 'delete-after-exists'})
    add('s3-download-disappearance-falls-through', files={'png': 'png', 'webp': 'webp'}, selected='webp', mode='s3', setup={'fault': 'delete-before-get'})
    for case in result:
        if case['name'] in ('restore-lock','pending-restore-post','cors-preflight'):
            case['expected']['headers'].update({'X-Content-Type-Options':None,'Referrer-Policy':None,'Cross-Origin-Opener-Policy':None})
    return result


def case_for_transport(case, versioned=False):
    expected=copy.deepcopy(case)
    if versioned:
        headers=expected['expected']['headers']
        # Shipped Caddy adds nosniff and supplies Referrer-Policy only when the
        # upstream omitted it, including restore/preflight short circuits.
        if 'X-Content-Type-Options' in headers:headers['X-Content-Type-Options']='nosniff'
        if 'Referrer-Policy' in headers and headers['Referrer-Policy'] is None:headers['Referrer-Policy']='strict-origin-when-cross-origin'
    return expected


def payload_bytes(name):
    if name == 'empty': return b''
    if name == 'large': return b'legacy-logo-data\x00' * (400 * 1024)  # 6,963,200 bytes; above the existing upload cap.
    if name == 'readable-symlink': return PAYLOADS['png']
    if name == 'stored-gzip': return gzip.compress(PAYLOADS['png'], mtime=0)
    return PAYLOADS[name]


def assert_observation(actual, case):
    expected = case['expected']
    for key, value in expected.items():
        if key == 'headers':
            for name, header in value.items():
                observed_header=actual.get('headers', {}).get(name)
                matches=observed_header in header if isinstance(header,list) else observed_header==header
                if not matches:
                    raise AssertionError(f'{case["name"]} header {name}: expected {header!r}, observed {actual.get("headers", {}).get(name)!r}')
        elif type(actual.get(key)) is not type(value) or actual.get(key) != value:
            raise AssertionError(f'{case["name"]} {key}: expected {value!r}, observed {actual.get(key)!r}')
    if actual.get('managed_state_unchanged') is not True:
        raise AssertionError(case['name']+': managed state changed')
    if actual.get('private_storage_canary_absent') is not True:
        raise AssertionError(case['name']+': private storage detail leaked')


class InstanceLogoContract(unittest.TestCase):
    def test_authored_cases_are_unique_and_explicit(self):
        matrix = cases()
        self.assertEqual(len(matrix), len({case['name'] for case in matrix}))
        self.assertTrue(all('status' in case['expected'] and 'headers' in case['expected'] for case in matrix))
        self.assertGreater(len(payload_bytes('large')), 5 << 20)

    def test_validator_rejects_missing_route_and_boolean_status(self):
        case = cases()[0]
        actual = copy.deepcopy(case['expected'])
        actual.update(managed_state_unchanged=True, private_storage_canary_absent=True)
        assert_observation(actual, case)
        for invalid in (404, True):
            actual['status'] = invalid
            with self.assertRaises(AssertionError): assert_observation(actual, case)

    def test_versioned_security_headers_preserve_upstream_defaults(self):
        blocked=next(case for case in cases() if case['name']=='restore-lock')
        versioned=case_for_transport(blocked,True)
        self.assertIsNone(blocked['expected']['headers']['X-Content-Type-Options'])
        self.assertEqual(versioned['expected']['headers']['X-Content-Type-Options'],'nosniff')
        self.assertEqual(versioned['expected']['headers']['Referrer-Policy'],'strict-origin-when-cross-origin')
        self.assertEqual(case_for_transport(cases()[0],True)['expected']['headers']['Referrer-Policy'],'same-origin')

    def test_finite_optional_gzip_is_an_explicit_alternative(self):
        case=next(case for case in cases() if case['name']=='options-duplicate-encoding')
        actual=copy.deepcopy(case['expected'])
        actual.update(managed_state_unchanged=True,private_storage_canary_absent=True)
        for encoding in (None,'gzip'):
            actual['headers']['Content-Encoding']=encoding
            assert_observation(actual,case)
        actual['headers']['Content-Encoding']='br'
        with self.assertRaises(AssertionError):assert_observation(actual,case)

    def test_header_contract_does_not_accept_s3_metadata(self):
        case = cases()[0]
        actual = copy.deepcopy(case['expected'])
        actual.update(managed_state_unchanged=True, private_storage_canary_absent=True)
        actual['headers']['ETag'] = 'metadata'
        with self.assertRaises(AssertionError): assert_observation(actual, case)


if __name__ == '__main__': unittest.main()
