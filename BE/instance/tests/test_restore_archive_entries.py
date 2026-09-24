import stat
import zipfile

from django.test import SimpleTestCase, override_settings

from instance.restore.archive import Archive, RestoreError


class ArchiveEntryTests(SimpleTestCase):
    def entry(self, name, size=0):
        entry = zipfile.ZipInfo(name)
        entry.file_size = size
        return entry

    def test_large_entry_limits_and_overrides_report_the_offending_file(self):
        # Check real metadata boundaries without allocating multi-GB fixtures.
        archive = Archive('unused.zip')
        for name, limit, setting in (
            ('data/42_signatures.json', 512 * 1024**2, 'DATA_RESTORE_MAX_JSON_BYTES'),
            ('files/documents/id/large.pdf', 5 * 1024**3, 'DATA_RESTORE_MAX_FILE_BYTES'),
        ):
            with self.subTest(name=name):
                archive._validate_entries([self.entry(name, limit)])
                with self.assertRaises(RestoreError) as caught:
                    archive._validate_entries([self.entry(name, limit + 1)])
                self.assertIn(name, str(caught.exception))
                self.assertIn(setting, str(caught.exception))
                with override_settings(**{setting: 123}):
                    archive._validate_entries([self.entry(name, 123)])
                    with self.assertRaisesRegex(RestoreError, 'limite 123 byte'):
                        archive._validate_entries([self.entry(name, 124)])

    def test_only_literal_media_basename_backslashes_are_accepted(self):
        archive = Archive('unused.zip')
        archive._validate_entries([self.entry(r'files/documents/id/legacy\.pdf')])
        invalid = [
            '../escape.pdf', '/absolute.pdf', 'C:/escape.pdf',
            r'files/documents/id/..\escape.pdf',
            r'files/documents/id/a\..\escape.pdf',
            r'files/documents/id/\absolute.pdf',
            r'files/documents/id/C:\escape.pdf',
            r'files/documents/id/\\server\share.pdf',
            r'files/docu\ments/id/file.pdf',
            r'files/documents/i\d/file.pdf',
            r'data/legacy\.json',
        ]
        for name in invalid:
            with self.subTest(name=name):
                with self.assertRaises(RestoreError) as caught:
                    archive._validate_entries([self.entry(name)])
                self.assertIn(name, str(caught.exception))

    def test_other_archive_guards_still_reject_with_specific_errors(self):
        archive = Archive('unused.zip')
        encrypted = self.entry('encrypted.pdf')
        encrypted.flag_bits |= 1
        symlink = self.entry('link.pdf')
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        for entry, reason in ((encrypted, 'cifrato'), (symlink, 'simbolico')):
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(RestoreError, reason):
                    archive._validate_entries([entry])
        with self.assertRaisesRegex(RestoreError, 'duplicato.*same.pdf'):
            archive._validate_entries([self.entry('same.pdf'), self.entry('same.pdf')])
        with override_settings(DATA_RESTORE_MAX_EXPANDED_BYTES=100):
            with self.assertRaisesRegex(RestoreError, 'DATA_RESTORE_MAX_EXPANDED_BYTES'):
                archive._validate_entries([self.entry('one.pdf', 60), self.entry('two.pdf', 60)])
