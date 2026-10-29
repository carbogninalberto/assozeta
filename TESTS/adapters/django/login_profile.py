"""Explicit UserAuthSerializer contracts on disposable PostgreSQL accounts.

This is the base login profile, not the complete JWTTokenService response or a
portable HTTP proof. Expected fields are written here, not copied from Django.
"""
import datetime
import json
import subprocess
from unittest.mock import patch

from .accounts import ROOT
from .scoped_resources import uid

NOW = datetime.datetime(2026, 8, 1, 12, tzinfo=datetime.timezone.utc)


def run(work, log, tool_env):
    from django.apps import apps
    from django.db import connection
    from auditlog.context import disable_auditlog
    from application.models import User, SportAssociation
    from application.models.user_models import UsersOnboarding
    from application.serializers.auth_serializers import UserAuthSerializer
    from instance.models import InstanceConfiguration
    from rest_framework.renderers import JSONRenderer

    cases = []
    def add(name, *, role=2, superuser=False, active=True, deleted=False, association=False,
            association_values=None, profile_values=None, expected_values=None, joined=NOW-datetime.timedelta(days=60),
            provenance=None, complete=False, self_hosted=True, foreign_config=False,
            onboarding=0, google_json_null=False, outcome=''):
        number = 55000 + len(cases)
        values = profile_values or {}
        with disable_auditlog():
            user = User.original_objects.create(user_id=uid(number), username=f'PROFILE-{number}',
                email=f'profile-{number}@example.test', first_name='First', last_name='Last', role=role,
                is_superuser=superuser, is_active=active, deleted=deleted, date_joined=joined,
                password='!synthetic-profile-only', **values)
            assoc = SportAssociation.original_objects.create(sport_association_id=uid(number+1000), user=user,
                logo='fixture-logo', denomination='Fixture association', tax_code='FIXTURE',
                **(association_values or {})) if association else None
            if provenance:
                InstanceConfiguration.objects.bulk_create([InstanceConfiguration(
                    domain=f'profile-{number}.example.test', name='Synthetic profile fixture',
                    setup_provenance=provenance, self_hosted=self_hosted,
                    primary_association=None if foreign_config else assoc,
                    onboarding_completed_at=NOW if complete else None)])
            UsersOnboarding.objects.bulk_create([UsersOnboarding(users_onboarding_id=uid(100000+number*1000+i),
                user=user, show=False, create_membership=True, view_membership=True,
                approve_payment=False, download_invoice=True, view_collaborators=False, view_settings=True)
                for i in range(onboarding)])
        if google_json_null:
            with connection.cursor() as cursor:
                cursor.execute("UPDATE bakney_user SET integration_google_credentials='null'::jsonb WHERE user_id=%s", [user.pk])
        expected = {
            'user_id':str(user.pk), 'can_impersonate':active and not deleted and (superuser or role==1),
            'first_name':'First', 'last_name':'Last', 'username':user.username, 'email':user.email,
            'avatar_image':None, 'collaborator_role':1, 'collaborator_permissions':None,
            'google_sync_enabled':False, 'dashboard_layout':None, 'full_installments_plan':False,
            'show_zero_payments':False, 'dark_mode':False, 'medical_certificate_notifications':True,
            'hide_category_name':False, 'phone':None, 'subscription_duration_equal_sport_year':True,
            'balance_sheet_start_day':1, 'balance_sheet_start_month':1, 'balance_sheet_year':1,
            'subscription_duration':2, 'membership_duration':2, 'membership_starting_number':0,
            'default_membership_type':'', 'subscription_start_day':1, 'subscription_start_month':1,
            'custom_end_date':False, 'subscription_end_day':31, 'subscription_end_month':12,
            'lead_sport_association_role':None, 'lead_sport_association_size':None,
            'lead_sport_market_channel':None, 'requires_welcome':False, 'onboarding':None,
            'default_payment_category':None, 'default_payment_category_courses':None,
        }
        expected.update(expected_values or {})
        if onboarding==1 and role==1 and not superuser:
            expected['onboarding'] = {'users_onboarding_id':uid(100000+number*1000), 'user':str(user.pk),
                'show':False, 'create_membership':True, 'view_membership':True, 'approve_payment':False,
                'download_invoice':True, 'view_collaborators':False, 'view_settings':True}
        cases.append({'name':name,'id':str(user.pk),'expected':expected,'outcome':outcome,
            'queries':2 if role==1 and not superuser and not outcome else 1})

    add('athlete defaults')
    add('collaborator base profile without association', role=3)
    add('administrator base profile without association', role=1, superuser=True)
    add('inactive owner cannot impersonate', role=1, active=False, association=True)
    add('inactive administrator cannot impersonate', superuser=True, active=False)
    add('deleted account excluded', deleted=True, outcome='account_missing')
    add('missing owner association', role=1, outcome='association_missing')
    add('deleted owner association', role=1, association=True, association_values={'deleted':True}, outcome='association_missing')
    add('legacy complete owner', role=1, association=True)
    # Fixtures use explicit model updates for alternative logo/tax/name values.
    for field in ('logo','denomination','tax_code'):
        for value in ('', None):
            # denomination and tax_code may be non-null at schema level: the null
            # case is only supported for fields whose schema actually allows it.
            if value is None and not SportAssociation._meta.get_field(field).null:
                continue
            add(f'legacy missing {field} {value!r}', role=1, association=True, expected_values={'requires_welcome':True})
            SportAssociation.original_objects.filter(user_id=cases[-1]['id']).update(**{field:value})
    for field in ('balance_sheet_start_day','balance_sheet_start_month'):
        add(f'legacy zero {field}',role=1,association=True,profile_values={field:0},expected_values={field:0,'requires_welcome':True})
    add('recent owner missing lead fields',role=1,association=True,joined=NOW-datetime.timedelta(days=29),expected_values={'requires_welcome':True})
    add('exact thirty-day boundary',role=1,association=True,joined=NOW-datetime.timedelta(days=30))
    lead={'lead_sport_association_role':'President','lead_sport_association_size':'20','lead_sport_market_channel':'Referral'}
    add('recent complete lead fields',role=1,association=True,joined=NOW,profile_values=lead,expected_values=lead)
    for field in lead:
        values={**lead,field:''}
        add(f'recent empty {field}',role=1,association=True,joined=NOW,profile_values=values,expected_values={**values,'requires_welcome':True})
    add('fresh setup incomplete overrides legacy',role=1,association=True,provenance='fresh',expected_values={'requires_welcome':True})
    add('fresh setup completed overrides recent lead',role=1,association=True,provenance='fresh',complete=True,joined=NOW)
    add('import setup overrides recent lead',role=1,association=True,provenance='import',joined=NOW)
    add('legacy setup uses legacy welcome',role=1,association=True,provenance='legacy',joined=NOW,expected_values={'requires_welcome':True})
    add('unknown provenance uses legacy welcome',role=1,association=True,provenance='historical',joined=NOW,expected_values={'requires_welcome':True})
    add('non self-hosted setup ignored',role=1,association=True,provenance='import',self_hosted=False,joined=NOW,expected_values={'requires_welcome':True})
    add('foreign primary association ignored',role=1,association=True,provenance='import',foreign_config=True,joined=NOW,expected_values={'requires_welcome':True})
    for first,second,welcome in [('import','fresh',False),('fresh','import',True)]:
        add(f'first instance row wins {first}',role=1,association=True,provenance=first,expected_values={'requires_welcome':welcome})
        association=SportAssociation.original_objects.get(user_id=cases[-1]['id'])
        InstanceConfiguration.objects.bulk_create([InstanceConfiguration(
            domain=f'second-{len(cases)}.example.test',name='Synthetic duplicate instance',
            primary_association=association,setup_provenance=second,self_hosted=True)])
    for count in (1,2,128,512):
        add(f'onboarding count {count}',role=1,association=True,onboarding=count)
    add('athlete onboarding hidden',onboarding=1)
    add('administrator onboarding hidden',role=1,superuser=True,onboarding=1)
    add('SQL NULL Google credentials')
    add('JSON null Google credentials',google_json_null=True)
    for label, value in [('empty object',{}),('empty list',[]),('false',False),('empty string',''),('serialized JSON','{"token":"synthetic-unused"}')]:
        add(f'Google credentials {label}',profile_values={'integration_google_credentials':value},expected_values={'google_sync_enabled':True})
    unusual={'avatar_image':'data:unmodified-athlete-avatar','collaborator_role':5,
        'collaborator_permissions':{'association.read':True,'large':9007199254740993},
        'dashboard_layout':['chart',{'order':9007199254740993}], 'full_installments_plan':True,
        'show_zero_payments':True, 'dark_mode':True, 'medical_certificate_notifications':False,
        'hide_category_name':True, 'phone':'', 'subscription_duration_equal_sport_year':False,
        'balance_sheet_start_day':17, 'balance_sheet_start_month':7, 'balance_sheet_year':4,
        'subscription_duration':5, 'membership_duration':3, 'membership_starting_number':-10,
        'default_membership_type':None, 'subscription_start_day':3, 'subscription_start_month':9,
        'custom_end_date':True, 'subscription_end_day':28, 'subscription_end_month':2,
        **lead}
    add('all persisted preferences and exact JSON numbers',profile_values=unusual,expected_values=unusual)
    add('historical scalar JSON preferences',profile_values={'collaborator_permissions':'legacy','dashboard_layout':False},expected_values={'collaborator_permissions':'legacy','dashboard_layout':False})

    from application.models.payment_models import PaymentCategory
    with disable_auditlog():
        category=PaymentCategory.objects.create(payment_category_id=uid(59000),name='Synthetic deleted category',deleted=True)
    add('category IDs retained without dereferencing',profile_values={'default_payment_category':category,'default_payment_category_courses':category},
        expected_values={'default_payment_category':str(category.pk),'default_payment_category_courses':str(category.pk)})

    def snapshot():
        return {m._meta.label:list(m._base_manager.order_by('pk').values()) for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
    before=snapshot()
    with patch('application.serializers.auth_serializers.timezone.now', return_value=NOW):
        for case in cases:
            outcome=''
            try:
                user=User.objects.get(pk=case['id'])
                actual=json.loads(JSONRenderer().render(UserAuthSerializer(user).data))
            except User.DoesNotExist:
                outcome='account_missing'
            except SportAssociation.DoesNotExist:
                outcome='association_missing'
            else:
                assert actual==case['expected'], f"{case['name']}: Django profile mismatch: {sorted(k for k in actual if actual[k]!=case['expected'].get(k))}"
            assert outcome==case['outcome'], f"{case['name']}: Django outcome differs"
    # A real PostgreSQL permission failure is caught only inside get_onboarding.
    # Restrict this synthetic role to the relations needed for the base profile.
    with connection.cursor() as cursor:
        cursor.execute('CREATE ROLE profile_onboarding_denied NOLOGIN')
        cursor.execute('GRANT USAGE ON SCHEMA public TO profile_onboarding_denied')
        cursor.execute('GRANT SELECT ON bakney_user, application_sportassociation, instance_instanceconfiguration TO profile_onboarding_denied')
    denial_case=next(case for case in cases if case['name']=='onboarding count 1')
    with patch('application.serializers.auth_serializers.timezone.now', return_value=NOW):
        with connection.cursor() as cursor:
            cursor.execute('SET ROLE profile_onboarding_denied')
        try:
            user=User.objects.get(pk=denial_case['id'])
            actual=json.loads(JSONRenderer().render(UserAuthSerializer(user).data))
            assert actual=={**denial_case['expected'],'onboarding':None}, 'Django onboarding permission failure changed profile behavior'
        finally:
            with connection.cursor() as cursor:
                cursor.execute('RESET ROLE')
    print('Django: real onboarding permission failure preserves base profile with null onboarding',flush=True)
    assert snapshot()==before, 'Django base profile wrote database state'
    print(f'Django: {len(cases)} explicit base login profile cases passed',flush=True)
    path=work/'login-profile.json'
    path.write_text(json.dumps({'now':NOW.isoformat(),'cases':cases}));path.chmod(0o600)
    try:
        subprocess.run(['go','test','-race','-tags=profileintegration','./internal/service','-run','^TestLoginProfile'],
            cwd=ROOT/'BE_GO',check=True,timeout=120,env={**tool_env,'ASSOZETA_LOGIN_PROFILE_FIXTURE':str(path),
                'ASSOZETA_ACCOUNT_TEST_DSN':f'host={work} port=5432 user=contracts dbname=assozeta_contract_accounts sslmode=disable'})
        assert snapshot()==before, 'Go base profile wrote database state'
    finally:
        path.unlink(missing_ok=True)
    return len(cases)
