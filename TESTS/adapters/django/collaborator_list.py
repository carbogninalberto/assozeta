"""Independent Django-source-authored collaborator list/current invitation oracle.

No output is learned from a previous response. Unpaginated results, invitation
secrets and otherwise surprising included rows remain explicit baseline facts.
"""
from contextvars import ContextVar
from datetime import datetime, timezone
import copy
import hashlib
import json
from pathlib import Path
import threading
import subprocess
import unittest
from urllib.parse import quote
from uuid import UUID

ROOT = Path(__file__).resolve().parents[3]


def uid(number):
    return f'a1000000-0000-4000-8000-{number:012d}'


def run(work, log, tool_env, address, *, go_server_factory=None, output=None):
    from auditlog.context import disable_auditlog
    from django.apps import apps
    from django.core.asgi import get_asgi_application
    from django.core.cache import cache
    from django.db import connections
    from django.db.backends.signals import connection_created
    from application.models import User, SportAssociation, Associate
    from application.models.user_models import CollaborationInvites
    from application.impersonation import session_key
    from rest_framework_simplejwt.tokens import AccessToken
    from TESTS.contracts.test_collaborator_list import assert_case,MATRIX_CASE_COUNT,MATRIX_REQUIRED_NAMES,MATRIX_VERSION
    from TESTS.http_client import poll
    import redis
    import uvicorn

    go_requested = tool_env.get('TESTS_COLLABORATOR_LIST_GO') == '1'
    versioned = tool_env.get('TESTS_COLLABORATOR_LIST_VERSIONED') == '1'
    assert not versioned or go_requested
    output = Path(output) if output is not None else ROOT / 'quality-reports/go-migration/collaborator-list'
    output.mkdir(parents=True, exist_ok=True)
    report = {'success': False, 'cleanup_verified': False, 'workloads': [],
              'scope': 'Authored current-schema collaborator/invitation read; unpaginated baseline, no new privacy policy',
              'complete_collaborator_parity': False}
    result_file = output / 'http-results.json'
    result_file.write_text(json.dumps(report) + '\n')
    def hashes():
        paths = {p for p in (ROOT/'BE').rglob('*.py') if 'site-packages' not in p.parts}
        paths.update(ROOT/p for p in ('TESTS/adapters/django/collaborator_list.py',
            'TESTS/contracts/test_collaborator_list.py', 'TESTS/contracts/test_impersonation_management.py',
            'TESTS/adapters/django/accounts.py','TESTS/adapters/django/production_runtime.py','TESTS/adapters/django/settings.py','BE/Dockerfile','selfhost/.env.example', 'TESTS/http_client.py'))
        if go_requested or tool_env.get('TESTS_COLLABORATOR_LIST_REPOSITORY')=='1':
            paths.update(p for p in (ROOT/'BE_GO').rglob('*') if p.is_file() and p.suffix in ('.go','.mod','.sum','.c','.h','.pem'))
            paths.update(ROOT/p for p in ('TESTS/adapters/go/payment_category_server.py','TESTS/adapters/go/server.py',
                'TESTS/adapters/django/payment_info_stats.py','TESTS/adapters/django/login_jpeg.py'))
        if versioned:
            paths.update(ROOT/p for p in ('TESTS/adapters/caddy.py','selfhost/caddy/Caddyfile','UI/Dockerfile'))
        return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
    sources = hashes()
    def snapshot():
        return {m._meta.label:list(m._base_manager.order_by('pk').values()) for m in apps.get_models(include_auto_created=True)
                if m._meta.managed and not m._meta.proxy}
    host, port = address.split(':')
    raw = redis.Redis(host=host, port=int(port), db=1, password='synthetic-identity-only')
    def cache_snapshot():
        return {k:raw.get(k) for k in raw.scan_iter() if not k.startswith(b'assozeta:go:readiness:')}
    original, original_cache = snapshot(), cache_snapshot()
    created, session_ids, cases = [], [], []
    test = unittest.TestCase()
    cid_context = ContextVar('collaborator_list_cid', default=None)
    lock, observed, counts = threading.Lock(), [], {}
    def trace(execute, sql, params, many, context):
        cid = cid_context.get()
        if cid:
            with lock:
                count = counts.setdefault(cid, {'total':0, 'writes':0, 'invites':0, 'connected':0})
                count['total'] += 1
                count['writes'] += int(sql.lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')))
                count['invites'] += int('FROM "application_collaborationinvites"' in sql)
                count['connected'] += int('"bakney_user"."connected_user_id" =' in sql)
        return execute(sql, params, many, context)
    def attached(sender, connection, **kwargs):
        connection.execute_wrappers.append(trace)
    connection_created.connect(attached, weak=False)
    application = get_asgi_application()
    async def traced(scope, receive, send):
        cid = dict(scope.get('headers', [])).get(b'x-correlation-id', b'').decode('latin1')
        active = scope['type']=='http' and cid.startswith('collaborator-list-')
        row = {'case':cid, 'status':None}
        if active:
            with lock: observed.append(row)
        marker = cid_context.set(cid if active else None)
        async def sent(message):
            if active and message['type']=='http.response.start':
                with lock:row['status']=message['status']
            await send(message)
        try: await application(scope, receive, sent)
        finally: cid_context.reset(marker)
    server = uvicorn.Server(uvicorn.Config(traced,host='0.0.0.0' if versioned else '127.0.0.1',port=0,lifespan='off',log_level='critical'))
    thread = None
    def seed(model, number, **values):
        assert not model._base_manager.filter(pk=uid(number)).exists()
        row=model(pk=uid(number),**values);model._base_manager.bulk_create([row]);created.append((model,row.pk));return row
    def user(number, role, **values):
        return seed(User,number,username=f'collaborator-fixture-{number}',email=f'{number}@example.test',
                    password='!synthetic',role=role,first_name=f'First{number}',last_name='Fixture',**values)
    def token(person):
        value=AccessToken();value['user_id']=str(person.pk);return str(value)
    def payload(person, **overrides):
        return {'user_id':str(person.pk),'first_name':person.first_name,'last_name':person.last_name,
            'email':person.email,'phone':person.phone,'role':person.role,
            'connected_user':str(person.connected_user_id) if person.connected_user_id else None,
            'collaborator_role':person.collaborator_role,'collaborator_permissions':copy.deepcopy(person.collaborator_permissions),**overrides}
    def add(name, expected=None, *, actor=None, status=200, suffix='', headers=None, method='GET', body=None, family=None, gzip=False, compare=True):
        case={'name':'collaborator-list-'+name,'path':'/collaborators/list'+suffix,'method':method,
              'status':status,'headers':headers or {}}
        if actor is not None:case['token']=token(actor) if not isinstance(actor,str) else actor
        if compare:case['response']=expected
        if body is not None:case['body']=body
        if family is not None:case['family_queries']=family
        if gzip:case['gzip']=True
        cases.append(case)
    def replay(base,label,django,selected=None,negative=False):
        rows=[]
        if not negative:report['workloads'].append({'backend':label,'cases':rows})
        for case in cases if selected is None else selected:
            before=snapshot();before_cache=cache_snapshot()
            with lock:
                offset=len(observed); prior=dict(counts.get(case['name'],{'total':0,'writes':0,'invites':0,'connected':0}))
            status,_=assert_case(test,base,case)
            with lock:
                seen=copy.deepcopy(observed[offset:]);now=dict(counts.get(case['name'],prior))
            if seen:assert seen[0]['status']==status
            delta={key:now[key]-prior[key] for key in prior}
            assert delta['writes']==0
            if django and 'family_queries' in case:
                assert (delta['connected'],delta['invites'])==tuple(case['family_queries']),(case['name'],delta)
            assert snapshot()==before and cache_snapshot()==before_cache,'Read changed database/cache'
            if [r['case'] for r in seen]!=([case['name']] if django else []):
                assert negative and seen==[{'case':case['name'],'status':200}]
                report['fallback_negative_control']={'rejected':True,'reason':'upstream_mismatch','status':status,'managed_state_unchanged':True,'cache_unchanged':True,'django_requests':seen}
                raise RuntimeError('upstream_mismatch')
            rows.append({'name':case['name'],'status':status,'django_requests':seen,'queries':delta if django else None,
                         'managed_state_unchanged':True,'cache_unchanged':True})
        print(f'{label}: {len(rows)} collaborator-list HTTP cases passed',flush=True)
    try:
        owner=user(610001,1);foreign=user(610002,1);empty=user(610003,1)
        admin=user(610004,1,is_superuser=True);athlete=user(610005,2)
        inactive_actor=user(610006,1,is_active=False);deleted_actor=user(610007,1,deleted=True)
        association=seed(SportAssociation,611001,user=owner,denomination='Collaborator owner')
        seed(SportAssociation,611002,user=foreign,denomination='Foreign owner')
        staff=[]
        specifications=[(612090,3,{'collaborator_role':1,'phone':None,'collaborator_permissions':None}),
            (612010,3,{'collaborator_role':3,'phone':'','collaborator_permissions':['other.users.collaborators.read']}),
            (612060,3,{'collaborator_role':2,'phone':'+39 123-456','collaborator_permissions':[]}),
            (612030,3,{'is_active':False,'collaborator_permissions':{'nested':[True,1,1.0,1.25,9007199254740993,None]}}),
            (612020,2,{'collaborator_permissions':'untyped stored string'}),
            (612080,1,{'collaborator_permissions':False})]
        for number,role,values in specifications:staff.append(user(number,role,connected_user=owner,**values))
        removed=user(612040,3,connected_user=owner,deleted=True)
        stranger=user(612050,3,connected_user=foreign)
        child=user(612070,3,connected_user=staff[0],collaborator_permissions={'chain':True})
        denied=user(612100,3,connected_user=owner,collaborator_role=3,collaborator_permissions=[]);staff.append(denied)
        invites=[]
        for number,email,expiration,accepted,secret,permissions in (
            (613090,'past@example.test',datetime(2020,1,2,3,4,5,123456,tzinfo=timezone.utc),False,'synthetic-past-invite',None),
            (613010,'accepted@example.test',None,True,None,{}),
            (613050,'future@example.test',datetime(2099,12,31,23,59,59,tzinfo=timezone.utc),False,'',{'nested':[1,True,None]})):
            row=seed(CollaborationInvites,number,user=owner,email=email,expiration_date=expiration,
                     accepted=accepted,token=secret,collaborator_role=3,collaborator_permissions=permissions)
            invites.append({'collaboration_invite_id':str(row.pk),'email':email,
                'expiration_date':expiration.isoformat().replace('+00:00','Z') if expiration else None,
                'accepted':accepted,'token':secret,'collaborator_role':3,'collaborator_permissions':permissions,'is_invite':True})
        seed(CollaborationInvites,613020,user=foreign,email='foreign@example.test',token='synthetic-foreign')
        combined=[payload(person,is_invite=False) for person in staff]+invites
        missing={'user_id':None,'first_name':'','last_name':'','email':'','phone':'','role':None,'connected_user':None,
                 'collaborator_role':None,'collaborator_permissions':None}
        add('owner-list',combined,actor=owner,family=(1,1))
        add('empty-owner',[],actor=empty,family=(1,1))
        for index,person in enumerate(staff):add('detail-'+str(index),payload(person),actor=owner,suffix='?user_id='+str(person.pk),family=(1,0))
        for name,identifier in [('foreign',str(stranger.pk)),('deleted',str(removed.pk)),('missing',uid(619999)),('self',str(owner.pk)),('invite',uid(613090))]:
            add('detail-'+name,missing,actor=owner,suffix='?user_id='+identifier,family=(1,0))
        for name,query in [('unknown','?search=nothing&role=999&page=2&page_size=1&ordering=email'),('group','?group_id='+uid(619999)),('empty-query','?user_id'),('empty-value','?user_id=')]:
            if name.startswith('empty'):
                add(name,['badly formed hexadecimal UUID string'],actor=owner,status=400,suffix=query)
            else:add(name,combined,actor=owner,suffix=query,family=(1,1))
        for name,headers in [('group-header',{'X-Group-Id':uid(619999)}),('invalid-group',{'X-Group-Id':'bad'})]:
            add(name,combined,actor=owner,headers=headers,family=(1,1))
        for name,identifier in [('bad','bad'),('invalid32','g'*32),('empty',''),('quote',"'"+'g'*31),('percent','%Q1')]:
            try:UUID(identifier)
            except ValueError as error:message=str(error)
            add('uuid-'+name,[message],actor=owner,status=400,suffix='?user_id='+quote(identifier,safe=''))
        canonical=str(staff[0].pk)
        for name,identifier in [('upper',canonical.upper()),('hex',canonical.replace('-','')),('braced','{'+canonical+'}'),
                ('urn','urn:uuid:'+canonical),('unicode',canonical.replace('0','٠'))]:
            add('uuid-'+name,payload(staff[0]),actor=owner,suffix='?user_id='+quote(identifier,safe=''),family=(1,0))
        add('duplicate-query-last',payload(staff[1]),actor=owner,suffix='?user_id=bad&user_id='+str(staff[1].pk),family=(1,0))
        add('duplicate-query-invalid-last',['badly formed hexadecimal UUID string'],actor=owner,status=400,suffix='?user_id='+canonical+'&user_id=bad')
        add('full-collaborator',combined,actor=staff[0],family=(1,1))
        add('granted-collaborator',combined,actor=staff[1],family=(1,1))
        for name,person in [('limited',staff[2]),('custom-denied',denied)]:
            add(name,{'detail':'Missing permission: other.users.collaborators.read'},actor=person,status=403)
        add('collaborator-chain',[payload(child,is_invite=False)],actor=child,family=(1,1))
        add('anonymous',{'detail':'Authentication credentials were not provided.'},status=401)
        add('athlete',{'detail':'You do not have permission to perform this action.'},actor=athlete,status=403)
        add('admin-unscoped',{'detail':'Seleziona un utente da impersonificare per accedere a questa sezione.'},actor=admin,status=403)
        for name,person,code,detail in [('inactive',inactive_actor,'user_inactive','User is inactive'),('deleted',deleted_actor,'user_not_found','User not found')]:
            add(name,{'detail':detail,'code':code},actor=person,status=401)
        add('invalid-token',{'detail':'Given token not valid for any token type','code':'token_not_valid','messages':[{'token_class':'AccessToken','token_type':'access','message':'Token is invalid'}]},actor='not-a-valid-token',status=401)
        for name,headers,status,response in [
            ('accept',{'Accept':'text/html'},406,{'detail':'Could not satisfy the request Accept header.'}),
            ('italian-athlete',{'Accept-Language':'it'},403,{'detail':"Non hai l'autorizzazione per eseguire questa azione."})]:
            add(name,response,actor=athlete if name.startswith('italian') else owner,headers=headers,status=status)
        for name,suffix in [('format-json','?format=json'),('format-empty','?format=')]:add(name,combined,actor=owner,suffix=suffix)
        add('format-invalid',{'detail':'Not found.'},actor=owner,status=404,suffix='?format=xml')
        add('format-before-accept-auth',{'detail':'Not found.'},status=404,suffix='?format=xml',headers={'Accept':'text/html'})
        add('options',{'name':'Collaborators List','description':'','renders':['application/json'],
            'parses':['application/json','application/x-www-form-urlencoded','multipart/form-data']},actor=owner,method='OPTIONS')
        for method in ('POST','PUT','PATCH','DELETE'):
            add('method-'+method,{'detail':f'Method "{method}" not allowed.'},actor=owner,method=method,body={'ignored':True},status=405)
        add('head','',actor=owner,method='HEAD',status=405)
        add('ignored-get-body',combined,actor=owner,body={'user_id':str(stranger.pk)})
        add('gzip',combined,actor=owner,headers={'Accept-Encoding':'gzip'},gzip=True)
        bad_request='\n<!doctype html>\n<html lang="en">\n<head>\n  <title>Bad Request (400)</title>\n</head>\n<body>\n  <h1>Bad Request (400)</h1><p></p>\n</body>\n</html>\n'
        unique='&'.join(f'ignored{i}=0' for i in range(1000))
        duplicate='&'.join(['ignored=0']*1000)
        for name,query,response in [('unique',unique,combined),('duplicate',duplicate,combined),('empty','&'*999,combined),('detail','&'.join(['x=0']*999)+'&user_id='+canonical,payload(staff[0]))]:
            add('query-limit-1000-'+name,response,actor=owner,suffix='?'+query)
        for name,query,person,headers,method in [
            ('unique',unique+'&extra=0',owner,{},'GET'),
            ('duplicate',duplicate+'&ignored=0',owner,{},'GET'),
            ('empty','&'*1000,owner,{},'GET'),
            ('detail',duplicate+'&user_id='+canonical,owner,{},'GET'),
            ('before-format',duplicate+'&format=xml',None,{'Accept':'text/html'},'GET'),
            ('before-accept',duplicate+'&extra=0',owner,{'Accept':'text/html'},'GET'),
            ('before-auth',duplicate+'&extra=0','invalid-token',{},'GET'),
            ('before-permission',duplicate+'&extra=0',athlete,{},'GET'),
            ('before-method',duplicate+'&extra=0',owner,{},'POST'),
            ('options',duplicate+'&extra=0',owner,{},'OPTIONS')]:
            add('query-limit-1001-'+name,bad_request,actor=person,status=400,suffix='?'+query,headers=headers,method=method)
            cases[-1].update(content_type='text/html',allow=None)
        for name,language,header,detail in [
            ('weighted-italian','it','en;q=0.2,it;q=1',"Non hai l'autorizzazione per eseguire questa azione."),
            ('weighted-english','en','it;q=0.2,en;q=1','You do not have permission to perform this action.'),
            ('french','fr','fr',"Vous n'avez pas la permission d'effectuer cette action.")]:
            add('locale-'+name,{'detail':detail},actor=athlete,status=403,headers={'Accept-Language':header})
            cases[-1]['language']=language
        # Authored locale vectors from pinned Django/DRF catalogs; HTTP results
        # remain independently checked instead of teaching expected responses.
        add('locale-selection-0',{'detail':"Non hai l'autorizzazione per eseguire questa azione."},actor=athlete,status=403,headers={'Accept-Language': ''})
        cases[-1]['language']='it'
        add('locale-selection-1',{'detail':'You do not have permission to perform this action.'},actor=athlete,status=403,headers={'Accept-Language': 'en'})
        cases[-1]['language']='en'
        add('locale-selection-2',{'detail':"Non hai l'autorizzazione per eseguire questa azione."},actor=athlete,status=403,headers={'Accept-Language': 'it'})
        cases[-1]['language']='it'
        add('locale-selection-3',{'detail':"Vous n'avez pas la permission d'effectuer cette action."},actor=athlete,status=403,headers={'Accept-Language': 'fr'})
        cases[-1]['language']='fr'
        add('locale-selection-4',{'detail':"Non hai l'autorizzazione per eseguire questa azione."},actor=athlete,status=403,headers={'Accept-Language': 'en;q=0.2,it;q=1'})
        cases[-1]['language']='it'
        add('locale-selection-5',{'detail':'You do not have permission to perform this action.'},actor=athlete,status=403,headers={'Accept-Language': 'it;q=0.1,en;q=1'})
        cases[-1]['language']='en'
        add('locale-selection-6',{'detail':'You do not have permission to perform this action.'},actor=athlete,status=403,headers={'Accept-Language': 'en;q=0,fr;q=0'})
        cases[-1]['language']='en'
        add('locale-selection-7',{'detail':'You do not have permission to perform this action.'},actor=athlete,status=403,headers={'Accept-Language': 'en,fr'})
        cases[-1]['language']='en'
        add('locale-selection-8',{'detail':"Vous n'avez pas la permission d'effectuer cette action."},actor=athlete,status=403,headers={'Accept-Language': 'fr,en'})
        cases[-1]['language']='fr'
        add('locale-selection-9',{'detail':"Non hai l'autorizzazione per eseguire questa azione."},actor=athlete,status=403,headers={'Accept-Language': '*;q=1,en;q=0.5'})
        cases[-1]['language']='it'
        add('locale-selection-10',{'detail':"Vous n'avez pas la permission d'effectuer cette action."},actor=athlete,status=403,headers={'Accept-Language': 'xx,fr'})
        cases[-1]['language']='fr'
        add('locale-selection-11',{'detail':'You do not have permission to perform this action.'},actor=athlete,status=403,headers={'Accept-Language': 'EN-us'})
        cases[-1]['language']='en'
        add('locale-selection-12',{'detail':"Vous n'avez pas la permission d'effectuer cette action."},actor=athlete,status=403,headers={'Accept-Language': 'fr-CA'})
        cases[-1]['language']='fr'
        add('locale-selection-13',{'detail':"Non hai l'autorizzazione per eseguire questa azione."},actor=athlete,status=403,headers={'Accept-Language': 'it-CH'})
        cases[-1]['language']='it'
        add('locale-selection-14',{'detail':'You do not have permission to perform this action.'},actor=athlete,status=403,headers={'Accept-Language': 'en-GB'})
        cases[-1]['language']='en-gb'
        add('locale-selection-15',{'detail':"Non hai l'autorizzazione per eseguire questa azione."},actor=athlete,status=403,headers={'Accept-Language': 'fr;q=1.001,en'})
        cases[-1]['language']='it'
        add('locale-selection-18',{'detail':"Vous n'avez pas la permission d'effectuer cette action."},actor=athlete,status=403,headers={'Accept-Language': 'fr,'})
        cases[-1]['language']='fr'
        add('locale-selection-19',{'detail':"Vous n'avez pas la permission d'effectuer cette action."},actor=athlete,status=403,headers={'Accept-Language': 'fr;Q=0.9,en;q=0.1'})
        cases[-1]['language']='fr'
        add('locale-selection-20',{'detail':"Vous n'avez pas la permission d'effectuer cette action."},actor=athlete,status=403,headers={'Accept-Language': 'en', 'Cookie': 'django_language=fr'})
        cases[-1]['language']='fr'
        add('locale-selection-21',{'detail':'You do not have permission to perform this action.'},actor=athlete,status=403,headers={'Accept-Language': 'fr', 'Cookie': 'django_language=en-US'})
        cases[-1]['language']='en'
        add('locale-selection-22',{'detail':"Vous n'avez pas la permission d'effectuer cette action."},actor=athlete,status=403,headers={'Accept-Language': 'en', 'Cookie': 'django_language=FR'})
        cases[-1]['language']='fr'
        add('locale-selection-23',{'detail':"Vous n'avez pas la permission d'effectuer cette action."},actor=athlete,status=403,headers={'Accept-Language': 'fr', 'Cookie': 'django_language=not-a-language'})
        cases[-1]['language']='fr'
        add('locale-en-credentials',{'detail':'Authentication credentials were not provided.'},actor=None,status=401,suffix='',method='GET',headers={'Accept-Language': 'en'})
        cases[-1]['language']='en'
        add('locale-en-accept',{'detail':'Could not satisfy the request Accept header.'},actor=owner,status=406,suffix='',method='GET',headers={'Accept': 'text/html', 'Accept-Language': 'en'})
        cases[-1]['language']='en'
        add('locale-en-notfound',{'detail':'Not found.'},actor=owner,status=404,suffix='?format=xml',method='GET',headers={'Accept-Language': 'en'})
        cases[-1]['language']='en'
        add('locale-en-method',{'detail':'Method "POST" not allowed.'},actor=owner,status=405,suffix='',method='POST',headers={'Accept-Language': 'en'})
        cases[-1]['language']='en'
        add('locale-it-credentials',{'detail':'Non sono state immesse le credenziali di autenticazione.'},actor=None,status=401,suffix='',method='GET',headers={'Accept-Language': 'it'})
        cases[-1]['language']='it'
        add('locale-it-accept',{'detail':'Impossibile soddisfare l\'header "Accept" presente nella richiesta.'},actor=owner,status=406,suffix='',method='GET',headers={'Accept': 'text/html', 'Accept-Language': 'it'})
        cases[-1]['language']='it'
        add('locale-it-notfound',{'detail':'Non trovato.'},actor=owner,status=404,suffix='?format=xml',method='GET',headers={'Accept-Language': 'it'})
        cases[-1]['language']='it'
        add('locale-it-method',{'detail':'Metodo "POST" non consentito'},actor=owner,status=405,suffix='',method='POST',headers={'Accept-Language': 'it'})
        cases[-1]['language']='it'
        add('locale-fr-credentials',{'detail':"Informations d'authentification non fournies."},actor=None,status=401,suffix='',method='GET',headers={'Accept-Language': 'fr'})
        cases[-1]['language']='fr'
        add('locale-fr-accept',{'detail':"L'en-tête « Accept » n'a pas pu être satisfaite."},actor=owner,status=406,suffix='',method='GET',headers={'Accept': 'text/html', 'Accept-Language': 'fr'})
        cases[-1]['language']='fr'
        add('locale-fr-notfound',{'detail':'Non trouvé.'},actor=owner,status=404,suffix='?format=xml',method='GET',headers={'Accept-Language': 'fr'})
        cases[-1]['language']='fr'
        add('locale-fr-method',{'detail':'Méthode «\xa0POST\xa0» non autorisée.'},actor=owner,status=405,suffix='',method='POST',headers={'Accept-Language': 'fr'})
        cases[-1]['language']='fr'
        # Existing Redis format is seeded directly so auth/scope gates remain independent of the new route.
        for number,actor,target_person,assoc in [(614001,admin,owner,None),(614002,admin,staff[0],None),
                (614003,owner,staff[0],str(association.pk)),(614004,owner,athlete,str(association.pk))]:
            identifier=uid(number);session_ids.append(identifier)
            cache.set(session_key(identifier),{'actor_id':str(actor.pk),'target_id':str(target_person.pk),'association_id':assoc},3600)
            if target_person==athlete:
                seed(Associate,615001,user=athlete,sport_association=association,first_name='Athlete',last_name='Fixture')
            expected=combined if target_person!=athlete else {'detail':'Operazione non disponibile nel contesto dell’associazione.'}
            add('impersonation-'+str(number),expected,actor=actor,headers={'X-Impersonation-Id':identifier},status=200 if target_person!=athlete else 403)
        add('locale-fr-scoped-media',{'detail':'Type de média « text/plain » non supporté.'},actor=owner,status=415,method='POST',headers={'Accept-Language':'fr','Content-Type':'text/plain','X-Impersonation-Id':uid(614003)})
        cases[-1].update(language='fr',raw_body='nonempty')
        for language,cookie,detail in [('fr','django_language=en; django_language=fr',"Vous n'avez pas la permission d'effectuer cette action."),('en','django_language=fr; django_language=en','You do not have permission to perform this action.')]:
            add('locale-cookie-last-'+language,{'detail':detail},actor=athlete,status=403,headers={'Accept-Language':'it','Cookie':cookie})
            cases[-1]['language']=language
        repository_tests=[]
        if tool_env.get('TESTS_COLLABORATOR_LIST_REPOSITORY')=='1':
            before=snapshot();before_cache=cache_snapshot()
            transcript=work/'collaborator-repository.jsonl'
            with transcript.open('w') as stream:
                subprocess.run(['go','test','-json','-race','-tags=collaboratorintegration','./internal/repository','-run','^TestCollaborator','-count=1'],cwd=ROOT/'BE_GO',check=True,timeout=180,stdout=stream,stderr=log,env={**tool_env,'ASSOZETA_COLLABORATOR_DSN':f'host={work} port=5432 user=contracts dbname=assozeta_contract_accounts sslmode=disable'})
            log.write(transcript.read_text());log.flush()
            records=[json.loads(line) for line in transcript.read_text().splitlines()]
            assert not any(row['Action']=='skip' for row in records)
            repository_tests=sorted(row['Test'] for row in records if row['Action']=='pass' and 'Test' in row)
            assert {'TestCollaboratorBoundedScopedReadLive','TestCollaboratorBlockedReadCancellationLive'}<=set(repository_tests)
            assert snapshot()==before and cache_snapshot()==before_cache
        report['repository_tests']=repository_tests
        report['repository_execution']='passed' if repository_tests else 'not_run_required_separately'
        report['repository_required']=tool_env.get('TESTS_COLLABORATOR_LIST_REPOSITORY')=='1'
        fixture=work/'collaborator-list.json' ;fixture.write_text(json.dumps({'cases':cases},indent=2)+'\n');fixture.chmod(0o600)
        assert len(cases)==len({case['name'] for case in cases})==MATRIX_CASE_COUNT
        assert MATRIX_REQUIRED_NAMES<={case['name'] for case in cases}
        assert all('response' in case for case in cases)
        report['matrix_version']=MATRIX_VERSION
        thread=threading.Thread(target=server.run,daemon=True);thread.start()
        poll(lambda:server.started,bool,timeout=15)
        port=server.servers[0].sockets[0].getsockname()[1];django_base=f'http://127.0.0.1:{port}'
        replay(django_base,'django',True)
        if go_requested:
            from TESTS.adapters.django.payment_info_stats import capture,compare,require_no_user_writes
            connections.close_all();stats_before=capture(connections['default'])
            from TESTS.adapters.go.payment_category_server import native_server
            with (go_server_factory or native_server)(work,log,tool_env,address,versioned=versioned) as runtime:
                report['go_runtime']=runtime['metadata']
                replay(runtime['base_url'],'go',False)
                if versioned:
                    from TESTS.adapters.caddy import proxy
                    with proxy(f'host.docker.internal:{port}',runtime['proxy_upstream'],log,network=runtime.get('network')) as (base,_):
                        replay(base+'/api/v1','v1',True);replay(base+'/api/v2','v2',False)
                    with proxy(f'host.docker.internal:{port}',f'host.docker.internal:{port}',log) as (base,_):
                        try:replay(base+'/api/v2','negative-v2',False,selected=cases[:1],negative=True)
                        except RuntimeError as error:assert str(error)=='upstream_mismatch'
                        else:raise AssertionError('Actual Django fallback was accepted')
                    report['proxy_cleanup_verified']=True
            connections.close_all();evidence=compare(stats_before,capture(connections['default']))
            report['database_write_evidence']=evidence;require_no_user_writes(evidence)
        report.update(success=True,http_cases=len(cases),managed_models=len(original),
            baseline_facts=['Unpaginated users followed by invitations; no explicit within-kind ordering.',
                'Inactive and non-collaborator connected users are included; deleted users excluded.',
                'Accepted/expired invites and invite tokens are returned; query pagination/search/group do not filter.'])
    except BaseException as error:
        report['error_type']=type(error).__name__
        raise
    finally:
        failures=[]
        def cleanup(label,operation):
            try:operation()
            except BaseException as error:failures.append({'stage':label,'error_type':type(error).__name__})
        def stop_server():
            if thread:
                server.should_exit=True;thread.join(timeout=30)
                assert not thread.is_alive(),'Django server did not stop'
        cleanup('django_server',stop_server)
        cleanup('trace_disconnect',lambda:connection_created.disconnect(attached))
        for identifier in session_ids:cleanup('cache_session',lambda identifier=identifier:cache.delete(session_key(identifier)))
        with disable_auditlog():
            for model,pk in reversed(created):cleanup('fixture_row',lambda model=model,pk=pk:model._base_manager.filter(pk=pk).delete())
        cleanup('database_connections',connections.close_all)
        def verify_state():
            assert snapshot()==original and cache_snapshot()==original_cache,'Cleanup state differs'
        cleanup('state_restore',verify_state)
        report['cleanup_verified']=not failures;report['cleanup_errors']=failures
        report['source_stable']=hashes()==sources;report['source_sha256']=sources
        report['success']=bool(report['success'] and report['cleanup_verified'] and report['source_stable'])
        cleanup('redis_client',raw.close)
        report['cleanup_verified']=not failures
        report['success']=bool(report['success'] and report['cleanup_verified'])
        result_file.write_text(json.dumps(report,indent=2)+'\n')
    assert report['success']
    return report
