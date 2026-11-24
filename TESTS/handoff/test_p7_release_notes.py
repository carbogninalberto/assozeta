"""Phase 7 part D: operator release notes for the runtime switch (CONTRACT P7 v1).

Boundary: selfhost/RUNTIME.md, shipped in the self-host bundle (the bundle is the selfhost/
directory) and linked from selfhost/README.md. English, like the other operator documents.
Sections (exact '## ' headings) and the facts each must state:
  ## Fresh installations      the effective default is Django ('default', 'Django').
  ## Existing installations   mixed/dual installs are adopted during `upgrade`: the instance setting
                              v1 selects Django and v2 selects Go; a browser's cached preference is not
                              an input; a blocked adoption preflight leaves the old release running
                              ('v1', 'v2', 'upgrade', 'preflight', 'browser').
  ## Migrations               the switch never migrates; schema changes happen only on upgrade
                              ('never', 'upgrade').
  ## Reversal                 how to go back either way and to recover an interrupted switch
                              (`./bin/assozeta runtime-switch django`, `./bin/assozeta runtime-switch go`,
                              `./bin/assozeta runtime-recover`, `./bin/assozeta status`).
Every `./bin/assozeta <command>` the notes name must be a command the shipped CLI accepts, and the
notes carry no URL with a real host (only example.invalid style placeholders).

Run: python3 -B -m unittest -v TESTS.handoff.test_p7_release_notes   (instant)
"""
import re
import unittest

from TESTS.handoff import p7_release as r

RED = r.RED
NOTES = r.ROOT / 'selfhost/RUNTIME.md'
CLI = r.ROOT / 'selfhost/bin/assozeta'
SECTIONS = {
    'Fresh installations': ('default', 'Django'),
    'Existing installations': ('v1', 'v2', 'Django', 'Go', 'upgrade', 'preflight', 'browser'),
    'Migrations': ('never', 'upgrade'),
    'Reversal': ('./bin/assozeta runtime-switch django', './bin/assozeta runtime-switch go',
                 './bin/assozeta runtime-recover', './bin/assozeta status'),
}


def cli_commands():
    text = CLI.read_text()
    block = text[text.rindex('case "$command" in'):]
    block = block[:block.index('\nesac')]
    commands = set()
    for match in re.finditer(r'^    ([a-z][a-z|-]*)\)', block, re.M):
        commands.update(match.group(1).split('|'))
    return commands


class ReleaseNotes(r.ContractCase, unittest.TestCase):
    def notes(self):
        if not NOTES.is_file():
            self.fail(f'{RED}: selfhost/RUNTIME.md (operator release notes for the runtime switch) does not exist')
        return NOTES.read_text()

    def sections(self):
        text, found = self.notes(), {}
        parts = re.split(r'^## +(.+?)\s*$', text, flags=re.M)
        for title, body in zip(parts[1::2], parts[2::2]):
            found[title] = body
        return found

    def test_each_audience_has_its_section_and_facts(self):
        sections = self.sections()
        for title, facts in SECTIONS.items():
            with self.subTest(section=title):
                self.assertIn(title, sections, f'section "## {title}" missing')
                missing = [fact for fact in facts if fact not in sections[title]]
                self.assertEqual(missing, [], f'"## {title}" does not state {missing}')

    def test_named_commands_exist_in_the_shipped_cli(self):
        named = set(re.findall(r'\./bin/assozeta ([a-z][a-z-]*)', self.notes()))
        self.assertTrue(named, 'the notes name no operator command')
        self.assertEqual(sorted(named - cli_commands()), [], 'the notes name commands the CLI does not accept')

    def test_no_real_hosts(self):
        hosts = re.findall(r'https?://([A-Za-z0-9.-]+)', self.notes())
        self.assertEqual([h for h in hosts if not h.endswith(('.invalid', '.example', 'example.com', 'localhost'))], [],
                         'the notes must not carry real hosts')

    def test_linked_from_the_operator_readme(self):
        self.notes()
        self.assertIn('RUNTIME.md', (r.ROOT / 'selfhost/README.md').read_text(),
                      'selfhost/README.md does not link the runtime release notes')


if __name__ == '__main__':
    unittest.main()
