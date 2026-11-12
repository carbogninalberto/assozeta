"""Static checks on the files the production images copy into place."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
MIME_TABLE = ROOT / 'TESTS' / 'fixtures' / 'mime.types'
MIME_COPY = 'COPY mime.types /etc/mime.types'


def lines(path):
    return path.read_text(encoding='utf-8').splitlines()


class MimeTableTests(unittest.TestCase):
    def test_image_tables_match_the_recorded_fixture_byte_for_byte(self):
        expected = MIME_TABLE.read_bytes()
        for image in ('BE', 'BE_GO'):
            with self.subTest(image=image):
                self.assertEqual((ROOT / image / 'mime.types').read_bytes(), expected)


class DjangoDockerfileTests(unittest.TestCase):
    def setUp(self):
        self.dockerfile = lines(ROOT / 'BE' / 'Dockerfile')

    def test_mime_table_is_copied_in_the_base_stage(self):
        self.assertIn(MIME_COPY, self.dockerfile)
        copy_at = self.dockerfile.index(MIME_COPY)
        production_at = self.dockerfile.index('FROM base AS production')
        self.assertLess(copy_at, production_at)

    def test_empty_upload_continue_probe_is_disabled(self):
        self.assertIn('BOTO_EXPERIMENTAL__NO_EMPTY_CONTINUE=true', '\n'.join(self.dockerfile))


class GoDockerfileTests(unittest.TestCase):
    def test_mime_table_is_copied_in_the_final_stage(self):
        dockerfile = lines(ROOT / 'BE_GO' / 'Dockerfile')
        starts = [index for index, line in enumerate(dockerfile) if line.startswith('FROM ')]
        self.assertTrue(starts, 'no FROM line')
        self.assertIn(MIME_COPY, dockerfile[starts[-1]:])


if __name__ == '__main__':
    unittest.main()
