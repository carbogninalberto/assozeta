"""The browser-child retry: its classification, and how the three drivers wire it in.

The wiring check reads each driver's source: inside the function that retries its browser child,
the name bound to the per-attempt log must not be a parameter or be used outside the retry loop.
A collision there rebinds a name the rest of the driver still needs (CI run on 5d53a311 lost the
collaborator report root that way).

Run: python3 -B -m unittest -v TESTS.adapters.django.test_browser_retry
"""
import ast
import io
from pathlib import Path
import tempfile
import unittest

from TESTS.adapters.django import browser_retry

HERE = Path(__file__).resolve().parent
DRIVERS = ('collaborator_list_browser.py', 'impersonation_browser.py', 'instance_pwa_browser.py')


def retry_loops(tree):
    """(function, loop, attempt-log names) for every `for attempt in (1, 2)` that calls attempt_log."""
    found = []
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for loop in ast.walk(function):
            if not (isinstance(loop, ast.For) and isinstance(loop.target, ast.Name) and loop.target.id == 'attempt'):
                continue
            names = {target.id for node in ast.walk(loop) if isinstance(node, ast.Assign)
                     and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute)
                     and node.value.func.attr == 'attempt_log' for target in node.targets
                     if isinstance(target, ast.Name)}
            if names:
                found.append((function, loop, names))
    return found


class DriverWiring(unittest.TestCase):
    def test_each_driver_retries_its_child_through_browser_retry(self):
        for driver in DRIVERS:
            with self.subTest(driver=driver):
                self.assertTrue(retry_loops(ast.parse((HERE / driver).read_text())), f'{driver} has no retry loop')

    def test_the_attempt_log_name_collides_with_nothing_in_the_driver(self):
        for driver in DRIVERS:
            tree = ast.parse((HERE / driver).read_text())
            for function, loop, names in retry_loops(tree):
                parameters = {argument.arg for argument in ast.walk(function.args) if isinstance(argument, ast.arg)}
                inner = {id(node) for node in ast.walk(loop)}
                for name in names:
                    with self.subTest(driver=driver, function=function.name, name=name):
                        self.assertNotIn(name, parameters, f'{name} rebinds a parameter of {function.name}')
                        outside = sorted(node.lineno for node in ast.walk(function)
                                         if isinstance(node, ast.Name) and node.id == name and id(node) not in inner)
                        self.assertEqual(outside, [], f'{name} is also used outside the retry loop (lines {outside})')


class Classification(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.checkpoints = self.work / 'checkpoints'
        self.checkpoints.mkdir()

    def retry(self, text, attempt=1, checkpoint=False):
        if checkpoint:
            (self.checkpoints / '1.json').write_text('{}')
        path = browser_retry.attempt_log(self.work, 'child', attempt)
        path.write_text(text)
        log = io.StringIO()
        decision = browser_retry.retry(log, path, attempt, self.checkpoints)
        self.assertIn(text, log.getvalue(), 'the child output always reaches the driver log')
        return decision

    def test_network_class_failures_before_any_checkpoint_retry_once(self):
        self.assertTrue(self.retry('page.goto: net::ERR_NETWORK_CHANGED at http://fixture.invalid/'))
        self.assertTrue(self.retry('page.goto: Timeout 15000ms exceeded.'))
        self.assertFalse(self.retry('page.goto: net::ERR_NETWORK_CHANGED', attempt=2))

    def test_content_assertions_and_later_failures_never_retry(self):
        self.assertFalse(self.retry('Error: expect(page).toHaveTitle(expected)\nExpected string: "Club"'))
        self.assertFalse(self.retry('net::ERR_CONNECTION_RESET\nAssertionError: rows differ'))
        self.assertFalse(self.retry('net::ERR_NETWORK_CHANGED', checkpoint=True))
        self.assertFalse(self.retry('TypeError: value is undefined'))


if __name__ == '__main__':
    unittest.main()
