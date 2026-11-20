"""Phase 2 (internal references): PDFs render with their assets from the active API in each mode.

Boundary: the renderer built from this checkout's selfhost/renderer (real
Chrome Headless Shell and its host trust checks), configured with the
renderer environment the distribution's Compose renders, on a private
network where only the active API resolves: a marker holding that service's
Compose name and network aliases on `renderer_egress`, serving a document
whose stylesheet and image are same-origin assets. The renderer is asked
for `${CURRENT_HOST_PUPPETEER}/document/...` exactly as Django
(application/utils/printing.py) and Go (internal/pdfrender) compose it from
the installation's generated .env. The marker's own access log proves the
document and every asset came from the active runtime.

Needs a Docker daemon; builds `assozeta-handoff-renderer:<content hash>`
from selfhost/renderer on first use (several minutes; cached afterwards).

    python3 -B -m TESTS.handoff.run_expected_red TESTS.handoff.test_p2_renderer
"""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import uuid

from TESTS.handoff.p2_harness import ACTIVE_API, aliases, configured_installation, env_values, render
from TESTS.handoff.topology import ROOT, caddy_image

RENDERER = ROOT / 'selfhost/renderer'
DOCUMENT = ('<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="/static/fixture.css">'
            '</head><body><h1>Fixture invoice</h1><img src="/media/fixture.svg" width="40" height="40"></body></html>')
ASSETS = {'document/fixture.html': DOCUMENT, 'static/fixture.css': 'h1 { color: #123456; }',
          'media/fixture.svg': '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40">'
                               '<rect width="40" height="40" fill="#654321"/></svg>'}
REQUEST = r'''
const target = process.argv[1];
(async () => {
  const response = await fetch('http://127.0.0.1:3000/render', {method: 'POST',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify({type: 'pdf', url: target, pdf: {format: 'A4'}})});
  const body = Buffer.from(await response.arrayBuffer());
  console.log(JSON.stringify({status: response.status, type: response.headers.get('content-type'),
    magic: body.subarray(0, 5).toString('latin1'), error: response.ok ? null : body.toString('utf8').slice(0, 300)}));
})().catch(error => console.log(JSON.stringify({status: null, error: String(error)})));
'''


def renderer_image():
    digest = hashlib.sha256()
    for path in sorted(RENDERER.iterdir()):
        if path.is_file():
            digest.update(path.name.encode() + b'\0' + path.read_bytes())
    tag = f'assozeta-handoff-renderer:{digest.hexdigest()[:12]}'
    if subprocess.run(['docker', 'image', 'inspect', tag], capture_output=True).returncode != 0:
        subprocess.run(['docker', 'build', '-q', '-t', tag, str(RENDERER)], check=True, capture_output=True,
                       timeout=1800)
    return tag


class PdfRendering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image = renderer_image()

    def render_pdf(self, mode):
        directory = tempfile.TemporaryDirectory(prefix='assozeta-p2-renderer-')
        self.addCleanup(directory.cleanup)
        work = Path(directory.name)
        (work / 'installation').mkdir()
        installation = configured_installation(work / 'installation')
        target = env_values(installation.env_file)['CURRENT_HOST_PUPPETEER'].rstrip('/') + '/document/fixture.html'
        services = render(installation, mode)['services']
        api = ACTIVE_API[mode]
        site = work / 'site'
        for name, content in ASSETS.items():
            (site / name).parent.mkdir(parents=True, exist_ok=True)
            (site / name).write_text(content)
        (work / 'marker.Caddyfile').write_text(
            '{\n    admin off\n}\n:8000 {\n    log {\n        output stdout\n        format json\n    }\n'
            f'    header X-Fixture-Backend "{api}"\n    root * /srv\n    file_server\n}}\n')
        suffix = uuid.uuid4().hex[:10]
        network, marker, renderer = f'assozeta-p2-render-{suffix}', f'p2-{api}-{suffix}', f'p2-renderer-{suffix}'
        log = (work / 'docker.log').open('w')
        self.addCleanup(log.close)

        def docker(*arguments, check=True):
            return subprocess.run(['docker', *arguments], check=check, stdout=subprocess.PIPE, stderr=log,
                                  text=True, timeout=180).stdout.strip()
        self.addCleanup(subprocess.run, ['docker', 'network', 'rm', network], stdout=log, stderr=log, timeout=60)
        self.addCleanup(subprocess.run, ['docker', 'rm', '-f', marker, renderer], stdout=log, stderr=log, timeout=60)
        docker('network', 'create', '--internal', network)
        names = {api} | aliases(services[api], 'renderer_egress')
        docker('run', '--pull=never', '-d', '--name', marker, '--network', network,
               *[argument for name in sorted(names) for argument in ('--network-alias', name)],
               '-v', f'{work / "marker.Caddyfile"}:/etc/caddy/Caddyfile:ro', '-v', f'{site}:/srv:ro', caddy_image())
        environment = {key: str(value) for key, value in (services['renderer'].get('environment') or {}).items()
                       if value not in (None, '')}
        docker('run', '--pull=never', '-d', '--name', renderer, '--network', network, '--init', '--read-only',
               '--shm-size', '1g', '--tmpfs', '/tmp:size=512m,mode=1777', '--cap-drop', 'ALL',
               '--security-opt', 'no-new-privileges:true',
               *[argument for key, value in environment.items() for argument in ('-e', f'{key}={value}')], self.image)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            probe = subprocess.run(['docker', 'exec', renderer, 'node', '-e',
                                    "fetch('http://127.0.0.1:3000/healthz').then(r=>process.exit(r.ok?0:1),"
                                    "()=>process.exit(1))"], capture_output=True, timeout=30)
            if probe.returncode == 0:
                break
            time.sleep(1)
        output = docker('exec', renderer, 'node', '-e', REQUEST, target, check=False)
        lines = [line for line in output.splitlines() if line.startswith('{')]
        result = json.loads(lines[-1]) if lines else {'status': None, 'error': output[-300:]}
        served = []
        for line in docker('logs', marker).splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if 'request' in entry:
                served.append(entry['request'].get('uri'))
        return target, result, served

    def check(self, mode):
        target, result, served = self.render_pdf(mode)
        self.assertEqual((result.get('status'), result.get('magic')), (200, '%PDF-'),
                         f'PDF render of {target} failed in runtime {mode}: {result}')
        self.assertEqual(sorted(set(served) & {'/document/fixture.html', '/static/fixture.css', '/media/fixture.svg'}),
                         ['/document/fixture.html', '/media/fixture.svg', '/static/fixture.css'],
                         f'document or assets not fetched from the active API ({ACTIVE_API[mode]}) in runtime {mode}: '
                         f'{served}')

    def test_pdf_renders_with_assets_from_the_django_api(self):
        self.check('django')

    def test_pdf_renders_with_assets_from_the_go_api(self):
        self.check('go')


if __name__ == '__main__':
    unittest.main()
