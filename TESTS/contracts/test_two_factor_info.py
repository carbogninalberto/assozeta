"""Portable read-only 2FA status contracts, with declared adapter identities."""
import concurrent.futures
import json
import os
from pathlib import Path
import unittest
from TESTS.http_client import Client


class TwoFactorInfoContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client=Client(os.environ['TESTS_BASE_URL'])
        cls.fixture=json.loads(Path(os.environ['TESTS_TWO_FACTOR_INFO_FIXTURE']).read_text())

    def request(self,actor=None,*,method='GET',headers=None,query=''):
        values={'Accept-Language':'en'}
        if actor:values['Authorization']='Bearer '+self.fixture['tokens'][actor]
        values.update(headers or {})
        return self.client.request(method,'/two-fa/info'+query,headers=values,include_headers=True)

    def enabled(self,response,value):
        status,body,headers=response
        self.assertEqual((status,body),(200,{'data':{'enabled':value}}))
        self.assertIn('Cookie',headers['Vary'])
        self.assertIsNone(headers.get('Set-Cookie'))
        self.assertEqual(set(v.strip() for v in headers['Allow'].split(',')),{'GET','OPTIONS'})

    def test_requires_bearer_even_with_session_or_auth_cookie(self):
        for headers in ({},{'Cookie':'sessionid=two-factor-info-active'},
                        {'Cookie':'BKN_AUTH='+self.fixture['tokens']['owner']},
                        {'Authorization':'Basic ignored'}):
            status,body,actual=self.request(headers=headers)
            self.assertEqual((status,body),(401,{'detail':'Authentication credentials were not provided.'}))
            self.assertEqual(actual['WWW-Authenticate'],'Bearer realm="api"')
        status,body,headers=self.request(headers={'Accept-Language':'it'})
        self.assertEqual((status,body),(401,{'detail':'Non sono state immesse le credenziali di autenticazione.'}))

    def test_owner_and_athlete_read_their_current_status(self):
        self.enabled(self.request('owner'),True)
        self.enabled(self.request('athlete'),False)
        self.enabled(self.request('foreign'),False)

    def test_collaborators_read_owner_status_only_with_permission(self):
        for actor in ('granted','full'):self.enabled(self.request(actor),True)
        status,body,headers=self.request('restricted')
        self.assertEqual((status,body),(403,{'detail':'Missing permission: other.settings.read'}))
        self.assertIsNone(headers.get('WWW-Authenticate'))

    def test_disabled_deleted_and_invalid_bearer_are_rejected(self):
        for actor,code in [('inactive','user_inactive'),('deleted','user_not_found')]:
            status,body,headers=self.request(actor)
            self.assertEqual(status,401);self.assertEqual(body['code'],code)
        status,body,headers=self.request(headers={'Authorization':'Bearer invalid'})
        self.assertEqual(status,401);self.assertEqual(body['code'],'token_not_valid')

    def test_administrator_requires_explicit_impersonation(self):
        for method in ('GET','POST','OPTIONS'):
            status,body,headers=self.request('admin',method=method)
            self.assertEqual((status,body),(403,{'detail':'Seleziona un utente da impersonificare per accedere a questa sezione.'}))
        self.enabled(self.request('admin',headers={'X-Impersonation-Id':self.fixture['sessions']['admin_foreign']}),False)
        self.enabled(self.request('admin',headers={'X-Impersonation-Id':self.fixture['sessions']['admin_granted']}),True)

    def test_association_impersonation_preserves_operation_and_resource_guards(self):
        status,body,headers=self.request('owner',headers={'X-Impersonation-Id':self.fixture['sessions']['owner_athlete']})
        self.assertEqual((status,body),(403,{'detail':'Operazione non disponibile nel contesto dell’associazione.'}))
        headers={'X-Impersonation-Id':self.fixture['sessions']['owner_granted']}
        self.enabled(self.request('owner',headers=headers),True)
        self.enabled(self.request('owner',headers=headers,query='?sport_association=00000000-0000-4000-8000-000000000080'),True)
        status,body,_=self.request('owner',headers=headers,query='?sport_association=00000000-0000-4000-8000-000000000090')
        self.assertEqual((status,body),(403,{'detail':'Risorsa non disponibile per questo utente e associazione.'}))

    def test_forged_scope_is_denied_before_status_read(self):
        for actor in (None,'owner'):
            status,body,headers=self.request(actor,headers={'User-Id':'00000000-0000-4000-8000-000000000009','X-Impersonation-Id':'forged'})
            self.assertEqual(status,403);self.assertEqual(body['impersonation_invalid'],'True')

    def test_methods_and_options_require_identity_first(self):
        for method in ('POST','PUT','PATCH','DELETE','HEAD'):
            status,body,headers=self.request('owner',method=method)
            self.assertEqual(status,405)
            if method!='HEAD':self.assertEqual(body,{'detail':f'Method "{method}" not allowed.'})
            self.assertEqual(self.request(method=method)[0],401)
        status,body,headers=self.request('owner',method='OPTIONS')
        self.assertEqual((status,body),(200,{'name':'Two Fa Info',
            'description':'This endpoint returns info about two factor authentication for the requesting user\n:param request:\n:return:',
            'renders':['application/json'],'parses':['application/json','application/x-www-form-urlencoded','multipart/form-data']}))
        self.assertEqual(self.request(method='OPTIONS')[0],401)

    def test_negotiation_and_malformed_session_precede_authentication(self):
        self.assertEqual(self.request(headers={'Accept':'text/plain'})[0],406)
        self.assertEqual(self.request(query='?format=xml')[0],404)
        status,body,headers=self.request(headers={'Cookie':'sessionid=two-factor-info-malformed','Authorization':'Bearer invalid'},method='POST')
        self.assertEqual(status,500);self.assertIn('Server Error (500)',body)
        self.assertEqual(headers['Content-Type'],'text/html; charset=utf-8')
        self.assertIsNone(headers.get('Allow'));self.assertIsNone(headers.get('WWW-Authenticate'))

    def test_concurrent_actor_and_scope_results_remain_separate(self):
        cases=[('owner',True),('athlete',False),('granted',True),('foreign',False)]
        def run(i):
            actor,value=cases[i%len(cases)]
            return value,self.request(actor)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            for value,response in pool.map(run,range(32)):self.enabled(response,value)


if __name__=='__main__':unittest.main()
