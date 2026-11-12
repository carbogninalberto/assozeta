"""Public identifier availability contracts; only synthetic adapter credentials.

The existing API permits get_user discovery. These tests retain that declared
behavior, including inactive accounts, without turning session identity into API
permission or weakening impersonation restrictions.
"""
import concurrent.futures
import json
import os
from pathlib import Path
import unittest
from urllib.parse import urlencode
from TESTS.http_client import Client


def uid(n):return f'00000000-0000-4000-8000-{n:012d}'


def person(n,first,email):
    return {'first_name':first,'last_name':'Availability','email':email,'user_id':uid(n)}


class IdentifierAvailabilityContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client=Client(os.environ['TESTS_BASE_URL'])
        cls.fixture=json.loads(Path(os.environ['TESTS_IDENTIFIER_FIXTURE']).read_text())

    def request(self,kind,value=None,*,extra=(),headers=None,method='GET',raw_query=None):
        pairs=[] if value is None else [(kind,value)]
        query=raw_query if raw_query is not None else urlencode([*pairs,*extra])
        return self.client.request(method,'/oauth2/check/'+kind+('?' + query if query else ''),headers=headers,include_headers=True)

    def result(self,response,kind,available,*,user=None):
        status,body,headers=response
        expected={'valid':True,'data':{'msg':kind+' is available.'}} if available else {'valid':False,'exception':kind+' already taken.'}
        if user is not None:expected['user']=user
        self.assertEqual((status,body),(200 if available else 409,expected))
        self.assertEqual({v.strip() for v in headers['Allow'].split(',')},{'GET','OPTIONS'})
        self.assertIn('Cookie',headers['Vary'])
        self.assertIsNone(headers.get('Set-Cookie'))

    def test_unused_identifiers_are_available(self):
        for kind in ('email','username'):self.result(self.request(kind,'availability-unused'),kind,True)

    def test_case_insensitive_accounts_include_disabled_and_exclude_deleted(self):
        for kind in ('email','username'):
            for suffix,available in [('mixed',False),('inactive',False),('deleted',True)]:
                value='availability-'+suffix+('@example.test' if kind=='email' else '')
                with self.subTest(kind=kind,value=value):self.result(self.request(kind,value.upper()),kind,available)

    def test_email_get_user_selects_first_uuid_duplicate(self):
        self.result(self.request('email','AVAILABILITY-DUP@example.test',extra=[('get_user','')]),'email',False,
                    user=person(80004,'First','availability-dup@example.test'))

    def test_get_user_presence_enables_username_fallback(self):
        for flag in ('','false','0','no'):
            self.result(self.request('email','AVAILABILITY-FALLBACK',extra=[('get_user',flag)]),'email',False,
                        user=person(80006,'Fallback','availability-fallback-mail@example.test'))
        self.result(self.request('email','availability-fallback'),'email',True)

    def test_email_match_precedes_username_match(self):
        self.result(self.request('email','availability-priority',extra=[('get_user','1')]),'email',False,
                    user=person(80007,'Email priority','availability-priority'))

    def test_inactive_user_details_and_missing_fallback(self):
        self.result(self.request('email','availability-inactive@example.test',extra=[('get_user','1')]),'email',False,
                    user=person(80002,'Inactive','availability-inactive@example.test'))
        self.result(self.request('email','availability-unused',extra=[('get_user','1')]),'email',True)

    def test_identifiers_are_literal_and_not_trimmed(self):
        for kind in ('email','username'):
            self.result(self.request(kind,'availability%_literal'),kind,False)
            for value in ('availabilityXYliteral',"availability' OR 1=1 --",' availability-mixed ', 'AVAILABILITY-MIXED '):
                self.result(self.request(kind,value),kind,True)

    def test_length_limit_counts_unicode_characters(self):
        for kind,limit in [('email',255),('username',150)]:
            for ch in ('x','è','😀'):
                self.result(self.request(kind,ch*limit),kind,True)
                status,body,headers=self.request(kind,ch*(limit+1))
                self.assertEqual((status,body),(400,{'valid':False,'exception':'invalid payload.'}))
                self.assertIsNone(headers.get('Set-Cookie'))

    def test_duplicate_query_uses_last_value(self):
        for kind in ('email','username'):
            taken='availability-mixed'+('@example.test' if kind=='email' else '')
            self.result(self.request(kind,'availability-unused',extra=[(kind,taken)]),kind,False)
            self.result(self.request(kind,taken,extra=[(kind,'availability-unused')]),kind,True)

    def test_missing_query_is_server_error_without_details(self):
        for kind in ('email','username'):
            status,body,headers=self.request(kind)
            self.assertEqual(status,500);self.assertIn('Server Error (500)',body)
            self.assertEqual(headers['Content-Type'],'text/html; charset=utf-8')
            self.assertIsNone(headers.get('Allow'));self.assertIsNone(headers.get('Set-Cookie'))

    def test_methods_and_options(self):
        for kind in ('email','username'):
            for method in ('POST','PUT','PATCH','DELETE','HEAD'):
                status,body,headers=self.request(kind,'availability-unused',method=method,headers={'Accept-Language':'en'})
                self.assertEqual(status,405)
                if method!='HEAD':self.assertEqual(body,{'detail':f'Method "{method}" not allowed.'})
                self.assertEqual({v.strip() for v in headers['Allow'].split(',')},{'GET','OPTIONS'})
            status,body,headers=self.request(kind,method='OPTIONS')
            description=('API endpoint to check if an email is already taken\n:param request: data: {username, password} or {token}\n:return: token, refresh token' if kind=='email' else
                         'API endpoint to check if a username is already taken\n:param request:\n:return:')
            self.assertEqual((status,body),(200,{'name':'Oauth2 Check '+kind.title(),'description':description,
                                               'renders':['application/json'],'parses':['application/json','application/x-www-form-urlencoded','multipart/form-data']}))

    def test_negotiation_and_bearer_precede_query_validation(self):
        for kind in ('email','username'):
            self.assertEqual(self.request(kind,headers={'Accept':'text/plain'})[0],406)
            self.assertEqual(self.request(kind,extra=[('format','xml')])[0],404)
            status,body,headers=self.request(kind,headers={'Authorization':'Bearer invalid'},method='POST')
            self.assertEqual(status,401);self.assertEqual(body['code'],'token_not_valid')
            self.assertEqual(headers['WWW-Authenticate'],'Bearer realm="api"')

    def test_current_bearer_state_and_public_collaborator_exclusions(self):
        for kind in ('email','username'):
            for actor in ('owner','staff'):
                self.result(self.request(kind,'availability-unused',headers={'Authorization':'Bearer '+self.fixture[actor]}),kind,True)
            for actor,code in [('inactive','user_inactive'),('deleted','user_not_found')]:
                status,body,headers=self.request(kind,'availability-unused',headers={'Authorization':'Bearer '+self.fixture[actor]})
                self.assertEqual(status,401);self.assertEqual(body['code'],code)

    def test_forged_anonymous_scope_and_impersonation_denial(self):
        for kind in ('email','username'):
            self.result(self.request(kind,'availability-unused',headers={'User-Id':uid(10),'X-Impersonation-Id':'forged','X-Group-Id':'forged'}),kind,True)
            status,body,headers=self.request(kind,'availability-unused',headers={'Authorization':'Bearer '+self.fixture['owner'],'X-Impersonation-Id':self.fixture['owner_session']})
            self.assertEqual((status,body),(403,{'detail':'Operazione non disponibile nel contesto dell’associazione.'}))

    def test_malformed_session_precedes_bearer_and_method(self):
        for kind in ('email','username'):
            status,body,headers=self.request(kind,headers={'Cookie':'sessionid=identifier-malformed-session','Authorization':'Bearer invalid'},method='POST')
            self.assertEqual(status,500);self.assertIn('Server Error (500)',body)
            self.assertIsNone(headers.get('Allow'));self.assertIsNone(headers.get('Set-Cookie'))

    def test_concurrent_identifier_checks_keep_results_separate(self):
        def run(i):
            kind='email' if i%2 else 'username'
            value='availability-mixed'+('@example.test' if kind=='email' else '') if i%3 else 'availability-unused'
            return kind,i%3==0,self.request(kind,value)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            for kind,available,response in pool.map(run,range(32)):self.result(response,kind,available)


if __name__=='__main__':unittest.main()
