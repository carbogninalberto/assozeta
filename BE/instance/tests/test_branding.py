from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
from django.template.loader import render_to_string
from django.test import SimpleTestCase
from application.templatetags.instance_branding import instance_brand_palette


class EmailBrandingTests(SimpleTestCase):
    @patch('application.templatetags.instance_branding.InstanceConfiguration.get_config')
    def test_every_transactional_template_uses_current_brand(self, get_config):
        for color, foreground in [('#087e54', '#ffffff'), ('#ffff00', '#000000')]:
            get_config.return_value = SimpleNamespace(primary_color=color, self_hosted=True)
            templates = Path(__file__).resolve().parents[2] / 'templates' / 'email' / 'account'
            for template in templates.glob('*.html'):
                with self.subTest(color=color, template=template.name):
                    html = render_to_string('email/account/' + template.name, {})
                    self.assertIn('background-color: ' + color, html)
                    self.assertIn('color: ' + foreground, html)
                    self.assertNotIn('#4338ca', html)
                    self.assertNotIn('#6366f1', html)
                    self.assertNotIn('var(--', html)

    @patch('application.templatetags.instance_branding.InstanceConfiguration.get_config')
    def test_invalid_color_and_legacy_instances_use_safe_default(self, get_config):
        for config in [None, SimpleNamespace(primary_color='red; color:blue', self_hosted=True),
                       SimpleNamespace(primary_color='#ff0000', self_hosted=False)]:
            get_config.return_value = config
            self.assertEqual(instance_brand_palette()['primary'], '#351dc2')
