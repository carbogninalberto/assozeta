"""Legacy software-billing tombstones; association/course payments stay supported."""
import concurrent.futures
import json
import os
from pathlib import Path
import unittest
from TESTS.http_client import Client


class RetiredBillingContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client=Client(os.environ['TESTS_BASE_URL'])
        cls.fixture=json.loads(Path(os.environ['TESTS_RETIRED_BILLING_FIXTURE']).read_text())

    def request(self,kind,actor='owner',*,method=None,headers=None,data=None,query=''):
        values={'Accept-Language':'en'}
        if actor:values['Authorization']='Bearer '+self.fixture['tokens'][actor]
        values.update(headers or {})
        return self.client.request(method or ('GET' if kind=='active-plan' else 'POST'),'/billing/'+kind+query,
                                   data=data,headers=values,include_headers=True)

    def retired(self,response,kind):
        status,body,headers=response
        message=('Software subscriptions are no longer required.' if kind=='active-plan' else
                 'Platform billing checkout is disabled for self-hosted instances.')
        self.assertEqual((status,body),(410,{'msg':message}))
        self.assertIn('Cookie',headers['Vary'])
        self.assertIsNone(headers.get('Set-Cookie'))
        self.assertIsNone(headers.get('Location'))

    def test_legacy_plan_states_all_return_the_same_tombstone(self):
        for kind in ('active-plan','checkout'):
            for actor in ('owner','expired','cancelled','paid','no-expiration'):
                with self.subTest(kind=kind,actor=actor):self.retired(self.request(kind,actor),kind)

    def test_collaborator_scope_and_registry_exemption_are_preserved(self):
        for kind in ('active-plan','checkout'):
            for actor in ('restricted','full'):self.retired(self.request(kind,actor),kind)

    def test_athlete_does_not_receive_association_tombstone(self):
        for kind in ('active-plan','checkout'):
            status,body,headers=self.request(kind,'athlete')
            self.assertEqual((status,body),(403,{'msg':'Info not available.'}))

    def test_authentication_still_required_before_retirement_response(self):
        for kind in ('active-plan','checkout'):
            for headers in ({},{'Cookie':'sessionid=retired-billing-active'},
                            {'Cookie':'BKN_AUTH='+self.fixture['tokens']['owner']}):
                status,body,actual=self.request(kind,None,headers=headers)
                self.assertEqual((status,body),(401,{'detail':'Authentication credentials were not provided.'}))
                self.assertEqual(actual['WWW-Authenticate'],'Bearer realm="api"')
            for actor,code in [('inactive','user_inactive'),('deleted','user_not_found')]:
                status,body,_=self.request(kind,actor)
                self.assertEqual(status,401);self.assertEqual(body['code'],code)

    def test_administrator_must_select_an_eligible_identity(self):
        for kind in ('active-plan','checkout'):
            status,body,headers=self.request(kind,'admin')
            self.assertEqual((status,body),(403,{'detail':'Seleziona un utente da impersonificare per accedere a questa sezione.'}))
            self.retired(self.request(kind,'admin',headers={'X-Impersonation-Id':self.fixture['sessions']['admin_owner']}),kind)
            status,body,_=self.request(kind,'admin',headers={'X-Impersonation-Id':self.fixture['sessions']['admin_athlete']})
            self.assertEqual((status,body),(403,{'msg':'Info not available.'}))

    def test_association_scope_still_restricts_operation_and_foreign_references(self):
        for kind in ('active-plan','checkout'):
            status,body,headers=self.request(kind,headers={'X-Impersonation-Id':self.fixture['sessions']['owner_athlete']})
            self.assertEqual((status,body),(403,{'detail':'Operazione non disponibile nel contesto dell’associazione.'}))
            headers={'X-Impersonation-Id':self.fixture['sessions']['owner_staff']}
            self.retired(self.request(kind,headers=headers),kind)
            status,body,_=self.request(kind,headers=headers,query='?sport_association=00000000-0000-4000-8000-000000000090')
            self.assertEqual((status,body),(403,{'detail':'Risorsa non disponibile per questo utente e associazione.'}))

    def test_checkout_does_not_parse_unused_payload_outside_scoped_impersonation(self):
        for payload,content_type in [(b'{broken','application/json'),(b'opaque','text/plain')]:
            self.retired(self.request('checkout',data=payload,headers={'Content-Type':content_type}),'checkout')
        status,body,headers=self.request('checkout',data=b'{broken',headers={'Content-Type':'application/json',
            'X-Impersonation-Id':self.fixture['sessions']['owner_staff']})
        self.assertEqual(status,400);self.assertIn('JSON parse error',body['detail'])
        status,body,headers=self.request('checkout',data={'sport_association':'00000000-0000-4000-8000-000000000090'},
            headers={'X-Impersonation-Id':self.fixture['sessions']['owner_staff']})
        self.assertEqual((status,body),(403,{'detail':'Risorsa non disponibile per questo utente e associazione.'}))

    def test_method_and_options_metadata_are_retained(self):
        for kind,allowed,title in [('active-plan','GET','Billing Active Plan'),('checkout','POST','Billing Checkout')]:
            for method in ({'GET','POST','PUT','PATCH','DELETE','HEAD'}-{allowed}):
                status,body,headers=self.request(kind,method=method)
                self.assertEqual(status,405)
                if method!='HEAD':self.assertEqual(body,{'detail':f'Method "{method}" not allowed.'})
                self.assertEqual(set(v.strip() for v in headers['Allow'].split(',')),{allowed,'OPTIONS'})
            status,body,headers=self.request(kind,method='OPTIONS')
            self.assertEqual((status,body),(200,{'name':title,'description':'','renders':['application/json'],
                'parses':['application/json','application/x-www-form-urlencoded','multipart/form-data']}))

    def test_negotiation_and_session_errors_precede_tombstones(self):
        for kind in ('active-plan','checkout'):
            self.assertEqual(self.request(kind,headers={'Accept':'text/plain'})[0],406)
            self.assertEqual(self.request(kind,query='?format=xml')[0],404)
            status,body,headers=self.request(kind,headers={'Cookie':'sessionid=retired-billing-malformed'})
            self.assertEqual(status,500);self.assertIn('Server Error (500)',body)
            self.assertIsNone(headers.get('Allow'));self.assertIsNone(headers.get('Set-Cookie'))

    def test_concurrent_retired_responses_do_not_cross_roles(self):
        def run(i):
            kind='checkout' if i%2 else 'active-plan'
            actor='athlete' if i%3==0 else 'paid'
            return kind,actor,self.request(kind,actor)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            for kind,actor,response in pool.map(run,range(32)):
                if actor=='athlete':self.assertEqual(response[:2],(403,{'msg':'Info not available.'}))
                else:self.retired(response,kind)


if __name__=='__main__':unittest.main()
