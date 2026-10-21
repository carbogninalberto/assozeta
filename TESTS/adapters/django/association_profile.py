"""Explicit association serializer contracts, separate from login mutations."""
import datetime
import json
import subprocess
from decimal import Decimal
from unittest.mock import patch

from .accounts import ROOT
from .scoped_resources import uid
from .login_profile import NOW


def run(work, log, tool_env):
    from django.apps import apps
    from django.db import connection, ProgrammingError
    from auditlog.context import disable_auditlog
    from application.models import User, SportAssociation, Group
    from application.serializers.auth_serializers import SportAssociationSerializer
    from rest_framework.renderers import JSONRenderer

    cases=[]
    def add(name, *, values=None, expected_values=None, days=60, delta=datetime.timedelta(),
            owner_deleted=False, owner_active=True, deleted=False, missing=False, groups=0):
        index=len(cases)
        owner_id=uid(60000+index)
        association_id=uid(61000+index)
        with disable_auditlog():
            owner=User.original_objects.create(user_id=owner_id,username=f'ASSOCIATION-PROFILE-{index}',
                email=f'association-profile-{index}@example.test',password='!synthetic-unused',role=1,
                is_active=owner_active,deleted=owner_deleted,date_joined=NOW-datetime.timedelta(days=days)+delta)
            if not missing:
                association=SportAssociation.original_objects.create(sport_association_id=association_id,user=owner,
                    **{'denomination':'Fixture ASD','tax_code':'12345678901','additional_sections':[], 'deleted':deleted,**(values or {})})
                # Descending UUIDs and unsorted labels catch an invented ordering.
                Group.objects.bulk_create([Group(group_id=uid(100000000+index*10000+groups-i),
                    name=f'Group {groups-i}',description='fixture description',sport_association=association) for i in range(groups)])
        expected={
            'denomination':'Fixture ASD','tax_code':'12345678901','address':None,'address_cap':None,'address_city':None,
            'regulation':None,'demand':None,'logo':None,'subscription_fee':'0.00','multiple_subscription_fee':False,
            'subscription_fee_plans':[],'membership_fee':None,'multiple_membership_fee':False,'membership_fee_plans':[],
            'custom_subscription_form_data':None,'document_header':None,'invoice_footer':None,'enable_quotes_management':True,
            'configuration':{'mandatory_phone':False,'mandatory_email':False,'mandatory_signature':False},
            'reviewed':False,'affiliate_code':None,'sport_association_id':association_id,'additional_sections':[],
            'president_signature':None,'stamp':None,'president_first_name':None,'president_last_name':None,
            'federation':None,'enroll_number':None,'sport':None,'enabled_for':['associate','associate-membership','membership'],
            'checkout_info':None,'additional_fields':None,'stripe_available_methods':['card','sepa_debit'],
            'iban':None,'website':None,'abbreviated':None,'vat_number':None,'whatsapp':None,'invoice_template':'invoice.html',
            'subscription_template':'subscription.html','extra_text_invoices':None,'imported_from_associami':False,
            'groups':[{'label':f'Group {groups-i}','value':uid(100000000+index*10000+groups-i)} for i in range(groups)],
            'show_regulation_to_members':True,'show_regulation_to_both':True,'show_regulation_to_athletes':True,
            'show_demand_to_members':True,'show_demand_to_both':True,'show_demand_to_athletes':True,
        }
        expected.update(expected_values or {})
        cases.append({'name':name,'owner':owner_id,'association':association_id,'expected':expected,
            'outcome':'missing' if missing or deleted else '', 'queries':1 if missing or deleted else 2})
    add('stored defaults with explicit empty sections')
    add('deleted association excluded',deleted=True)
    add('missing association',missing=True)
    add('inactive owner remains serializable',owner_active=False)
    add('deleted owner remains serializable',owner_deleted=True)
    add('recent owner forces reviewed',days=4,expected_values={'reviewed':True})
    add('exact five-day boundary',days=5)
    add('microsecond inside five-day boundary',days=5,delta=datetime.timedelta(microseconds=1),expected_values={'reviewed':True})
    add('persisted reviewed on old owner',values={'reviewed':True},expected_values={'reviewed':True})
    for number in ('0.01','-0.01','9999999.99','-9999999.99','12345.67'):
        add(f'exact numeric {number}',values={'subscription_fee':Decimal(number),'membership_fee':Decimal(number)},
            expected_values={'subscription_fee':number,'membership_fee':number})
    for number,expected in [('1.005','1.01'),('-1.005','-1.01')]:
        add(f'PostgreSQL numeric rounding {number}',expected_values={'subscription_fee':expected,'membership_fee':expected})
        with connection.cursor() as cursor:
            cursor.execute('UPDATE application_sportassociation SET subscription_fee=%s::numeric,membership_fee=%s::numeric WHERE sport_association_id=%s',
                [number,number,cases[-1]['association']])
    rich={'address':'Via Test 1','address_cap':'00100','address_city':'Roma','regulation':'<p>Regole</p>','demand':'<p>Domanda</p>',
        'logo':'data:unchanged-image','multiple_subscription_fee':True,'subscription_fee_plans':[{'amount':'123.45','id':9007199254740993}],
        'multiple_membership_fee':True,'membership_fee_plans':[{'name':'Annual','amount':5}],
        'custom_subscription_form_data':{'field':'value'},'document_header':'Header','invoice_footer':'Footer',
        'enable_quotes_management':False,'configuration':{'custom':True},'affiliate_code':'ABC12345',
        'additional_sections':[{'name':'Custom','text':'<p>Test</p>','show_to_members':True}],
        'president_signature':'signature-data','stamp':'stamp-data','president_first_name':'Nome','president_last_name':'Cognome',
        'federation':'Federazione','enroll_number':'42','sport':'Sport','enabled_for':['membership'],'checkout_info':'Checkout',
        'additional_fields':[{'name':'test'}],'stripe_available_methods':['card'],'iban':'IT-SYNTHETIC','website':'https://example.test',
        'abbreviated':'ASD','vat_number':'SYNTHETIC','whatsapp':'+3900000000','invoice_template':'legacy-custom.html',
        'subscription_template':'legacy-subscription.html','extra_text_invoices':'Extra','imported_from_associami':True,
        'show_regulation_to_members':False,'show_regulation_to_both':False,'show_regulation_to_athletes':False,
        'show_demand_to_members':False,'show_demand_to_both':False,'show_demand_to_athletes':False}
    add('all persisted association preferences',values=rich,expected_values=rich)
    nullable={key:None for key in ('enabled_for','stripe_available_methods','invoice_template','subscription_template')}
    add('nullable preferences',values=nullable,expected_values=nullable)
    scalar={'additional_sections':False,'configuration':'legacy','subscription_fee_plans':{'id':9007199254740993},
        'membership_fee_plans':True,'enabled_for':0,'stripe_available_methods':'legacy','additional_fields':False}
    add('historical scalar JSON preferences',values=scalar,expected_values=scalar)
    for count in (1,16,128,512):
        add(f'groups {count}',groups=count)

    def snapshot():
        return {m._meta.label:list(m._base_manager.order_by('pk').values()) for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
    before=snapshot()
    with patch('application.serializers.auth_serializers.timezone.now',return_value=NOW):
        for case in cases:
            try:
                association=SportAssociation.objects.get(user_id=case['owner'])
            except SportAssociation.DoesNotExist:
                assert case['outcome']=='missing',f"{case['name']}: unexpected missing association"
            else:
                actual=json.loads(JSONRenderer().render(SportAssociationSerializer(association).data))
                assert not case['outcome'] and actual==case['expected'],f"{case['name']}: Django association differs in {sorted(k for k in actual if actual[k]!=case['expected'].get(k))}"
    with connection.cursor() as cursor:
        cursor.execute('CREATE ROLE association_groups_denied NOLOGIN')
        cursor.execute('GRANT USAGE ON SCHEMA public TO association_groups_denied')
        cursor.execute('GRANT SELECT ON bakney_user, application_sportassociation TO association_groups_denied')
        cursor.execute('SET ROLE association_groups_denied')
    try:
        association=SportAssociation.objects.get(user_id=cases[0]['owner'])
        try:
            SportAssociationSerializer(association).data
        except ProgrammingError as error:
            assert getattr(error.__cause__,'sqlstate',getattr(error.__cause__,'pgcode',None))=='42501', 'unexpected association failure'
        else:
            raise AssertionError('Django suppressed required group lookup failure')
    finally:
        with connection.cursor() as cursor:
            cursor.execute('RESET ROLE')
    print('Django: required group permission failure propagates',flush=True)
    assert snapshot()==before,'Django association serialization wrote database state'
    print(f'Django: {len(cases)} explicit association profile cases passed',flush=True)
    path=work/'association-profile.json';path.write_text(json.dumps({'now':NOW.isoformat(),'cases':cases}));path.chmod(0o600)
    try:
        subprocess.run(['go','test','-race','-tags=associationprofileintegration','./internal/service','-run','^TestAssociationProfile'],
            cwd=ROOT/'BE_GO',check=True,timeout=120,env={**tool_env,'ASSOZETA_ASSOCIATION_PROFILE_FIXTURE':str(path),
                'ASSOZETA_ACCOUNT_TEST_DSN':f'host={work} port=5432 user=contracts dbname=assozeta_contract_accounts sslmode=disable'})
        assert snapshot()==before,'Go association serialization changed database state'
    finally:
        path.unlink(missing_ok=True)
    return len(cases)
