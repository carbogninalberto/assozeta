"""Behavioral tests for the Django-specific resolver adapter's metadata."""
import functools
import unittest
from TESTS.adapters.django.inventory import methods, qualified


class RuntimeInventoryTests(unittest.TestCase):
    def test_function_method_decorator_is_inspected_through_wrappers(self):
        def view():
            pass
        request_method_list = ['GET']

        @functools.wraps(view)
        def wrapped():
            return request_method_list

        self.assertEqual(methods(wrapped), ['GET'])
        self.assertIsNone(methods(view))

    def test_class_methods_do_not_invent_put_or_delete(self):
        class View:
            http_method_names = ['get', 'post', 'put', 'delete', 'head', 'options']
            def get(self): pass
            def post(self): pass
            def options(self): pass
        def callback(): pass
        callback.cls = View
        self.assertEqual(methods(callback), ['GET', 'HEAD', 'OPTIONS', 'POST'])

    def test_drf_function_view_does_not_implicitly_admit_head(self):
        class View:
            http_method_names = ['get', 'options']
            def get(self): pass
            def options(self): pass
        def callback(): pass
        callback.cls = View
        self.assertEqual(methods(callback), ['GET', 'OPTIONS'])

    def test_composed_permissions_are_named_without_memory_addresses(self):
        class And: pass
        class Authenticated: pass
        class Owner: pass
        class Operand:
            operator_class = And
            op1_class = Authenticated
            op2_class = Owner
        value = qualified(Operand())
        self.assertIn('And(', value)
        self.assertIn('Authenticated', value)
        self.assertIn('Owner', value)
        self.assertNotIn('0x', value)


if __name__ == '__main__':
    unittest.main()
