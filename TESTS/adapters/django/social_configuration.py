"""Current social client-ID resolution on private, current-model PostgreSQL.

No provider request, customer credential or development environment is used.
Expected values and Python exception classes are declared before either runtime.
"""
import json
import subprocess

from .accounts import ROOT


def run(work, log, tool_env):
    from django.apps import apps
    from django.db import connection
    from django.test import override_settings
    from django.test.utils import CaptureQueriesContext
    from instance.integration_configuration import provider_client_id
    from instance.models import InstanceConfiguration

    assert not InstanceConfiguration.objects.exists(), 'configuration fixture must precede account seeding'
    cases=[]
    def add(name, provider='google', expected='env-google', *, error=None, hosted=True, integrations=None, display=None):
        cases.append(dict(name=name,provider=provider,expected=expected,error=error,hosted=hosted,
                          integrations={} if integrations is None else integrations,display={} if display is None else display))
    add('default visibility')
    add('apple environment','apple','env-apple')
    add('google hidden',expected='',display={'login':{'allowOauthLogin':False}})
    add('apple ignores visibility','apple','env-apple',display={'login':{'allowOauthLogin':False}})
    add('hosted ignores malformed overrides',hosted=False,integrations=[1],display=[1])
    add('explicit google overrides visibility',expected='saved-google',integrations={'google':{'settings':{'enabled':True,'client_id':'saved-google'}}},display={'login':None})
    add('explicit apple','apple','saved-apple',integrations={'apple':{'settings':{'enabled':True,'client_id':'saved-apple'}}})
    add('disabled does not require ID',expected='',integrations={'google':{'settings':{'enabled':False}}})
    add('reset restores visibility',expected='',integrations={'google':{'settings':None,'revision':7}},display={'login':{'allowOauthLogin':False}})
    add('record without settings',integrations={'google':{'revision':9}})
    add('empty override errors',error='KeyError',expected=None,integrations={'google':{'settings':{}}})
    add('enabled missing ID',error='KeyError',expected=None,integrations={'google':{'settings':{'enabled':True}}})
    add('null ID preserved',expected=None,integrations={'google':{'settings':{'enabled':True,'client_id':None}}})
    add('list ID preserved',expected=['first','second'],integrations={'google':{'settings':{'enabled':'false','client_id':['first','second']}}})
    add('number ID preserved',expected=17,integrations={'google':{'settings':{'enabled':True,'client_id':17}}})
    add('empty ID preserved',expected='',integrations={'google':{'settings':{'enabled':True,'client_id':''}}})
    add('empty enabled object',expected='',integrations={'google':{'settings':{'enabled':{}}}})
    add('empty integration list',integrations=[])
    add('false integration scalar',integrations=False)
    add('truthy integration list errors',error='AttributeError',expected=None,integrations=[1])
    add('null record errors',error='AttributeError',expected=None,integrations={'google':None})
    add('list record errors',error='AttributeError',expected=None,integrations={'google':[]})
    add('null login errors',error='AttributeError',expected=None,display={'login':None})
    add('list display errors',error='AttributeError',expected=None,display=[1])
    add('empty display list defaults',display=[])
    add('empty login defaults',display={'login':{}})
    add('false visibility object',expected='',display={'login':{'allowOauthLogin':{}}})
    add('truthy visibility string',display={'login':{'allowOauthLogin':'false'}})
    add('visibility null',expected='',display={'login':{'allowOauthLogin':None}})
    add('other provider ignored',integrations={'apple':None})
    add('array of pairs',expected='paired',integrations={'google':{'settings':[['enabled',True],['client_id','paired']]}})
    add('invalid settings list',expected=None,error='TypeError',integrations={'google':{'settings':[1]}})
    add('invalid settings scalar',expected=None,error='TypeError',integrations={'google':{'settings':True}})
    assert len(cases)==33

    fixture=work/'social-configuration.json'
    fixture.write_text(json.dumps({'cases':cases}));fixture.chmod(0o600)
    env={**tool_env,'ASSOZETA_SOCIAL_CONFIGURATION_FIXTURE':str(fixture),
         'ASSOZETA_ACCOUNT_TEST_DSN':f'host={work} port=5432 user=contracts dbname=assozeta_contract_accounts sslmode=disable'}
    def go(name):
        subprocess.run(['go','test','-race','-timeout=2m','-tags=socialconfigintegration','./internal/service','-run','^'+name+'$'],
                       cwd=ROOT/'BE_GO',env=env,check=True,timeout=150)
    def snapshot():
        return {m._meta.label:list(m._base_manager.order_by(m._meta.pk.name).values())
                for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
    initial=snapshot()
    try:
        with override_settings(SOCIAL_AUTH_GOOGLE_OAUTH2_KEY='env-google',SOCIAL_AUTH_APPLE_ID_CLIENT='env-apple'):
            for provider,expected in [('google','env-google'),('apple','env-apple')]:
                with CaptureQueriesContext(connection) as queries:
                    assert provider_client_id(provider)==expected
                assert len(queries)==1
            go('TestSocialConfigurationAbsentPostgres')
            first=InstanceConfiguration.objects.create(id=760001,domain='social-config-first.example.test',name='Social configuration',
                    google_client_id='ignored-legacy-google',apple_client_id='ignored-legacy-apple')
            original=InstanceConfiguration.objects.values('self_hosted','integration_settings','display_settings').get(pk=first.pk)
            for size in (1,16,128,512):
                existing=InstanceConfiguration.objects.count()
                InstanceConfiguration.objects.bulk_create([InstanceConfiguration(id=760001+n,domain=f'social-config-{n}.example.test',name='Later',
                    self_hosted=True,integration_settings={'google':{'settings':{'enabled':True,'client_id':'wrong-later'}}}) for n in range(existing,size)])
                before=snapshot()
                assert provider_client_id('google')=='env-google'
                go('TestSocialConfigurationBatchPostgres')
                assert snapshot()==before,'configuration reads wrote data'
            for case in cases:
                InstanceConfiguration.objects.filter(pk=first.pk).update(self_hosted=case['hosted'],integration_settings=case['integrations'],display_settings=case['display'])
                with CaptureQueriesContext(connection) as queries:
                    try: result=provider_client_id(case['provider'])
                    except (KeyError,AttributeError,TypeError,ValueError) as error:
                        assert type(error).__name__==case['error'],(case['name'],type(error).__name__)
                    else:
                        assert case['error'] is None and result==case['expected'],(case['name'],result)
                assert len(queries)==1,(case['name'],'query count')
            InstanceConfiguration.objects.filter(pk=first.pk).update(**original)
            before=snapshot()
            go('TestSocialConfigurationCasesPostgres')
            assert snapshot()==before,'Go configuration transitions were not restored'
        print(f'Django/Go: {len(cases)} social configuration cases and 1/16/128/512-row reads passed',flush=True)
    finally:
        InstanceConfiguration.objects.filter(pk__gte=760001,pk__lte=760512).delete()
        fixture.unlink(missing_ok=True)
    assert snapshot()==initial,'configuration fixture changed unrelated managed tables'
    return len(cases)
