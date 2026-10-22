"""Explicit response shapes, including concrete login image transformations.

The real response builder and Go services run on one disposable database. Image
dispatch still has declared format gaps; HTTP admission and final session delivery
remain separate open gates.
"""
import datetime
import json
import io
import subprocess
from unittest.mock import patch
from .accounts import ROOT
from .scoped_resources import uid
from .login_profile import NOW

TOKENS={'access_token':'synthetic-response-access','refresh_token':'synthetic-response-refresh','expires_in':14400,'token_type':'Bearer'}
FEATURES=['Iscrizione per famiglia','Iscrizioni per Enti e Federazioni','Iscrizioni multiple per federazioni']
FLAGS=['FAMILY_REGISTRATION_ENABLED','FEDERATION_REGISTRATION_ENABLED','MULTIPLE_REGISTRATION_ENABLED']


def base_profile(id, role, admin, avatar, welcome):
    return {'user_id':id,'first_name':'First','last_name':'Last','username':f'RESPONSE-{id}',
        'email':'response@example.test','avatar_image':avatar,'collaborator_role':1,'collaborator_permissions':None,
        'google_sync_enabled':False,'dashboard_layout':None,'full_installments_plan':False,'show_zero_payments':False,
        'dark_mode':False,'medical_certificate_notifications':True,'hide_category_name':False,'phone':None,
        'subscription_duration_equal_sport_year':True,'balance_sheet_start_day':1,'balance_sheet_start_month':1,
        'balance_sheet_year':1,'subscription_duration':2,'membership_duration':2,'membership_starting_number':0,
        'default_membership_type':'','subscription_start_day':1,'subscription_start_month':1,'custom_end_date':False,
        'subscription_end_day':31,'subscription_end_month':12,'lead_sport_association_role':None,
        'lead_sport_association_size':None,'lead_sport_market_channel':None,'default_payment_category':None,
        'default_payment_category_courses':None,'can_impersonate':admin or role==1,'requires_welcome':welcome,'onboarding':None}


def association_profile(id, regulation, demand, logo):
    result={'sport_association_id':id,'denomination':'Fixture ASD','tax_code':'12345678901','address':None,'address_cap':None,'address_city':None,
        'regulation':regulation,'demand':demand,'logo':logo,'subscription_fee':'0.00','multiple_subscription_fee':False,
        'subscription_fee_plans':[],'membership_fee':None,'multiple_membership_fee':False,'membership_fee_plans':[],
        'custom_subscription_form_data':None,'document_header':None,'invoice_footer':None,'enable_quotes_management':True,
        'configuration':{'mandatory_phone':False,'mandatory_email':False,'mandatory_signature':False},'reviewed':False,
        'affiliate_code':None,'additional_sections':[],'president_signature':None,'stamp':None,'president_first_name':None,
        'president_last_name':None,'federation':None,'enroll_number':None,'sport':None,'enabled_for':['associate','associate-membership','membership'],
        'checkout_info':None,'additional_fields':None,'stripe_available_methods':['card','sepa_debit'],'iban':None,'website':None,
        'abbreviated':None,'vat_number':None,'whatsapp':None,'invoice_template':'invoice.html','subscription_template':'subscription.html',
        'extra_text_invoices':None,'imported_from_associami':False,'groups':[],
        'show_regulation_to_members':True,'show_regulation_to_both':True,'show_regulation_to_athletes':True,
        'show_demand_to_members':True,'show_demand_to_both':True,'show_demand_to_athletes':True,
        'membership_card_configuration':{'emit_only_on_approval':False,'customized_template':None}}
    if regulation in (None,'') or demand in (None,''): result['empty_sections']=True
    return result


def run(work, log, tool_env):
    from django.apps import apps
    from django.db import connection, ProgrammingError
    from django.test import override_settings
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from application.models import User, SportAssociation, SportAssociationMembershipCardConfiguration, Instructor
    from application.models.audit_models import AuditLogIndex
    from application.services.jwt_token_service import JWTTokenService
    from rest_framework.renderers import JSONRenderer
    import environ
    boolean_cases=[('true',True),(' YES ',True),('on',True),('ok',True),('Y',True),('1',True),('-2',True),('+20',True),
        ('1_0',True),('٠١',True),(' １２ ',True),('\x1c1\x1f',True),('',False),('false',False),('off',False),('no',False),
        ('0',False),('-0',False),('000_000',False),('٠٠',False),('1.0',False),('0x1',False),('1__0',False),('１_',False),
        ('\x1c2',False),('enabled',False),('9'*4300,True),('9'*4301,False)]
    for value,expected in boolean_cases:
        with patch.object(environ.Env,'ENVIRON',{name:value for name in FLAGS}):
            env=environ.Env()
            assert [env.bool(name,default) for name,default in zip(FLAGS,(True,False,False))]==[expected]*3, 'explicit Django boolean expectation differs'
    with patch.object(environ.Env,'ENVIRON',{FLAGS[0]:'$MISSING',FLAGS[1]:'$$FEATURE','FEATURE':'$ENABLED','ENABLED':'-2'}):
        env=environ.Env(); assert env.bool(FLAGS[0],True) and env.bool(FLAGS[1],False), 'Django proxy/default differs'
    file_cases=[
        {'name':'missing proxy default','lines':'FAMILY_REGISTRATION_ENABLED=$MISSING\n','expected':[True,False,False]},
        {'name':'later references','lines':'FAMILY_REGISTRATION_ENABLED=$LATER\nLATER=0\nFEDERATION_REGISTRATION_ENABLED=$YES\nYES=ok\n','expected':[False,True,False]},
        {'name':'literal comment','lines':'FAMILY_REGISTRATION_ENABLED=true # literal\n','expected':[False,False,False]},
        {'name':'invalid key whitespace','lines':' FAMILY_REGISTRATION_ENABLED=false\n','expected':[True,False,False]},
        {'name':'quoted words','lines':"FAMILY_REGISTRATION_ENABLED='on'\nFEDERATION_REGISTRATION_ENABLED=\"yes\"\n",'expected':[True,True,False]},
        {'name':'literal newline escape','lines':'FAMILY_REGISTRATION_ENABLED="\\ntrue"\n','expected':[False,False,False]},
        {'name':'last duplicate wins','lines':'FAMILY_REGISTRATION_ENABLED=0\nFAMILY_REGISTRATION_ENABLED=1\n','expected':[True,False,False]},
        {'name':'process reference precedence','lines':'FAMILY_REGISTRATION_ENABLED=$SWITCH\nSWITCH=1\n','process':{'SWITCH':'0'},'expected':[False,False,False]},
    ]
    for case in file_cases:
        with patch.object(environ.Env,'ENVIRON',dict(case.get('process',{}))):
            environ.Env.read_env(io.StringIO(case['lines']))
            env=environ.Env()
            assert [env.bool(name,default) for name,default in zip(FLAGS,(True,False,False))]==case['expected'], 'Django dotenv flag semantics differ'
    with patch.object(environ.Env,'ENVIRON',{FLAGS[0]:'$FAMILY_REGISTRATION_ENABLED'}):
        try: environ.Env().bool(FLAGS[0],True)
        except RecursionError: pass
        else: raise AssertionError('Django accepted cyclic flag proxy')
    cases=[]
    with disable_auditlog():
        foreign_owner=User.original_objects.create(user_id=uid(90000),username='RESPONSE-FOREIGN',password='!unused',role=1)
        SportAssociation.original_objects.create(sport_association_id=uid(91000),user=foreign_owner,denomination='Foreign association')
    def add(name, *, role=1, admin=False, flags=(True,False,False), tables=None, json_null=False, temporary=True,
            regulation=None,demand=None,logo=None,avatar=None,instructors=0,missing_owner=False,missing_association=False,deleted_association=False,created=False,avatar_expected=None,logo_expected=None):
        n=len(cases);id=uid(90100+n);owner_id=uid(90300+n) if role==3 else id;association_id=uid(91100+n)
        with disable_auditlog():
            owner=None
            if role==3 and not missing_owner:
                owner=User.original_objects.create(user_id=owner_id,username=f'RESPONSE-OWNER-{n}',password='!unused',role=1,
                    date_joined=NOW-datetime.timedelta(days=60),tables_settings={'owner':'must not leak'},temporary_invoice_deletion=not temporary)
            user=User.original_objects.create(user_id=id,username=f'RESPONSE-{id}',email='response@example.test',first_name='First',last_name='Last',
                password='!synthetic-response-only',role=role,is_superuser=admin,connected_user=owner,
                date_joined=NOW-datetime.timedelta(days=60),avatar_image=avatar,temporary_invoice_deletion=temporary,tables_settings={} if tables is None else tables)
            association=None
            if role in (1,3) and not admin and not missing_owner and not missing_association:
                association=SportAssociation.original_objects.create(sport_association_id=association_id,user=owner or user,
                    denomination='Fixture ASD',tax_code='12345678901',additional_sections=[],regulation=regulation,demand=demand,logo=logo,deleted=deleted_association)
                if not created: SportAssociationMembershipCardConfiguration.objects.create(sport_association=association)
            Instructor.objects.bulk_create([Instructor(instructor_id=uid(300000000+n*10000+instructors-i),user=foreign_owner if i==instructors-1 else owner or user,
                first_name='Instructor',last_name='Fixture',associated_user_id=user.pk,draft=True) for i in range(instructors)])
            # A record owned by the target's association but linked to someone
            # else must not be selected by this login metadata query.
            Instructor.objects.create(instructor_id=uid(400000000+n),user=owner or user,first_name='Unrelated',last_name='Fixture',associated_user_id=foreign_owner.pk)
        if json_null:
            with connection.cursor() as cursor: cursor.execute("UPDATE bakney_user SET tables_settings='null'::jsonb WHERE user_id=%s",[user.pk])
        outcome='role' if role>3 else 'association' if not admin and role in (1,3) and (missing_owner or missing_association or deleted_association) else ''
        expected=None
        if not outcome:
            expected={**TOKENS,'role':'administrator' if admin else 'athlete' if role==2 else 'association',
                'user_data':base_profile(id,role,admin,avatar,role==1 and not admin and logo in (None,''))}
            if admin:
                expected['user_data']['is_superuser']=True;expected['requires_welcome']=False
            elif role in (1,3):
                if role==3:
                    expected['requires_welcome']=False
                    if instructors: expected['user_data']['instructor_id']=uid(300000000+n*10000+1)
                expected['user_data']['sport_association']=association_profile(association_id,regulation,demand,logo)
                expected['user_data']['temporary_invoice_deletion']=temporary
                expected['user_data']['preview_and_custom_features']=[{'name':label,'description':''} for enabled,label in zip(flags,FEATURES) if enabled]
                expected['tables_settings']=None if json_null else {} if tables is None else tables
                if avatar_expected is not None:expected['user_data']['avatar_image']=avatar_expected
                if logo_expected is not None and (regulation in (None,'') or demand in (None,'')):expected['user_data']['sport_association']['logo']=logo_expected
        queries=0 if outcome=='role' else 1 if admin or role in (0,2) or (role==1 and outcome) or missing_owner else 2 if outcome else 10 if created else 7
        cases.append({'name':name,'id':id,'owner':str(owner.pk) if owner else None,'role':role,'admin':admin,'flags':list(flags),
            'association':str(association.pk) if association else None,'created':created,'expected':expected,'outcome':outcome,'queries':queries,'instructors':instructors})
    add('athlete omits association fields',role=2,avatar='opaque-athlete-image',instructors=1)
    add('administrator returns before association',admin=True,avatar='opaque-admin-image',instructors=1)
    add('collaborator administrator needs no owner',role=3,admin=True,missing_owner=True)
    add('historical zero role label without augmentation',role=0)
    add('unsupported role fails before serialization',role=4)
    add('unsupported administrator role still fails',role=4,admin=True)
    for n in range(8): add(f'registration feature mask {n}',flags=tuple(bool(n&(1<<i)) for i in range(3)))
    add('collaborator inherits owner association, keeps own settings',role=3,tables={'target':9007199254740993},temporary=False,instructors=1)
    for count in (0,2,128,512): add(f'collaborator instructors {count}',role=3,instructors=count)
    add('association does not expose linked instructor',instructors=1)
    for label,value in [('array',[]),('false',False),('zero',0),('string','historical'),('large object',{'id':9007199254740993})]: add(f'tables settings {label}',tables=value)
    add('tables settings JSON null',json_null=True)
    add('existing complete sections preserve logo without processing',regulation='regulation',demand='demand',logo='opaque-unmodified-logo')
    add('empty regulation emits empty_sections',regulation='',demand='present')
    add('empty demand emits empty_sections',regulation='present',demand='')
    add('whitespace sections are present',regulation=' ',demand='\t')
    add('missing owner association',missing_association=True)
    add('deleted owner association',deleted_association=True)
    add('collaborator missing owner',role=3,missing_owner=True)
    add('collaborator missing association',role=3,missing_association=True)
    add('collaborator deleted association',role=3,deleted_association=True)
    add('default configuration is composed and persisted',created=True)
    from TESTS.adapters.django.login_images import vectors as image_vectors
    images={case['name']:case for case in image_vectors()}
    png=images['PNG alpha'];gif=images['GIF 3x2'];tga=images['TGA alpha'];gbr=images['GBR'];unsupported=images['unimplemented TIFF'];ppm=images['PPM 3x2'];collision=images['GBR precedes SUN']
    add('owner transforms avatar and logo before creating card',avatar=png['uri'],avatar_expected=png['expected'],logo=gif['uri'],logo_expected=gif['expected'],created=True)
    add('collaborator transforms own avatar and owner logo',role=3,instructors=1,avatar=tga['uri'],avatar_expected=tga['expected'],logo=gbr['uri'],logo_expected=gbr['expected'])
    add('athlete bypasses image conversion',role=2,avatar=unsupported['uri'])
    add('administrator bypasses image conversion',admin=True,avatar=unsupported['uri'])
    add('complete sections bypass unsupported logo',regulation='present',demand='present',logo=unsupported['uri'],avatar=ppm['uri'],avatar_expected=ppm['expected'])
    add('malformed image URIs remain unchanged',avatar='opaque,A',logo='literal-logo')
    add('GBR SUN collision retains original logo',logo=collision['uri'],logo_expected=collision['expected'])
    ico=images['ICO PNG frame']
    add('owner transforms ICO logo',logo=ico['uri'],logo_expected=ico['expected'])
    webp=images['WebP frame'];alpha=images['WebP alpha']
    add('owner transforms WebP avatar and logo',avatar=alpha['uri'],avatar_expected=alpha['expected'],logo=webp['uri'],logo_expected=webp['expected'])
    def snapshot():
        return {m._meta.label:list(m._base_manager.order_by('pk').values()) for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
    before=snapshot()
    def reset_created(case):
        if not case['created']:return
        config=SportAssociationMembershipCardConfiguration.objects.get(sport_association_id=case['association'])
        entry=LogEntry.objects.get(object_pk=str(config.pk))
        assert entry.action==0 and entry.changes_dict['sport_association']==['None',case['association']], 'composed configuration audit missing'
        index=AuditLogIndex.objects.get(log_entry=entry)
        assert str(index.sport_association_id)==case['association'] and index.resolution_path=='direct', 'composed configuration index missing'
        with disable_auditlog(): config.delete();entry.delete()
    with patch('application.serializers.auth_serializers.timezone.now',return_value=NOW):
        for case in cases:
            user=User.original_objects.get(pk=case['id'])
            with override_settings(**dict(zip(FLAGS,case['flags']))):
                try: actual=json.loads(JSONRenderer().render(JWTTokenService.build_login_response(user,TOKENS)))
                except IndexError: assert case['outcome']=='role', 'unexpected role failure'
                except SportAssociation.DoesNotExist: assert case['outcome']=='association', 'unexpected association failure'
                else:
                    assert not case['outcome'] and actual==case['expected'], f"{case['name']}: explicit response mismatch"
            reset_created(case)
    assert snapshot()==before, 'Django response composition changed undeclared state'
    # Instructor lookup is required only for collaborators. Rehearse a real
    # denied SELECT and ensure owners do not acquire that table dependency.
    with connection.cursor() as cursor:
        cursor.execute('CREATE ROLE login_instructor_denied NOLOGIN')
        cursor.execute('GRANT USAGE ON SCHEMA public TO login_instructor_denied')
        cursor.execute('GRANT SELECT ON ALL TABLES IN SCHEMA public TO login_instructor_denied')
        cursor.execute('REVOKE SELECT ON application_instructor FROM login_instructor_denied')
        cursor.execute('GRANT UPDATE ON application_sportassociation TO login_instructor_denied')
        cursor.execute('SET ROLE login_instructor_denied')
    try:
        with patch('application.serializers.auth_serializers.timezone.now',return_value=NOW):
            for case in (cases[6],cases[14]):
                try: JWTTokenService.build_login_response(User.original_objects.get(pk=case['id']),TOKENS)
                except ProgrammingError as exc:
                    assert case['role']==3 and getattr(exc.__cause__,'pgcode',getattr(exc.__cause__,'sqlstate',None))=='42501', 'wrong permission failure'
                else: assert case['role']==1, 'collaborator ignored required instructor read'
    finally:
        with connection.cursor() as cursor: cursor.execute('RESET ROLE')
    assert snapshot()==before, 'Django permission rehearsal changed state'
    print(f'Django: {len(cases)} explicit composed response cases, {len(boolean_cases)} boolean values, eight dotenv cases and required instructor denial passed; nine concrete image response cases included',flush=True)
    from TESTS.adapters.django.login_jpeg import build_codec
    codec=work/'response-codec';build_codec(codec,log,tool_env)
    path=work/'login-response.json';path.write_text(json.dumps({'now':NOW.isoformat(),'tokens':TOKENS,'cases':cases,'files':file_cases,'booleans':[{'value':v,'expected':e} for v,e in boolean_cases]}));path.chmod(0o600)
    try:
        subprocess.run(['go','test','-race','-tags=responseintegration','./internal/service','-run','^TestLoginResponsePostgres'],cwd=ROOT/'BE_GO',check=True,timeout=180,
            env={**tool_env,'ASSOZETA_LOGIN_JPEG_CODEC':str(codec),'ASSOZETA_LOGIN_RESPONSE_FIXTURE':str(path),'ASSOZETA_ACCOUNT_TEST_DSN':f'host={work} port=5432 user=contracts dbname=assozeta_contract_accounts sslmode=disable'})
        for case in cases:reset_created(case)
        assert snapshot()==before, 'Go response composition changed undeclared state'
    finally:path.unlink(missing_ok=True);codec.unlink(missing_ok=True)
    return len(cases)
