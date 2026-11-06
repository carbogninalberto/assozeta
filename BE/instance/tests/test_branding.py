from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
from django.template.loader import render_to_string
from datetime import datetime
from django.test import SimpleTestCase, override_settings
from application.templatetags.instance_branding import instance_brand_palette


class EmailBrandingTests(SimpleTestCase):
    @patch('application.templatetags.instance_branding.InstanceConfiguration.get_config')
    def test_every_transactional_template_uses_current_brand(self, get_config):
        for color, foreground in [('#087e54', '#ffffff'), ('#ffff00', '#000000')]:
            get_config.return_value = SimpleNamespace(primary_color=color, self_hosted=True, name='Club', logo_path='', updated_at=None)
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


@override_settings(APP_URL='https://club.example.test', WHITELABEL_NAME='Assozeta', IS_WHITELABEL=False)
@patch('application.templatetags.instance_branding.InstanceConfiguration.get_config')
class EmailLogoTests(SimpleTestCase):
    templates = Path(__file__).resolve().parents[2] / 'templates' / 'email'

    def render_all(self):
        for template in sorted(self.templates.rglob('*.html')):
            if template.name.startswith('_'):
                continue
            name = template.relative_to(self.templates).as_posix()
            with self.subTest(template=name):
                yield render_to_string('email/' + name, {})

    def test_self_hosted_instance_uses_its_uploaded_logo(self, get_config):
        get_config.return_value = SimpleNamespace(
            primary_color='#087e54', self_hosted=True, name='Polisportiva <Test>',
            logo_path='/api/instance/logo.png', updated_at=datetime(2026, 11, 6, 10, 0, 0),
        )
        for html in self.render_all():
            self.assertNotIn('bakney.com', html)
            self.assertIn('src="https://club.example.test/api/instance/logo.png?v=20261106100000000000"', html)
            self.assertIn('alt="Polisportiva &lt;Test&gt;"', html)
            self.assertIn('Assozeta', html)

    def test_self_hosted_instance_without_logo_shows_its_name(self, get_config):
        get_config.return_value = SimpleNamespace(
            primary_color='#087e54', self_hosted=True, name='Polisportiva Test',
            logo_path='', updated_at=None,
        )
        for html in self.render_all():
            self.assertNotIn('bakney.com', html)
            self.assertNotIn('<img', html)
            self.assertIn('Polisportiva Test</span>', html)

    def test_hosted_service_keeps_bakney_logo_unless_whitelabel(self, get_config):
        get_config.return_value = None
        for html in self.render_all():
            self.assertIn('src="https://bakney.com/images/logo.png"', html)
        with self.settings(IS_WHITELABEL=True):
            for html in self.render_all():
                self.assertNotIn('<img', html)
