"""The skip allowlist is strict even though retired records are not persisted."""
import io
import json
import unittest
import zipfile

from application.services.legacy_backup import skipped_records


class LegacyBackupPolicyTests(unittest.TestCase):
    def archive(self, specs, files):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            for name, value in files.items():
                archive.writestr(name, json.dumps(value))
        return zipfile.ZipFile(buffer), {'models_exported': specs}

    def test_reports_only_explicitly_retired_sections(self):
        with self.archive([], {})[0] as archive:
            self.assertEqual(skipped_records(archive, {'models_exported': []}), {})
        specs = [dict(name='BillingPlan', file='system/00_billing_plans.json', count=1),
                 dict(name='PreviewAndCustomFeatures', file='system/64_preview_features.json', count=2),
                 dict(name='Associate', file='data/07_associates.json', count=10)]
        archive, manifest = self.archive(specs, {specs[0]['file']: [{}], specs[1]['file']: [{}, {}]})
        with archive:
            self.assertEqual(skipped_records(archive, manifest), {'BillingPlan': 1, 'PreviewAndCustomFeatures': 2})

    def test_invalid_retired_sections_are_not_silently_ignored(self):
        good = dict(name='BillingPlan', file='system/00_billing_plans.json', count=1)
        for specs, contents in [([dict(good, count=2)], [{}]), ([good], ['invalid']),
                                ([good, good], [{}]), ([dict(good, file='../plans.json')], [{}]),
                                ([good], {'not': 'array'})]:
            with self.subTest(specs=specs, contents=contents):
                archive, manifest = self.archive(specs, {specs[0]['file']: contents})
                with archive, self.assertRaises(ValueError):
                    skipped_records(archive, manifest)
