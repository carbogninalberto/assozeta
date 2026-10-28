import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / 'docs/scripts'
sys.path.insert(0, str(SCRIPTS))
import manuale_impact as impact  # noqa: E402


def load_runner():
    spec = importlib.util.spec_from_file_location('manuale_runner_for_impact', SCRIPTS / 'manuale.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GIT_ENV = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull,
           'GIT_AUTHOR_NAME': 'impact-test', 'GIT_AUTHOR_EMAIL': 'impact-test@example.invalid',
           'GIT_COMMITTER_NAME': 'impact-test', 'GIT_COMMITTER_EMAIL': 'impact-test@example.invalid'}
REVIEWED = 'a' * 64


def recipe(identifier, dependencies, page, prefix):
    script = f'selfhost/tests/browser/manuale/{identifier}.mjs'
    return {'id': identifier, 'version': 1, 'script': script, 'pages': [page],
            'dependencies': sorted({*dependencies, script}), 'capture_prefix': prefix, 'sources': dependencies,
            'checkpoints': [{'id': 'one', 'caption': 'One'}], 'publication_sections': [page + '#intro']}


FILES = {
    'docs/manuale/recipes.json': json.dumps({
        'format': 1, 'shared_dependencies': ['BE/core/'], 'non_capture_dependencies': [],
        'reference_dependencies': ['BE/application/reviewed_only.py'],
        'reference_modules': ['docs/manuale/alpha-recipes.mjs'],
        'recipes': [
            recipe('alpha', ['UI/src/routes/alpha/Alpha.svelte', 'BE/application/views/alpha_views.py',
                             'selfhost/tests/browser/manuale/scenario.mjs'], 'faq/alpha.mdx', 'images/alpha/'),
            recipe('beta', ['UI/src/routes/beta/Beta.svelte', 'BE/application/views/beta_views.py',
                            'selfhost/tests/browser/manuale/scenario.mjs'], 'faq/beta.mdx', 'images/beta/'),
        ]}, indent=2),
    'docs/manuale/alpha-recipes.mjs': f'const reviewed = {{\n  "BE/application/reviewed_only.py": "{REVIEWED}"\n}};\n',
    'docs/manuale/beta-authored-workflows.mjs': "export const betaWorkflows = {\n    'beta': {version: 1},\n};\n",
    'docs/architecture/notes.md': '# Notes\n',
    'manuale/faq/alpha.mdx': '# Alpha\n\nFirst paragraph.\n\n![Alpha](/images/alpha/1.png)\n',
    'manuale/faq/beta.mdx': '# Beta\n\nBeta text.\n',
    'manuale/images/alpha/1.png': 'png-bytes',
    'manuale/.manuale-evidence.json': json.dumps({'format': 1, 'sections': {
        'faq/alpha.mdx#intro': {'recipe_module': 'docs/manuale/alpha-recipes.mjs', 'content_sha256': '1'},
        'faq/beta.mdx#intro': {'recipe_module': 'docs/manuale/alpha-recipes.mjs', 'content_sha256': '2'}}}),
    # Capture tooling.
    'selfhost/tests/browser/manuale/scenario.mjs': 'export const scenario = 1;\n',
    'selfhost/tests/browser/manuale/capture-helper.mjs': 'export const helper = 1;\n',
    'selfhost/tests/browser/manuale/alpha.mjs': "import {scenario} from './scenario.mjs';\nimport {helper} from './capture-helper.mjs';\n",
    'selfhost/tests/browser/manuale/beta.mjs': "import {scenario} from './scenario.mjs';\n",
    'selfhost/tests/browser/manuale/alpha.test.mjs': "import './alpha.mjs';\n",
    # Frontend.
    'UI/endpoints.js': ('export const buildConfig = function(config={apiHost: "/api"}) {\n'
                        '    const apiHost = config.apiHost;\n'
                        '    return {__bakney: JSON.stringify({env: {API: {\n'
                        '        ALPHA: {LIST: `${apiHost}/alpha/list`, STATS: `${apiHost}/alpha/stats/`},\n'
                        '    }}})};\n};\n'),
    'UI/src/main.js': "import App from './App.svelte';\n",
    'UI/src/App.svelte': "<script>\n    import Shell from 'components/Shell.svelte';\n    import routes from './routes';\n</script>\n",
    'UI/src/routes.js': ("import {session} from './utils/session.js';\nexport default {\n"
                         "    '/alpha': () => import('./routes/alpha/Alpha.svelte'),\n"
                         "    '/beta': () => import('./routes/beta/Beta.svelte'),\n"
                         "    '/report': () => import('./routes/report/Report.svelte'),\n};\n"),
    'UI/src/utils/session.js': 'export const session = 1;\n',
    'UI/src/components/Shell.svelte': '<nav>Shell</nav>\n',
    'UI/src/components/AlphaWidget.svelte': '<p>Widget</p>\n',
    'UI/src/components/Unused.svelte': '<p>Unused</p>\n',
    'UI/src/components/ReportChart.svelte': '<p>Chart</p>\n',
    'UI/src/routes/dashboard/Dashboard.svelte': '<h1>Dashboard</h1>\n',
    'UI/src/routes/alpha/Alpha.svelte': ("<script>\n    import AlphaWidget from 'components/AlphaWidget.svelte';\n"
                                         "    const list = __bakney.env.API.ALPHA.LIST;\n"
                                         "    const stats = __bakney.env.API.ALPHA.STATS + id;\n</script>\n"),
    'UI/src/routes/beta/Beta.svelte': "<script>\n    import {format} from '../../utils/format.js';\n</script>\n",
    'UI/src/routes/report/Report.svelte': "<script>\n    import Chart from 'components/ReportChart.svelte';\n</script>\n",
    'UI/src/utils/format.js': "import {deep} from './deep.js';\nexport const format = deep;\n",
    'UI/src/utils/deep.js': 'export const deep = 1;\n',
    # Backend.
    'BE/core/__init__.py': '',
    'BE/core/urls.py': "from django.urls import include, path\nurlpatterns = [path('', include('application.urls'))]\n",
    'BE/core/middleware.py': 'class TenantMiddleware:\n    pass\n',
    'BE/application/__init__.py': '',
    'BE/application/apps.py': ('from django.apps import AppConfig\n\n\nclass Config(AppConfig):\n'
                               '    def ready(self):\n        import application.signals  # noqa\n'),
    'BE/application/signals.py': ('from django.dispatch import receiver\nfrom application.models import AuditEntry\n\n\n'
                                  '@receiver(object, sender=AuditEntry)\ndef on_save(**kwargs):\n'
                                  '    from application.services.audit import record\n    record()\n'),
    'BE/application/tenant_scope.py': 'def install():\n    return None\n',
    'BE/application/reviewed_only.py': 'VALUE = 1\n',
    'BE/application/urls.py': ("from django.urls import path\nfrom .views.alpha_views import alpha_list\n"
                               "from .views.stats_views import stats\nfrom .views.gamma_views import gamma\n"
                               "urlpatterns = [path('alpha/list', alpha_list), path('alpha/stats/<int:pk>', stats),\n"
                               "               path('gamma', gamma)]\n"),
    'BE/application/models/__init__.py': ('from .thing_models import *\nfrom .other_models import *\n'
                                          'from .audit_models import AuditEntry as AuditEntry\n'),
    'BE/application/models/audit_models.py': 'class AuditEntry:\n    pass\n',
    'BE/application/models/thing_models.py': 'class Thing:\n    pass\n',
    'BE/application/models/other_models.py': 'class Other:\n    pass\n',
    'BE/application/services/__init__.py': '',
    'BE/application/services/audit.py': 'def record():\n    return True\n',
    'BE/application/services/alpha_service.py': ('from application.utils import helpers\n\n\n'
                                                 'def compute(model):\n    return helpers.fmt(model)\n\n\n'
                                                 'def other():\n    return 2\n'),
    'BE/application/utils/__init__.py': '',
    'BE/application/utils/helpers.py': 'def fmt(value):\n    return str(value)\n',
    'BE/application/utils/legacy_helper.py': 'def unused():\n    return 1\n',
    'BE/application/views/__init__.py': '',
    'BE/application/views/alpha_views.py': ('from application.models import Thing\n'
                                            'from application.services.alpha_service import compute\n\n\n'
                                            'def alpha_list(request):\n    return compute(Thing)\n'),
    'BE/application/views/beta_views.py': 'def beta(request):\n    return 1\n',
    'BE/application/views/stats_views.py': 'def stats(request, pk):\n    return pk\n',
    'BE/application/views/gamma_views.py': 'def gamma(request):\n    return 1\n',
    'BE/application/agent/__init__.py': '',
    'BE/application/agent/core.py': 'def answer(question):\n    return question\n',
    'BE/application/mcp_server/__init__.py': '',
    'BE/application/mcp_server/server.py': 'def serve():\n    return 1\n',
    'BE/application/mcp_server/security.py': 'def owner_only(user):\n    return user.is_owner\n',
    'BE/application/management/__init__.py': '',
    'BE/application/management/commands/__init__.py': '',
    'BE/application/management/commands/seed_manuale.py': 'def handle():\n    return 1\n',
    'BE/application/tests/test_alpha.py': 'def test_alpha():\n    assert True\n',
}


class Repository:
    def __init__(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.write(FILES)
        (self.root / 'docs/manuale/impact-map.json').write_text((ROOT / impact.MAP_PATH).read_text())
        (self.root / 'docs/scripts').mkdir(parents=True, exist_ok=True)
        shutil.copy(SCRIPTS / 'manuale_impact.py', self.root / 'docs/scripts/manuale_impact.py')
        self.git('init', '-q')
        self.base = self.commit('base')

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.root), *args], check=True, capture_output=True, text=True,
                              env=GIT_ENV).stdout.strip()

    def write(self, files):
        for relative, content in files.items():
            path = self.root / relative
            if content is None:
                path.unlink()
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)

    def commit(self, message):
        self.git('add', '-A')
        self.git('commit', '-q', '--no-verify', '-m', message)
        return self.git('rev-parse', 'HEAD')

    def change(self, files, message='change'):
        self.write(files)
        head = self.commit(message)
        return impact.analyze(self.root, impact.changed_files(self.root, self.base, head), base=self.base, head=head)

    def classify(self, *paths):
        return impact.analyze(self.root, list(paths), base=None, head=None)

    def close(self):
        self.directory.cleanup()


class ImpactFixtureTest(unittest.TestCase):
    def setUp(self):
        self.repo = Repository()
        self.addCleanup(self.repo.close)

    def entry(self, report, path):
        return next(item for item in report['files'] if item['path'] == path)

    def assertFormat(self, report):
        self.assertEqual(set(report), {'format', 'mode', 'full_verification_required', 'affected_workflows',
                                       'reused_workflows', 'affected_pages', 'checks', 'files', 'reasons'})
        self.assertEqual(report['format'], 1)
        self.assertEqual(set(report['checks']), {'bot', 'mcp', 'security', 'runtime'})
        for item in report['files']:
            self.assertEqual(set(item), {'path', 'category', 'workflows', 'reason'})
            self.assertIn(item['category'], impact.CATEGORIES)
            self.assertTrue(item['reason'])
        self.assertEqual(sorted(report['affected_workflows'] + report['reused_workflows']), ['alpha', 'beta'])

    def assertUnaffected(self, path, category='unrelated'):
        report = self.repo.classify(path)
        self.assertFormat(report)
        self.assertEqual(self.entry(report, path)['category'], category, self.entry(report, path)['reason'])
        self.assertEqual(report['mode'], 'unaffected')
        self.assertFalse(report['full_verification_required'])
        self.assertEqual(report['affected_workflows'], [])
        self.assertEqual(report['reused_workflows'], ['alpha', 'beta'])
        return report

    def test_unrelated_backend_utility(self):
        report = self.assertUnaffected('BE/application/utils/legacy_helper.py')
        self.assertIn('dead code', report['files'][0]['reason'])
        self.assertFalse(any(report['checks'].values()))

    def test_backend_tests_and_allow_listed_areas_are_unrelated(self):
        for path in ('BE/application/tests/test_alpha.py', 'docs/architecture/notes.md', 'TESTS/contracts/x.py',
                     'BE_GO/internal/util.go', 'quality-reports/run.json', 'docs/scripts/tests/test_x.py',
                     'selfhost/tests/browser/manuale/alpha.test.mjs'):
            with self.subTest(path=path):
                self.assertUnaffected(path)

    def test_unrelated_ui_component(self):
        report = self.assertUnaffected('UI/src/components/Unused.svelte')
        self.assertIn('not bundled', report['files'][0]['reason'])
        report = self.assertUnaffected('UI/src/components/ReportChart.svelte')
        self.assertIn('no workflow declares', report['files'][0]['reason'])

    def test_one_mdx_paragraph_recompiles_only_that_page(self):
        report = self.repo.change({'manuale/faq/alpha.mdx': FILES['manuale/faq/alpha.mdx'].replace('First', 'Updated')})
        self.assertFormat(report)
        self.assertEqual(report['mode'], 'content')
        self.assertEqual(report['affected_workflows'], [])
        self.assertEqual(report['affected_pages'], ['manuale/faq/alpha.mdx'])
        item = self.entry(report, 'manuale/faq/alpha.mdx')
        self.assertEqual(item['category'], 'docs-text')
        self.assertIn('screenshots are reused: alpha', item['reason'])
        self.assertFalse(report['checks']['runtime'])

    def test_mdx_referencing_a_missing_screenshot_recaptures_its_owner(self):
        report = self.repo.change({'manuale/faq/beta.mdx': '# Beta\n\n![New](/images/beta/2.png)\n'})
        self.assertEqual(self.entry(report, 'manuale/faq/beta.mdx')['category'], 'behavior')
        self.assertEqual(report['affected_workflows'], ['beta'])

    def test_documented_ui_workflow(self):
        report = self.repo.classify('UI/src/routes/alpha/Alpha.svelte')
        self.assertFormat(report)
        self.assertEqual(report['mode'], 'behavioral')
        self.assertEqual(report['affected_workflows'], ['alpha'])
        self.assertEqual(report['reused_workflows'], ['beta'])
        self.assertEqual(report['affected_pages'], ['manuale/faq/alpha.mdx'])
        self.assertEqual(self.entry(report, 'UI/src/routes/alpha/Alpha.svelte')['category'], 'behavior')
        self.assertTrue(report['checks']['runtime'])
        self.assertFalse(report['checks']['security'])

    def test_shared_capture_utility_invalidates_its_dependents(self):
        report = self.repo.classify('selfhost/tests/browser/manuale/scenario.mjs')
        self.assertEqual(self.entry(report, 'selfhost/tests/browser/manuale/scenario.mjs')['category'], 'capture-tooling')
        self.assertEqual(report['mode'], 'behavioral')
        self.assertEqual(report['affected_workflows'], ['alpha', 'beta'])
        report = self.repo.classify('selfhost/tests/browser/manuale/capture-helper.mjs')
        self.assertEqual(report['affected_workflows'], ['alpha'])

    def test_shared_contracts_and_fixtures_fail_closed(self):
        for path in ('BE/application/management/commands/seed_manuale.py',
                     'selfhost/tests/browser/manuale/frame.mjs', 'docs/scripts/manuale.py',
                     '.github/workflows/manuale.yml'):
            with self.subTest(path=path):
                report = self.repo.classify(path)
                self.assertEqual(self.entry(report, path)['category'], 'shared-contract')
                self.assertEqual(report['mode'], 'full')
                self.assertEqual(report['affected_workflows'], ['alpha', 'beta'])

    def test_shared_auth_behavior_requires_security_checks(self):
        for path in ('BE/core/middleware.py', 'BE/application/tenant_scope.py',
                     'BE/application/impersonation_scope.py', 'BE/application/permissions_registry.py'):
            with self.subTest(path=path):
                report = self.repo.classify(path)
                self.assertFormat(report)
                self.assertEqual(self.entry(report, path)['category'], 'security')
                self.assertEqual(report['mode'], 'full')
                self.assertTrue(report['full_verification_required'])
                self.assertEqual(report['affected_workflows'], ['alpha', 'beta'])
                self.assertEqual(report['reused_workflows'], [])
                self.assertTrue(all(report['checks'].values()))

    def test_bot_answer_code_runs_bot_contracts_only(self):
        report = self.repo.classify('BE/application/agent/core.py')
        self.assertFormat(report)
        self.assertEqual(self.entry(report, 'BE/application/agent/core.py')['category'], 'bot')
        self.assertEqual(report['mode'], 'unaffected')
        self.assertEqual(report['checks'], {'bot': True, 'mcp': False, 'security': False, 'runtime': False})

    def test_mcp_authorization_is_security_and_mcp_tools_are_mcp(self):
        report = self.repo.classify('BE/application/mcp_server/security.py')
        self.assertEqual(self.entry(report, 'BE/application/mcp_server/security.py')['category'], 'security')
        self.assertEqual(report['mode'], 'full')
        self.assertTrue(report['checks']['mcp'] and report['checks']['security'])
        report = self.repo.classify('BE/application/mcp_server/server.py')
        self.assertEqual(self.entry(report, 'BE/application/mcp_server/server.py')['category'], 'mcp')
        self.assertEqual(report['mode'], 'unaffected')
        self.assertEqual(report['checks'], {'bot': False, 'mcp': True, 'security': False, 'runtime': False})

    def test_transitive_dependencies(self):
        cases = {
            'UI/src/utils/deep.js': ['beta'],                         # Beta.svelte -> format.js -> deep.js
            'UI/src/components/AlphaWidget.svelte': ['alpha'],        # alias import
            'BE/application/services/alpha_service.py': ['alpha'],   # view -> service
            'BE/application/utils/helpers.py': ['alpha'],             # view -> service -> utils module attribute
            'BE/application/views/stats_views.py': ['alpha'],         # UI endpoint -> URL table -> view
            'UI/src/components/Shell.svelte': ['alpha', 'beta'],      # application shell
            'BE/application/services/audit.py': ['alpha', 'beta'],    # registered signal receiver
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                report = self.repo.classify(path)
                item = self.entry(report, path)
                self.assertEqual(item['category'], 'behavior', item['reason'])
                self.assertEqual(report['affected_workflows'], expected)
        self.assertIn('Via alpha', self.entry(self.repo.classify('BE/application/utils/helpers.py'),
                                              'BE/application/utils/helpers.py')['reason'])

    def test_model_changes_are_narrowed_to_their_users(self):
        report = self.repo.change({'BE/application/models/thing_models.py': 'class Thing:\n    flag = True\n'})
        self.assertEqual(report['affected_workflows'], ['alpha'])  # star re-export resolved by name
        self.repo.base = self.repo.git('rev-parse', 'HEAD')
        report = self.repo.change({'BE/application/models/audit_models.py': 'class AuditEntry:\n    flag = True\n'})
        self.assertEqual(report['affected_workflows'], ['alpha', 'beta'])  # signal receiver sender

    def test_unreferenced_model_module_is_not_a_workflow_dependency(self):
        report = self.repo.change({'BE/application/models/other_models.py': 'class Other:\n    flag = True\n'})
        item = self.entry(report, 'BE/application/models/other_models.py')
        self.assertEqual(item['category'], 'unrelated', item['reason'])

    def test_symbol_level_narrowing_and_formatting_only_changes(self):
        service = FILES['BE/application/services/alpha_service.py']
        report = self.repo.change({'BE/application/services/alpha_service.py': service.replace('return 2', 'return 3')})
        item = self.entry(report, 'BE/application/services/alpha_service.py')
        self.assertEqual(item['category'], 'unrelated', item['reason'])
        self.assertIn('other', item['reason'])
        report = self.repo.change({'BE/application/services/alpha_service.py': service.replace(
            'return helpers.fmt(model)', 'return helpers.fmt(model) + "!"')})
        self.assertEqual(report['affected_workflows'], ['alpha'])
        report = self.repo.change({'BE/application/views/alpha_views.py':
                                   FILES['BE/application/views/alpha_views.py'] + '# comment only\n'})
        self.assertEqual(self.entry(report, 'BE/application/views/alpha_views.py')['category'], 'unrelated')

    def test_added_removed_and_renamed_files(self):
        report = self.repo.change({
            'UI/src/components/NewThing.svelte': '<p>New</p>\n',
            'UI/src/routes/beta/Beta.svelte': ("<script>\n    import {format} from '../../utils/format.js';\n"
                                               "    import NewThing from 'components/NewThing.svelte';\n</script>\n"),
            'UI/src/utils/format.js': 'export const format = 1;\n',
            'UI/src/utils/deep.js': None,
        })
        self.assertFormat(report)
        categories = {item['path']: (item['category'], item['workflows']) for item in report['files']}
        self.assertEqual(categories['UI/src/components/NewThing.svelte'], ('behavior', ['beta']))
        self.assertEqual(categories['UI/src/utils/deep.js'], ('behavior', ['beta']))
        self.assertIn('(removed)', self.entry(report, 'UI/src/utils/deep.js')['reason'])
        self.assertIn('(added)', self.entry(report, 'UI/src/components/NewThing.svelte')['reason'])
        self.assertEqual(report['affected_workflows'], ['beta'])

        self.repo.base = self.repo.git('rev-parse', 'HEAD')
        self.repo.git('mv', 'selfhost/tests/browser/manuale/capture-helper.mjs',
                      'selfhost/tests/browser/manuale/helper.mjs')
        report = self.repo.change({'selfhost/tests/browser/manuale/alpha.mjs':
                                   "import {scenario} from './scenario.mjs';\nimport {helper} from './helper.mjs';\n"})
        paths = {item['path']: item for item in report['files']}
        self.assertIn('selfhost/tests/browser/manuale/capture-helper.mjs', paths)
        self.assertIn('selfhost/tests/browser/manuale/helper.mjs', paths)
        self.assertEqual(paths['selfhost/tests/browser/manuale/capture-helper.mjs']['workflows'], ['alpha'])
        self.assertEqual(report['affected_workflows'], ['alpha'])

        self.repo.base = self.repo.git('rev-parse', 'HEAD')
        report = self.repo.change({'BE/application/utils/new_helper.py': 'def helper():\n    return 1\n'})
        self.assertEqual(self.entry(report, 'BE/application/utils/new_helper.py')['category'], 'unrelated')

    def test_unknown_files_fail_closed_with_diagnostics(self):
        report = self.repo.classify('newtop/tool.py')
        item = self.entry(report, 'newtop/tool.py')
        self.assertEqual(item['category'], 'unknown')
        self.assertIn('impact-map.json', item['reason'])
        self.assertEqual(report['mode'], 'full')
        self.assertTrue(report['full_verification_required'])
        self.assertEqual(report['affected_workflows'], ['alpha', 'beta'])
        self.assertTrue(report['checks']['security'])
        report = self.repo.classify('BE/application/views/gamma_views.py')
        item = self.entry(report, 'BE/application/views/gamma_views.py')
        self.assertEqual(item['category'], 'unknown')
        self.assertIn('-authored-workflows.mjs', item['reason'])
        self.assertIn('Full verification required', ' '.join(report['reasons']))

    def test_unknown_wins_over_everything_else(self):
        report = self.repo.classify('UI/src/routes/alpha/Alpha.svelte', 'manuale/faq/beta.mdx', 'newtop/x')
        self.assertEqual(report['mode'], 'full')
        self.assertEqual(report['reused_workflows'], [])

    def test_reviewed_prose_source_and_reference_module(self):
        report = self.repo.classify('BE/application/reviewed_only.py')
        self.assertEqual(self.entry(report, 'BE/application/reviewed_only.py')['category'], 'docs-text')
        self.assertEqual(report['mode'], 'content')
        self.assertEqual(report['affected_pages'], ['manuale/faq/alpha.mdx', 'manuale/faq/beta.mdx'])
        report = self.repo.classify('docs/manuale/alpha-recipes.mjs')
        self.assertEqual(self.entry(report, 'docs/manuale/alpha-recipes.mjs')['category'], 'docs-text')

    def test_registry_and_binding_changes_are_compared_per_entry(self):
        registry = json.loads(FILES['docs/manuale/recipes.json'])
        registry['recipes'][1]['checkpoints'].append({'id': 'two', 'caption': 'Two'})
        report = self.repo.change({'docs/manuale/recipes.json': json.dumps(registry, indent=2)})
        self.assertEqual(self.entry(report, 'docs/manuale/recipes.json')['workflows'], ['beta'])
        self.assertEqual(report['affected_workflows'], ['beta'])
        self.assertEqual(self.repo.classify('docs/manuale/recipes.json')['affected_workflows'], ['alpha', 'beta'])

        self.repo.base = self.repo.git('rev-parse', 'HEAD')
        evidence = json.loads(FILES['manuale/.manuale-evidence.json'])
        evidence['sections']['faq/beta.mdx#intro']['content_sha256'] = '3'
        report = self.repo.change({'manuale/.manuale-evidence.json': json.dumps(evidence)})
        self.assertEqual(report['mode'], 'content')
        self.assertEqual(report['affected_pages'], ['manuale/faq/beta.mdx'])

    def test_workflow_definition_module_selects_defined_workflows(self):
        report = self.repo.classify('docs/manuale/beta-authored-workflows.mjs')
        self.assertEqual(report['affected_workflows'], ['beta'])

    def test_no_changes_is_unaffected(self):
        report = self.repo.classify()
        self.assertEqual(report['mode'], 'unaffected')
        self.assertEqual(report['files'], [])

    def test_workflow_dependencies(self):
        dependencies = impact.workflow_dependencies(self.repo.root, 'alpha')
        self.assertEqual(dependencies, sorted(set(dependencies)))
        for expected in ('UI/src/routes/alpha/Alpha.svelte', 'UI/src/components/AlphaWidget.svelte',
                         'BE/application/services/alpha_service.py', 'BE/application/utils/helpers.py',
                         'BE/application/views/stats_views.py', 'selfhost/tests/browser/manuale/capture-helper.mjs',
                         'UI/src/main.js', 'UI/src/components/Shell.svelte', 'BE/application/urls.py'):
            self.assertIn(expected, dependencies)
        for unexpected in ('UI/src/utils/deep.js', 'UI/src/components/Unused.svelte',
                           'BE/application/views/gamma_views.py', 'BE/application/utils/legacy_helper.py'):
            self.assertNotIn(unexpected, dependencies)
        self.assertIn('UI/src/utils/deep.js', impact.workflow_dependencies(self.repo.root, 'beta'))
        with self.assertRaises(KeyError):
            impact.workflow_dependencies(self.repo.root, 'missing')
        # Content changes invalidate the cached graph.
        self.repo.write({'UI/src/routes/alpha/Alpha.svelte': "<script>import '../../utils/deep.js';</script>\n"})
        self.assertIn('UI/src/utils/deep.js', impact.workflow_dependencies(self.repo.root, 'alpha'))
        self.assertEqual(set(impact.all_workflow_dependencies(self.repo.root)), {'alpha', 'beta'})

    def test_unavailable_base_falls_back_to_head_only_analysis(self):
        report = impact.analyze(self.repo.root, ['UI/src/utils/deep.js'], base='0' * 40, head=None)
        self.assertEqual(report['affected_workflows'], ['beta'])
        self.assertIn('unavailable', report['reasons'][0])

    def test_cli_writes_the_report(self):
        head = self.repo.base
        self.repo.write({'UI/src/routes/alpha/Alpha.svelte': FILES['UI/src/routes/alpha/Alpha.svelte'] + '<p/>\n'})
        head = self.repo.commit('alpha')
        output = self.repo.root / 'impact.json'
        result = subprocess.run([sys.executable, str(SCRIPTS / 'manuale_impact.py'), '--root', str(self.repo.root),
                                 '--base', self.repo.base, '--head', head, '--changed-file', 'docs/architecture/notes.md',
                                 '--output', str(output)], capture_output=True, text=True, env=GIT_ENV)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(output.read_text())
        self.assertFormat(report)
        self.assertEqual([item['path'] for item in report['files']],
                         ['UI/src/routes/alpha/Alpha.svelte', 'docs/architecture/notes.md'])
        self.assertEqual(report['affected_workflows'], ['alpha'])
        self.assertIn('mode=behavioral', result.stderr)

    def test_reports_are_deterministic(self):
        paths = ['UI/src/utils/deep.js', 'manuale/faq/alpha.mdx', 'BE/application/agent/core.py']
        first = self.repo.classify(*paths)
        impact._CONTEXTS.clear()
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(self.repo.classify(*reversed(paths)), sort_keys=True))

    def test_manuale_runner_uses_impact_selection(self):
        runner = load_runner()
        registry = runner.recipe_registry(self.repo.root)
        decisions, report = [], {}
        selected = runner.select_affected(registry, ['UI/src/components/Unused.svelte'], root=self.repo.root,
                                          decisions=decisions, report=report)
        self.assertEqual(selected, [])
        self.assertEqual(decisions[0]['reason'], 'unknown-production-dependency')  # legacy decision retained
        self.assertEqual(report['mode'], 'unaffected')
        self.assertEqual(runner.select_affected(registry, ['UI/src/utils/deep.js'], root=self.repo.root), ['beta'])
        self.assertEqual(runner.select_affected(registry, ['newtop/x'], root=self.repo.root), ['alpha', 'beta'])
        self.assertEqual(runner.select_affected(registry, [], ['faq/alpha.mdx'], root=self.repo.root), [])
        # Snapshots without the analyzer keep the registered-recipe selection.
        (self.repo.root / impact.MAP_PATH).unlink()
        self.assertEqual(runner.select_affected(registry, ['UI/src/components/Unused.svelte'], root=self.repo.root),
                         ['alpha', 'beta'])


class ImpactRepositoryTest(unittest.TestCase):
    """Smoke checks against the real registry and maintained impact map."""

    @classmethod
    def setUpClass(cls):
        started = time.monotonic()
        cls.dependencies = impact.all_workflow_dependencies(ROOT)
        cls.seconds = time.monotonic() - started

    def test_every_registered_workflow_has_dependencies(self):
        registry = json.loads((ROOT / impact.REGISTRY_PATH).read_text())
        self.assertEqual(set(self.dependencies), {recipe['id'] for recipe in registry['recipes']})
        for identifier, dependencies in self.dependencies.items():
            self.assertEqual(dependencies, sorted(set(dependencies)))
            recipe = next(item for item in registry['recipes'] if item['id'] == identifier)
            self.assertIn(recipe['script'], dependencies)
            for source in recipe['sources']:
                if (ROOT / source).is_file():
                    self.assertIn(source, dependencies)
        self.assertIn('UI/src/routes/profile/sections/TwoFactor.svelte',
                      impact.workflow_dependencies(ROOT, 'account-two-factor'))

    def test_real_repository_classification(self):
        report = impact.analyze(ROOT, ['docs/architecture/overview.md', 'TESTS/README.md'], base=None, head=None)
        self.assertEqual(report['mode'], 'unaffected')
        self.assertEqual(len(report['reused_workflows']), len(self.dependencies))
        report = impact.analyze(ROOT, ['UI/src/routes/profile/sections/TwoFactor.svelte'], base=None, head=None)
        self.assertEqual(report['mode'], 'behavioral')
        self.assertIn('account-two-factor', report['affected_workflows'])
        self.assertLess(len(report['affected_workflows']), len(self.dependencies))
        report = impact.analyze(ROOT, ['BE/application/agent/core.py'], base=None, head=None)
        self.assertEqual((report['mode'], report['checks']['bot']), ('unaffected', True))
        report = impact.analyze(ROOT, ['BE/core/middleware.py'], base=None, head=None)
        self.assertTrue(report['full_verification_required'])

    def test_every_tracked_top_level_area_is_mapped(self):
        """New top-level areas must be classified explicitly; this lists those that would be unknown."""
        tracked = subprocess.run(['git', '-C', str(ROOT), 'ls-files'], capture_output=True, text=True).stdout.split()
        areas = sorted({path.split('/')[0] for path in tracked})
        report = impact.analyze(ROOT, [area + '/__impact_probe__' if (ROOT / area).is_dir() else area
                                       for area in areas], base=None, head=None)
        unknown = [item['path'] for item in report['files'] if item['category'] == 'unknown']
        self.assertTrue(set(unknown) <= {'BE/__impact_probe__', 'UI/__impact_probe__', 'selfhost/__impact_probe__',
                                         'docs/__impact_probe__', '.github/__impact_probe__',
                                         'manuale/__impact_probe__'}, unknown)


if __name__ == '__main__':
    unittest.main()
