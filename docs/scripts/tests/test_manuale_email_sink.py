"""Exercise the actual settings branch without importing unrelated Django dependencies."""
import ast
from pathlib import Path
import types
import unittest

ROOT = Path(__file__).resolve().parents[3]
tree = ast.parse((ROOT / 'BE/core/settings.py').read_text())
branch = next(node for node in tree.body if isinstance(node, ast.If)
              and isinstance(node.test, ast.Compare) and ast.unparse(node.test) == "'test' in sys.argv")
code = compile(ast.Module(body=[branch], type_ignores=[]), 'core/settings.py:email-branch', 'exec')


class ManualEmailSinkTests(unittest.TestCase):
    def backend(self, mode, run, argv=('manage.py', 'runserver')):
        state = {'sys': types.SimpleNamespace(argv=argv), 'MANUAL_RUN_ID': run,
                 'ASSOZETA_DEPLOYMENT_MODE': mode, 'EMAIL_BACKEND': 'instance.email_configuration.EmailBackend'}
        exec(code, state)
        return state['EMAIL_BACKEND']

    def test_only_owned_development_and_existing_tests_use_memory(self):
        production = 'instance.email_configuration.EmailBackend'
        local = 'django.core.mail.backends.locmem.EmailBackend'
        self.assertEqual(self.backend('development', 'fixture-owned'), local)
        self.assertEqual(self.backend('production', 'fixture-owned'), production)
        self.assertEqual(self.backend('development', ''), production)
        self.assertEqual(self.backend('production', '', ('manage.py', 'test')), local)
