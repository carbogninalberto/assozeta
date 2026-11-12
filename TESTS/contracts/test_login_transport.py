"""Portable login HTTP admission contracts; no credential success is claimed.

These cases stop before credential selection. Rate limiting, session middleware
and successful login composition have their own gates and are not replaced here.
"""
import os
import unittest
from TESTS.http_client import Client


class LoginTransportContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client=Client(os.environ['TESTS_BASE_URL'])

    def request(self,data=None,headers=None,method='POST',path='/oauth2/login'):
        result=self.client.request(method,path,data,headers,include_headers=True)
        self.assertIsNone(result[2].get('Set-Cookie'))
        return result

    def test_missing_fields_and_token_presence(self):
        for value in ({},{'username':'synthetic'},{'password':'synthetic'},{'username':'synthetic','token':None},
                      {'username':'synthetic','password':'synthetic','token':None},{'token':'synthetic','backend':'google'},
                      {'Username':'synthetic','password':'synthetic'}):
            with self.subTest(keys=list(value)):
                status,body,_=self.request(value)
                self.assertEqual((status,body),(400,{'msg':'Invalid request.'}))

    def test_nonobjects_retain_view_error(self):
        for value in (b'null',b'[]',b'"username"',b'42',b'true'):
            with self.subTest(value=value):
                status,body,_=self.request(value,{'Content-Type':'application/json'})
                self.assertEqual(status,500);self.assertIn('Server Error (500)',body)

    def test_methods_precede_body_parse(self):
        for method in ('GET','HEAD','PUT','PATCH','DELETE'):
            with self.subTest(method=method):
                status,body,headers=self.request(b'{',{'Content-Type':'application/json','Accept-Language':'en'},method)
                self.assertEqual(status,405);self.assertEqual(set(headers['Allow'].split(', ')),{'POST','OPTIONS'})
                if method!='HEAD':self.assertEqual(body,{'detail':f'Method "{method}" not allowed.'})

    def test_options_metadata_does_not_parse_body(self):
        status,body,headers=self.request(b'{',{'Content-Type':'application/json'},'OPTIONS')
        self.assertEqual(status,200)
        self.assertEqual(body,{'name':'Oauth2 Login','description':
            'API endpoint to login a user.\nSupports password auth and social auth (Google/Apple).\n\nRequest body:\n'
            '    Password login: {username, password} or {username, password, otp}\n'
            '    Social login: {username, token, backend} or {username, token, backend, otp}\n\nReturns:\n'
            '    {access_token, refresh_token, expires_in, token_type, role, user_data, ...}',
            'renders':['application/json'],'parses':['application/json','application/x-www-form-urlencoded','multipart/form-data']})
        self.assertEqual(set(headers['Allow'].split(', ')),{'POST','OPTIONS'})

    def test_negotiation_precedes_authentication(self):
        status,body,_=self.request(b'{',{'Accept':'text/html','Accept-Language':'en','Authorization':'Bearer'})
        self.assertEqual((status,body),(406,{'detail':'Could not satisfy the request Accept header.'}))

    def test_json_accept_quality_zero_is_still_accepted(self):
        status,body,_=self.request({}, {'Accept':'application/json;q=0'})
        self.assertEqual((status,body),(400,{'msg':'Invalid request.'}))

    def test_format_query_precedes_credentials(self):
        status,body,_=self.request({}, {'Accept-Language':'en','Authorization':'Bearer'},path='/oauth2/login?format=xml')
        self.assertEqual((status,body),(404,{'detail':'Not found.'}))
        status,body,_=self.request({},path='/oauth2/login?format=xml&format=json')
        self.assertEqual((status,body),(400,{'msg':'Invalid request.'}))

    def test_bad_bearer_precedes_body(self):
        status,body,headers=self.request(b'{',{'Content-Type':'application/json','Authorization':'Bearer'})
        self.assertEqual((status,body),(401,{'detail':'Authorization header must contain two space-delimited values','code':'bad_authorization_header'}))
        self.assertEqual(headers['WWW-Authenticate'],'Bearer realm="api"')

    def test_invalid_token_precedes_method(self):
        status,body,headers=self.request(headers={'Authorization':'Bearer synthetic-invalid'},method='GET')
        self.assertEqual(status,401)
        self.assertEqual(body,{'detail':'Given token not valid for any token type','code':'token_not_valid',
            'messages':[{'token_class':'AccessToken','token_type':'access','message':'Token is invalid'}]})
        self.assertEqual(headers['WWW-Authenticate'],'Bearer realm="api"')

    def test_other_auth_schemes_do_not_authenticate(self):
        for scheme in ('bearer','Basic'):
            status,body,_=self.request({}, {'Authorization':scheme+' synthetic-invalid'})
            self.assertEqual((status,body),(400,{'msg':'Invalid request.'}))

    def test_json_syntax_error(self):
        status,body,_=self.request(b'{',{'Content-Type':'application/json','Accept-Language':'en'})
        self.assertEqual(status,400)
        self.assertEqual(body,{'detail':'JSON parse error - Expecting property name enclosed in double quotes: line 1 column 2 (char 1)'})

    def test_unsupported_media(self):
        status,body,_=self.request(b'body',{'Content-Type':'text/plain','Accept-Language':'en'})
        self.assertEqual((status,body),(415,{'detail':'Unsupported media type "text/plain" in request.'}))

    def test_form_missing_fields(self):
        status,body,_=self.request(b'username=synthetic&token=',{'Content-Type':'application/x-www-form-urlencoded'})
        self.assertEqual((status,body),(400,{'msg':'Invalid request.'}))
        body=b'--fixture\r\nContent-Disposition: form-data; name="username"\r\n\r\nsynthetic\r\n--fixture--\r\n'
        status,result,_=self.request(body,{'Content-Type':'multipart/form-data; boundary=fixture'})
        self.assertEqual((status,result),(400,{'msg':'Invalid request.'}))

    def test_anonymous_scope_headers_do_not_create_identity(self):
        status,body,_=self.request({}, {'X-Impersonation-Id':'forged','User-Id':'foreign','X-Group-Id':'foreign'})
        self.assertEqual((status,body),(400,{'msg':'Invalid request.'}))

    def test_empty_body_ignores_unsupported_media(self):
        status,body,_=self.request(b'',{'Content-Type':'text/plain'})
        self.assertEqual((status,body),(400,{'msg':'Invalid request.'}))
