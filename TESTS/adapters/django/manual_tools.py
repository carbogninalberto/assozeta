"""Real manual-tool functions, settings, filesystem and PostgreSQL context.

Invoked with manual_reader_http.py --tools to reuse its owned database lifecycle.
No function, context resolver, loader or permission decision is replaced.
"""
import hashlib
import json
import os
import subprocess

SOURCE = 'def fixture(): return "captured"\n'
SOURCE_PATH = 'BE/manual_fixture.py'
REVISION = 'a'*40
RELEASE = 'manual-tool-fixture'


def prepare(base, spec):
    from application.manuale.index import canonical, digest
    base.mkdir(); code = base/'code'; (code/'BE').mkdir(parents=True)
    (code/SOURCE_PATH).write_text(SOURCE)
    public_count = spec.get('copies', 2)
    chunks = []
    for ident, audience, features in [
        *[(f'public{i:02d}', 'public', []) for i in range(public_count)],
        ('private', 'maintainer', []), ('host', 'public', ['self_hosted']),
    ]:
        chunks.append(dict(id='guide#'+ident, page='guide', section=ident, title='Fixture', text='Fixture',
            status='verified', url='https://manual.example.test/guide#'+ident, screenshots=[],
            scenario_ids=['fixture'], page_title='Fixture guide', language='it', intent='read',
            audience=audience, features=features, runtime_dependencies={}, private_secret='never-public-canary',
            evidence=[dict(path=SOURCE_PATH, sha256=digest(SOURCE), excerpt='SOURCE_EVIDENCE')],
            capture_provenance=[{'scenario': 'fixture', 'status': 'passed'}]))
    value = dict(format=1, embedding='italian-tfidf-lsa-v5', dependencies={SOURCE_PATH: digest(SOURCE)},
        content_identity='d'*64, chunks=chunks,
        semantics=dict(vocabulary=['fixture'], idf=[1.0], basis=[[1.0]], vectors=[[1.0] for _ in chunks]),
        gaps=[{'page': 'docs/fixture.mdx', 'section': 'pending', 'status': 'pending'}],
        metadata=dict(application_revision=REVISION, release=RELEASE, publication_status='release-bundled',
            code_state='committed', manual_revision='c'*40, manual_state='generated-from-committed',
            verification={'corpus_identity': 'd'*64}))
    if spec.get('catalog'):
        value['catalog'] = dict(summary={'navigation_pages': 1, 'verified_sections': len(chunks), 'pending_sections': 1},
            pages=[{'path': 'docs/fixture.mdx', 'domains': ['fixture'], 'sections': [{'id': 'pending', 'status': 'pending'}]}],
            domains={'fixture': {'status': 'review_target'}},
            screenshot_backlog=[{'page': 'docs/fixture.mdx', 'status': 'review_target'}],
            files={SOURCE_PATH: {'sha256': digest(SOURCE)}})
    bundle = base/'bundle'; bundle.mkdir()
    if spec.get('missing_model'):value.pop('semantics')
    if spec.get('empty_model'):value['semantics'] = {'vocabulary': []}
    runtime = base/'runtime.json'
    source = spec.get('source', 'bundle')
    if source == 'runtime':value['metadata']['publication_status'] = 'published'
    if source == 'development':
        value['metadata'].update(publication_status='development-local', verified_corpus_identity='d'*64)
        if spec.get('reappeared'):value['metadata']['development_deleted_files'] = [SOURCE_PATH]
    value['identity'] = digest(canonical(value))
    index = runtime if source == 'runtime' else bundle/'index.json'
    index.write_text(json.dumps(value))
    (bundle/'bundle.json').write_text(json.dumps(dict(format=1, distribution='immutable-backend-image',
        index_sha256=digest(index.read_bytes()), corpus_identity=value['identity'], application_revision=REVISION,
        release=RELEASE, manual_revision='c'*40, verified_corpus_identity='d'*64)))
    if spec.get('corrupt'):(bundle/'bundle.json').write_text('{}')
    if spec.get('stale'):(code/SOURCE_PATH).write_text('changed\n')
    if spec.get('deleted'):(code/SOURCE_PATH).unlink()
    settings = dict(MANUAL_BUNDLE_PATH=str(bundle if source=='bundle' else base/'absent'),
        MANUAL_BUNDLE_REQUIRED=False, MANUAL_INDEX_PATH=str(runtime), MANUAL_ASSET_ROOT='',
        MANUAL_SOURCE_ROOT=str(code) if spec.get('code_root') else '', MANUAL_CORPUS_BASE_URL='',
        MANUAL_APPLICATION_REVISION=REVISION, RUNNING_VERSION='wrong-release' if spec.get('wrong_version') else RELEASE,
        MANUAL_RUN_ID='', MANUAL_DEVELOPMENT_ROOT=str(base/'development'), ASSOZETA_DEPLOYMENT_MODE='production')
    if source == 'development':
        development = base/'development'; package = development/'packages'/value['identity']; package.mkdir(parents=True)
        (package/'index.json').write_text(json.dumps(value))
        (development/'active.json').write_text(json.dumps({'format': 1, 'corpus_identity': value['identity']}))
        # A malformed preview must not affect tool retrieval: only the ordinary
        # reader, not these verified-knowledge tools, loads preview content.
        (development/'preview').mkdir()
        (development/'preview/active.json').write_text('{}')
        settings.update(ASSOZETA_DEPLOYMENT_MODE='development', MANUAL_APPLICATION_REVISION='', MANUAL_SOURCE_ROOT=str(code))
    if spec.get('missing'):
        settings.update(MANUAL_BUNDLE_PATH=str(base/'absent'), MANUAL_INDEX_PATH=str(base/'missing.json'))
    return settings, value


def fingerprints(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()}


def measure_tools(work, fixture, observed, go_source=False, log=None, go_service=False):
    from django.db import connection
    from django.test import override_settings
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from application.models import User, SportAssociation
    from application.models.audit_models import AuditLogIndex
    from application.manuale.tools import MANUAL_TOOL_FUNCTIONS
    from instance.models import InstanceConfiguration
    from TESTS.contracts.test_manual_tools import validate_report
    if go_source or go_service:
        from TESTS.adapters.django.manual_reader_http import ROOT
        subprocess.run(['go', 'test', '-race', '-c', '-tags', 'manualtoolserviceintegration' if go_service else 'manualtoolintegration',
                        '-o', str(work/'tool-oracle'), './internal/service' if go_service else './pkg/manual'], cwd=ROOT/'BE_GO',
                       check=True, timeout=180, stdout=log, stderr=subprocess.STDOUT)
    service_env = {**os.environ, 'ASSOZETA_MANUAL_TOOL_SERVICE_DSN':
                   f'host={work} port=5432 user=contracts dbname=assozeta_contract_manual_http sslmode=disable'}
    users = {}
    with disable_auditlog():
        for name in ('owner', 'administrator', 'collaborator', 'foreign', 'inactive', 'deleted'):
            users[name] = User.original_objects.create(username='tool-'+name, email=name+'@example.test', password='!fixture',
                role=User.COLLABORATOR if name=='collaborator' else User.ASSOCIATION,
                is_superuser=name=='administrator', is_active=name!='inactive', deleted=name=='deleted',
                connected_user=users.get('owner') if name=='collaborator' else None,
                collaborator_role=User.CUSTOM_COLLABORATOR_ROLE, collaborator_permissions=[])
        association = SportAssociation.original_objects.create(user=users['owner'], denomination='Tool Fixture ASD')
        other = SportAssociation.original_objects.create(user=users['foreign'], denomination='Foreign Fixture ASD')
    def snapshot():
        return {model._meta.label: list(model._base_manager.order_by('pk').values())
                for model in (User, SportAssociation, InstanceConfiguration, LogEntry, AuditLogIndex)}
    for name, spec in fixture['cases'].items():
        setup = spec.get('setup', {})
        with disable_auditlog():
            User.original_objects.filter(pk=users['owner'].pk).update(is_active=not setup.get('inactive_owner', False), deleted=setup.get('deleted_owner', False))
            InstanceConfiguration.objects.all().delete()
            if setup.get('config', True):
                InstanceConfiguration.objects.create(domain='tools.example.test', name='Tool fixture',
                    self_hosted=setup.get('self_hosted', True),
                    primary_association=association if setup.get('primary', True) else None)
        base = work/name; settings, corpus = prepare(base, setup)
        args = dict(spec.get('arguments', {}))
        if spec.get('long_query'):args['query'] = 'x' * spec['long_query']
        if spec.get('actor'):
            args['user_id'] = str(users[spec['actor']].pk)
        association_id = str(other.pk if setup.get('foreign_association') else association.pk)
        before = snapshot(); files_before = fingerprints(base); statements = []
        def capture(execute, sql, params, many, context):
            statements.append(sql)
            return execute(sql, params, many, context)
        with override_settings(**settings), connection.execute_wrapper(capture):
            result = MANUAL_TOOL_FUNCTIONS[spec['tool']](association_id, **args)
        sections = result.get('results', [])
        if isinstance(result.get('section'), dict):sections += [result['section']]
        for section in sections:
            assert section['corpus_identity'] == corpus['identity'], 'Response did not use the actual fixture index'
        item = dict(result=result, ids=[section['id'] for section in sections],
            database_unchanged=snapshot()==before, files_unchanged=fingerprints(base)==files_before,
            writes=sum(not sql.lstrip().upper().startswith(('SELECT', 'SHOW', 'BEGIN', 'COMMIT', 'ROLLBACK', 'SAVEPOINT', 'RELEASE')) for sql in statements),
            selects=sum(sql.lstrip().upper().startswith('SELECT') for sql in statements),
            source_canary_absent='never-public-canary' not in json.dumps(result),
            fixture_identity=corpus['identity'])
        observed[name] = item
        validate_report({'schema_version': 1, 'cases': {name: item}}, {'cases': {name: spec}})
        print('PASS manual tool: '+name, flush=True)
        if go_source or go_service:
            # Fixture-supplied trusted context exercises only the Go library.
            # Django above still resolves authority from the actual database.
            maintainer = (spec.get('actor')=='owner' and setup.get('config', True)
                          and setup.get('self_hosted', True) and setup.get('primary', True)
                          and not any(setup.get(k) for k in ('foreign_association', 'inactive_owner', 'deleted_owner')))
            fields = {'MANUAL_BUNDLE_PATH':'BundlePath','MANUAL_BUNDLE_REQUIRED':'BundleRequired',
                      'MANUAL_INDEX_PATH':'IndexPath','MANUAL_ASSET_ROOT':'AssetRoot','MANUAL_SOURCE_ROOT':'SourceRoot',
                      'MANUAL_CORPUS_BASE_URL':'CorpusBaseURL','MANUAL_APPLICATION_REVISION':'ApplicationRevision',
                      'RUNNING_VERSION':'Release','MANUAL_RUN_ID':'RunID','MANUAL_DEVELOPMENT_ROOT':'DevelopmentRoot',
                      'ASSOZETA_DEPLOYMENT_MODE':'DeploymentMode'}
            manifest = {'Settings': {fields[k]:v for k,v in settings.items()},
                        'Context': {'Maintainer': maintainer, 'Features': ['self_hosted'] if setup.get('config', True) and setup.get('self_hosted', True) else []},
                        'Name': spec['tool'], 'Arguments': args, 'Expected': result}
            if go_service:
                # The service receives trusted identity IDs, not role/owner
                # flags. It must resolve authority against these actual rows.
                manifest.update(UserID=str(users[spec['actor']].pk) if spec.get('actor') else '',
                                AssociationID=association_id, OwnerID=str(users['owner'].pk), OtherAssociationID=str(other.pk))
            path = work/'tool-case.json'; path.write_text(json.dumps(manifest))
            env = {**service_env, 'ASSOZETA_MANUAL_TOOL_SERVICE_FIXTURE':str(path)} if go_service else {**os.environ, 'ASSOZETA_MANUAL_TOOL_FIXTURE': str(path)}
            subprocess.run([str(work/'tool-oracle'), '-test.run', '^TestManualToolServiceOracle$' if go_service else '^TestManualToolOracle$', '-test.v'],
                           env=env,
                           check=True, timeout=45, stdout=log, stderr=subprocess.STDOUT)
            assert fingerprints(base)==files_before, 'Go mutated fixture files'
            assert snapshot()==before, 'Go mutated fixture database'
            actual = json.loads(path.with_name(path.name+'.result.json').read_text())
            if go_service:
                item.update(go_service_result=actual['result'], go_service_selects=actual['selects'],
                            go_service_files_unchanged=True, go_service_database_unchanged=True)
            else:
                item.update(go_library_result=actual, go_library_files_unchanged=True)
            print('PASS Go manual tool '+('service' if go_service else 'library')+': '+name, flush=True)
    if go_service:
        base=work/'service-live'; settings,_=prepare(base,{})
        manifest['Settings']={fields[k]:v for k,v in settings.items()}
        manifest['AssociationID']=str(association.pk)
        path.write_text(json.dumps(manifest))
        before=snapshot(); files_before=fingerprints(base)
        subprocess.run([str(work/'tool-oracle'),'-test.run','^TestManualToolServiceLiveAuthority$','-test.v'],
                       env=env,check=True,timeout=45,stdout=log,stderr=subprocess.STDOUT)
        assert snapshot()==before and fingerprints(base)==files_before, 'Live service tests did not restore owned fixture state'
        result=json.loads(path.with_name(path.name+'.live.json').read_text())
        return {**result,'database_unchanged':True,'files_unchanged':True}
