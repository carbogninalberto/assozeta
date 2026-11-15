"""Optional front-proxy overlay: CLI, updater, Compose and Caddy contracts."""
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'selfhost/updater'), str(ROOT / 'BE/instance')]
import distribution
from common import EDGE_OVERLAY, compose_command

CLI = ROOT / 'selfhost/bin/assozeta'
DEFINITIONS = CLI.read_text().split('\ncommand=${1:-help}\n')[0]
INSTALLATION_ENV = ('COMPOSE_PROJECT_NAME=edge-test\n'
                    'STRIPE_PUBLIC_KEY=\n'
                    'STRIPE_WEBHOOK_SECRET=\n'
                    'INSTANCE_SETUP_TOKEN=test-token\n'
                    'GOOGLE_CALENDAR_REDIRECT_URL=http://localhost/callback\n')
FORWARDED = {'x-forwarded-for', 'x-forwarded-proto', 'x-forwarded-host'}
# The installation env file decides these; a developer shell must not.
OVERLAY_VARIABLES = ('ASSOZETA_EDGE_NETWORK', 'ASSOZETA_EDGE_ALIAS', 'ASSOZETA_TRUSTED_PROXIES')
HERMETIC = {key: value for key, value in os.environ.items() if key not in OVERLAY_VARIABLES}


def historical_managed_path(name):
    """selfhost/updater/distribution.py as shipped in v1.0.17."""
    path = PurePosixPath(name)
    return name in ('compose.yml', 'compose.updater.yml', '.env.example', '.env.dev.example') or path.parts[0] in ('bin', 'config', 'caddy', 'updater')


def git_tracked_files():
    """Every file a `tar -czf <archive> selfhost` bundle would carry."""
    result = subprocess.run(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard', 'selfhost'],
                            cwd=ROOT, capture_output=True)
    if result.returncode != 0:
        return []
    return sorted(path for path in result.stdout.decode().split('\0')
                  if path and (ROOT / path).is_file() and not (ROOT / path).is_symlink())


def release_archive(paths):
    """The published bundle, built hermetically instead of by the release workflow."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        for path in paths:
            source = ROOT / path
            data = source.read_bytes()
            member = tarfile.TarInfo(path)
            member.size = len(data)
            member.mode = source.stat().st_mode & 0o777
            archive.addfile(member, io.BytesIO(data))
    return buffer.getvalue()


def docker_available(*arguments):
    try:
        return subprocess.run(['docker', *arguments], capture_output=True).returncode == 0
    except OSError:
        return False


class EdgeCliSelectionTests(unittest.TestCase):
    """logs selects the overlay only for production with a configured network."""

    def fake_docker(self, root):
        docker = root / 'docker'
        docker.write_text(f'#!{sys.executable}\n' + '''import json, os, sys
args = sys.argv[1:]
with open(os.environ['CALLS'], 'a') as output:
    output.write(json.dumps(args) + '\\n')
if args[:2] == ['compose', 'version']:
    print(os.environ['FAKE_COMPOSE_VERSION'])
''')
        docker.chmod(0o755)
        return {**HERMETIC, 'PATH': str(root) + os.pathsep + os.environ['PATH'],
                'CALLS': str(root / 'calls')}

    def exercise(self, arguments, *, edge_line='', version='2.39.1'):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            environment = {**self.fake_docker(root), 'FAKE_COMPOSE_VERSION': version}
            installation = root / 'installation'
            (installation / 'bin').mkdir(parents=True)
            for name in ('compose.yml', 'compose.dev.yml'):
                shutil.copy2(ROOT / 'selfhost' / name, installation / name)
            (installation / 'config').mkdir()
            shutil.copy2(ROOT / 'selfhost/config/compose.edge.yml', installation / 'config/compose.edge.yml')
            shutil.copy2(CLI, installation / 'bin/assozeta')
            (installation / 'bin/assozeta').chmod(0o755)
            for name in ('.env', '.env.dev'):
                (installation / name).write_text(INSTALLATION_ENV + edge_line)
            environment.update({'ASSOZETA_INSTALL_ROOT': str(installation),
                                'ASSOZETA_ENV_FILE': str(installation / '.env'),
                                'ASSOZETA_DEV_ENV_FILE': str(installation / '.env.dev')})
            result = subprocess.run([str(installation / 'bin/assozeta'), *arguments],
                                    env=environment, text=True, capture_output=True)
            calls = (root / 'calls').read_text().splitlines() if (root / 'calls').exists() else []
            compose = [json.loads(line) for line in calls]
            return result, [call for call in compose if call[:2] == ['compose', '--env-file']], installation

    def production_call(self, edge_line):
        result, calls, installation = self.exercise(['logs', 'web'], edge_line=edge_line)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 1)
        return calls[-1], installation

    def test_blank_network_never_selects_the_overlay(self):
        for edge_line in ('', 'ASSOZETA_EDGE_NETWORK=\n', 'ASSOZETA_EDGE_NETWORK=""\n',
                          'ASSOZETA_EDGE_NETWORK=   \n'):
            with self.subTest(edge_line=edge_line):
                arguments, installation = self.production_call(edge_line)
                self.assertIn(str(installation / 'compose.yml'), arguments)
                self.assertNotIn(str(installation / 'config/compose.edge.yml'), arguments)

    def test_configured_network_appends_the_overlay_after_the_production_compose(self):
        for edge_line in ('ASSOZETA_EDGE_NETWORK=edge-test\n', 'ASSOZETA_EDGE_NETWORK="edge-test"\n'):
            with self.subTest(edge_line=edge_line):
                arguments, installation = self.production_call(edge_line)
                self.assertEqual(arguments[:3], ['compose', '--env-file', str(installation / '.env')])
                self.assertEqual(arguments[3:7], ['-f', str(installation / 'compose.yml'),
                                                  '-f', str(installation / 'config/compose.edge.yml')])

    def test_absent_network_keeps_the_exact_production_command(self):
        arguments, installation = self.production_call('')
        self.assertEqual(arguments[:6], ['compose', '--env-file', str(installation / '.env'),
                                         '-f', str(installation / 'compose.yml'), 'logs'])
        self.assertEqual(arguments.count('-f'), 1)
        self.assertEqual(arguments[-1], 'web')

    def test_development_compose_never_selects_the_overlay(self):
        result, calls, installation = self.exercise(['dev-compose', 'config', '--images'],
                                                    edge_line='ASSOZETA_EDGE_NETWORK=edge-test\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = calls[-1]
        self.assertIn(str(installation / 'compose.dev.yml'), arguments)
        self.assertNotIn(str(installation / 'config/compose.edge.yml'), arguments)

    def test_compose_before_2_24_refuses_the_overlay_but_not_plain_usage(self):
        result, calls, _ = self.exercise(['logs', 'web'], edge_line='ASSOZETA_EDGE_NETWORK=edge-test\n',
                                         version='2.23.0')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('v2.24.0', result.stderr)
        self.assertEqual(calls, [])
        result, _, _ = self.exercise(['logs', 'web'], version='2.23.0')
        self.assertEqual(result.returncode, 0, result.stderr)


@unittest.skipUnless(docker_available('compose', 'version'), 'docker compose is unavailable')
class EdgeComposeConfigTests(unittest.TestCase):
    """Real Compose rendering through the CLI: the overlay drops host ports."""

    def installation(self, directory):
        root = Path(directory)
        shutil.copy2(ROOT / 'selfhost/compose.yml', root / 'compose.yml')
        (root / 'config').mkdir()
        shutil.copy2(ROOT / 'selfhost/config/compose.edge.yml', root / 'config/compose.edge.yml')
        return root

    def render(self, root, extra=''):
        (root / '.env').write_text((ROOT / 'selfhost/.env.example').read_text() + extra)
        result = subprocess.run(['sh', '-c', DEFINITIONS + '\nprod_compose "$@"\n', 'test',
                                 'config', '--format', 'json'],
                                capture_output=True, text=True,
                                env={**HERMETIC, 'ASSOZETA_INSTALL_ROOT': str(root)})
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def configuration(self, extra=''):
        with TemporaryDirectory() as directory:
            return self.render(self.installation(directory), extra)

    def test_network_attaches_web_only_and_publishes_no_port(self):
        config = self.configuration('ASSOZETA_EDGE_NETWORK=edge-test\n')
        web = config['services']['web']
        self.assertEqual(web.get('ports', []), [])
        self.assertEqual(set(web['networks']), {'internal', 'edge'})
        self.assertEqual(web['networks']['edge']['aliases'], ['assozeta-web'])
        self.assertTrue(config['networks']['edge']['external'])
        self.assertEqual(config['networks']['edge']['name'], 'edge-test')
        for name in ('api', 'worker', 'postgres'):
            self.assertNotIn('edge', config['services'][name].get('networks') or {})

    def test_alias_is_configurable(self):
        config = self.configuration('ASSOZETA_EDGE_NETWORK=edge-test\nASSOZETA_EDGE_ALIAS=assozeta-other\n')
        self.assertEqual(config['services']['web']['networks']['edge']['aliases'], ['assozeta-other'])

    def test_empty_network_keeps_the_published_ports_without_the_overlay(self):
        with TemporaryDirectory() as directory:
            root = self.installation(directory)
            absent, empty = self.render(root), self.render(root, 'ASSOZETA_EDGE_NETWORK=\n')
        # The installation env file reaches containers wholesale, so an explicit
        # empty key is visible to them; it must change nothing else.
        for config in (absent, empty):
            for service in config['services'].values():
                (service.get('environment') or {}).pop('ASSOZETA_EDGE_NETWORK', None)
        self.assertEqual(absent, empty)
        web = absent['services']['web']
        self.assertEqual({port['published'] for port in web['ports']}, {'80', '443'})
        self.assertEqual(set(web['networks']), {'internal'})
        self.assertNotIn('edge', absent['networks'])
        self.assertEqual(web['environment']['ASSOZETA_TRUSTED_PROXIES'], '')

    def test_trusted_proxies_reach_the_web_service(self):
        self.assertEqual(self.configuration('')['services']['web']['environment']['ASSOZETA_TRUSTED_PROXIES'], '')
        config = self.configuration('ASSOZETA_TRUSTED_PROXIES=private_ranges\n')
        self.assertEqual(config['services']['web']['environment']['ASSOZETA_TRUSTED_PROXIES'], 'private_ranges')


class EdgeUpdaterTests(unittest.TestCase):
    def command(self, content, *, updater=False):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            env_file = root / '.env'
            env_file.write_text(content)
            return compose_command(root, env_file, updater=updater), root

    def test_overlay_follows_the_production_compose_only_when_the_network_is_set(self):
        for content, enabled in (('APP_URL=x\n', False),
                                 ('ASSOZETA_EDGE_NETWORK=\n', False),
                                 ('ASSOZETA_EDGE_NETWORK=edge-test\n', True)):
            with self.subTest(content=content):
                command, root = self.command(content)
                production = str(root / 'compose.yml')
                index = command.index(production)
                self.assertEqual(command[index - 1], '-f')
                if enabled:
                    self.assertEqual(command[index + 1:index + 3], ['-f', str(root / 'config/compose.edge.yml')])
                else:
                    self.assertNotIn(str(root / 'config/compose.edge.yml'), command)

    def test_updater_project_never_uses_the_application_overlay(self):
        for content in ('APP_URL=x\n', 'ASSOZETA_EDGE_NETWORK=edge-test\n'):
            with self.subTest(content=content):
                command, root = self.command(content, updater=True)
                self.assertIn(str(root / 'compose.updater.yml'), command)
                self.assertNotIn(str(root / 'config/compose.edge.yml'), command)

    def test_release_bundles_install_the_overlay(self):
        self.assertEqual(EDGE_OVERLAY, 'config/compose.edge.yml')
        self.assertTrue(distribution.managed_path('config/compose.edge.yml'))


@unittest.skipUnless(shutil.which('git'), 'git is unavailable')
class HistoricalUpdaterManifestTests(unittest.TestCase):
    """Updaters already in the field judge this bundle by the v1.0.17 rule."""

    @classmethod
    def setUpClass(cls):
        cls.paths = git_tracked_files()
        cls.files = distribution.unpack(release_archive(cls.paths))

    def test_overlay_is_installed_inside_the_managed_config_directory(self):
        self.assertIn('selfhost/config/compose.edge.yml', self.paths)
        self.assertIn('config/compose.edge.yml', self.files)
        self.assertNotIn('compose.edge.yml', self.files)

    def test_the_historical_rule_accepts_every_installed_file(self):
        manifest = {name: hashlib.sha256(data).hexdigest() for name, (data, _) in self.files.items()}
        offenders = [name for name in manifest if not historical_managed_path(name)]
        self.assertEqual(offenders, [])

    def test_the_current_rule_is_no_broader_than_the_historical_one(self):
        offenders = [relative for relative in (path.removeprefix('selfhost/') for path in self.paths)
                     if distribution.managed_path(relative) and not historical_managed_path(relative)]
        self.assertEqual(offenders, [])


@unittest.skipUnless(docker_available('info'), 'Docker is unavailable')
class EdgeCaddyfileTests(unittest.TestCase):
    """The real Caddy image trusts only the configured front proxy."""

    def adapted(self, trusted=None):
        image = re.search(r'^FROM (caddy:\S+) AS production$',
                          (ROOT / 'UI/Dockerfile').read_text(), re.M).group(1)
        arguments = ['docker', 'run', '--rm', '-e', 'SITE_ADDRESS=http://example.org']
        if trusted is not None:
            arguments += ['-e', f'ASSOZETA_TRUSTED_PROXIES={trusted}']
        arguments += ['-v', f'{ROOT}/selfhost/caddy/Caddyfile:/etc/caddy/Caddyfile:ro', image,
                      'sh', '-c', 'caddy validate --config /etc/caddy/Caddyfile >&2 && '
                      'caddy adapt --config /etc/caddy/Caddyfile']
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def trusted_ranges(self, configuration):
        servers = configuration['apps']['http']['servers']
        self.assertTrue(servers)
        return [address for server in servers.values()
                for address in (server.get('trusted_proxies') or {}).get('ranges') or []]

    def handlers(self, node):
        if isinstance(node, dict):
            yield node
            for value in node.values():
                yield from self.handlers(value)
        elif isinstance(node, list):
            for value in node:
                yield from self.handlers(value)

    def test_unset_trusts_no_proxy(self):
        for server in self.adapted()['apps']['http']['servers'].values():
            self.assertFalse((server.get('trusted_proxies') or {}).get('ranges'))

    def test_private_ranges_trusts_the_private_networks(self):
        ranges = self.trusted_ranges(self.adapted('private_ranges'))
        self.assertIn('10.0.0.0/8', ranges)
        self.assertIn('172.16.0.0/12', ranges)

    def test_explicit_ranges_are_trusted_verbatim(self):
        self.assertEqual(self.trusted_ranges(self.adapted('10.0.0.0/8 192.168.1.0/24')),
                         ['10.0.0.0/8', '192.168.1.0/24'])

    def test_reverse_proxy_handlers_never_override_forwarded_headers(self):
        for handler in self.handlers(self.adapted('private_ranges')):
            if handler.get('handler') != 'reverse_proxy':
                continue
            request = (handler.get('headers') or {}).get('request') or {}
            overridden = {name.lower() for operation in ('set', 'add', 'delete')
                          for name in request.get(operation) or {}}
            self.assertFalse(overridden & FORWARDED, overridden)


if __name__ == '__main__':
    unittest.main()
