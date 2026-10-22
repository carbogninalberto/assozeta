#!/usr/bin/env python3
"""Prepare the complete local manual for authoring; never produce capture proof."""
import argparse
import hashlib
import html
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import textwrap

ROOT = Path(__file__).resolve().parents[2]
MARKER = 'assozeta-manuale-placeholder'
IMAGE = re.compile(r'(?P<prefix>!\[[^\]]*\]\(|\bsrc=[\"\u0027])(?P<path>/images/[^\)\"\u0027]+)(?P<suffix>\)|[\"\u0027])')


def git(root, *arguments):
    return subprocess.run(['git', '-C', str(root), *arguments], check=True, capture_output=True, text=True).stdout.strip()


def digest(contents):
    return hashlib.sha256(contents).hexdigest()


def navigation_pages(value):
    pages = []
    def walk(node):
        if isinstance(node, dict):
            for key, child in node.items():
                if key == 'pages':
                    for entry in child:
                        if isinstance(entry, str):
                            relative = entry.removeprefix('/') + ('' if entry.endswith('.mdx') else '.mdx')
                            if relative not in pages:
                                pages.append(relative)
                        else:
                            walk(entry)
                else:
                    walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)
    walk(value.get('navigation', []))
    return pages


def owned(root, relative):
    target = (root / relative).resolve()
    if Path(relative).is_absolute() or not target.is_relative_to(root) or target == root:
        raise ValueError('Path escapes manual: ' + relative)
    return target


def svg_placeholder(title, original, caption=''):
    # Bound text inside the artboard, including unusually long chapter titles.
    title_lines = textwrap.wrap(caption or title, width=74, break_long_words=True)[:3]
    text = ''.join(f'<tspan x="960" dy="{0 if position == 0 else 38}">{html.escape(line)}</tspan>'
                   for position, line in enumerate(title_lines))
    title, original = html.escape(title), html.escape(original[:125])
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080" data-{MARKER}="true" role="img" aria-labelledby="title description">
  <title id="title">Screenshot da acquisire: {title}</title>
  <desc id="description">Immagine SVG provvisoria di 1920 per 1080 pixel. Deve essere sostituita da uno screenshot reale prima della verifica.</desc>
  <defs><linearGradient id="surface" x2="1" y2="1"><stop stop-color="#f5f3ff"/><stop offset="1" stop-color="#f8fafc"/></linearGradient></defs>
  <rect width="1920" height="1080" rx="32" fill="url(#surface)"/>
  <rect x="48" y="48" width="1824" height="984" rx="24" fill="#fff" stroke="#e5e7ef" stroke-width="2"/>
  <path d="M48 144H1872" stroke="#e5e7ef" stroke-width="2"/>
  <circle cx="92" cy="96" r="9" fill="#ddd6fe"/><circle cx="126" cy="96" r="9" fill="#e8e3fa"/><circle cx="160" cy="96" r="9" fill="#ede9fe"/>
  <text x="1816" y="105" text-anchor="end" font-family="Arial, sans-serif" font-size="22" fill="#7c879a">ASSOZETA · MANUALE</text>
  <rect x="876" y="332" width="168" height="128" rx="18" fill="#f0edff"/>
  <path d="M904 424l32-34 25 23 34-40 23 51H904z" fill="#b4a4ec"/><circle cx="923" cy="366" r="12" fill="#7160d6"/>
  <text x="960" y="534" text-anchor="middle" font-family="Arial, sans-serif" font-size="42" font-weight="700" fill="#253145">Screenshot da acquisire</text>
  <text x="960" y="590" text-anchor="middle" font-family="Arial, sans-serif" font-size="26" fill="#637083">{text}</text>
  <text x="960" y="742" text-anchor="middle" font-family="Arial, sans-serif" font-size="21" fill="#7c879a">Immagine provvisoria · non è una schermata dell’applicazione</text>
  <text x="104" y="981" font-family="Arial, sans-serif" font-size="19" fill="#7c879a">{original}</text>
  <text x="1816" y="981" text-anchor="end" font-family="Arial, sans-serif" font-size="19" fill="#7160d6">FULL HD · 1920 × 1080</text>
</svg>
'''


def initialize(output, *, reference_repository=None, reference_revision=None):
    """Bootstrap an existing owned snapshot in place; do not clone or change HEAD."""
    output = output.resolve()
    if not output.is_dir():
        raise ValueError('Initialize requires an existing run-owned manual snapshot')
    if any((output / name).exists() for name in ('.manuale-authoring.json', '.manuale-evidence.json', 'AUTHORING.md')) or any(output.rglob('*.placeholder.svg')):
        raise ValueError('Refusing to overwrite preexisting or partial manual authoring state')
    revision = git(output, 'rev-parse', 'HEAD')
    if reference_revision is not None and reference_revision != revision:
        raise ValueError('Authoring input revision must match the selected manual snapshot HEAD')
    configuration = json.loads((output / 'mint.json').read_text())
    pages = navigation_pages(configuration)
    spec = importlib.util.spec_from_file_location('manual_authoring_index', ROOT / 'BE/application/manuale/index.py')
    index = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(index)
    # Validate the complete source before creating or replacing any draft.
    sources = [(relative, owned(output, relative).read_bytes().decode('utf-8')) for relative in pages]
    page_sections = {relative: index.sections(text) for relative, text in sources}
    for relative, original in sources:
        for match in IMAGE.finditer(original):
            source = match['path'].strip().removeprefix('/')
            owned(output, source)
            owned(output, str(Path(source).with_suffix('.placeholder.svg')))
    placeholders = {}
    drafted = []
    for relative, original in sources:
        match = re.search(r'^title:\s*(.+?)\s*$', original, re.M)
        title = match[1].strip().strip('\"\u0027') if match else relative
        committed = subprocess.run(['git', '-C', str(output), 'show', revision + ':' + relative], capture_output=True)
        def replace_image(match):
            source = match['path'].strip().removeprefix('/')
            owned(output, source)  # A missing historical image can still be planned.
            placeholder = str(Path(source).with_suffix('.placeholder.svg'))
            record = placeholders.setdefault(source, {'original_path': source, 'placeholder_path': placeholder,
                'status': 'placeholder', 'width': 1920, 'height': 1080, 'verified': False, 'pages': []})
            if relative not in record['pages']:
                record['pages'].append(relative)
            target = owned(output, placeholder)
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_text(svg_placeholder(title, source))
            record['sha256'] = digest(target.read_bytes())
            return match['prefix'] + '/' + placeholder + match['suffix']
        text = IMAGE.sub(replace_image, original)
        owned(output, relative).write_text(text)
        drafted.append({'path': relative, 'title': title, 'status': 'draft', 'original_sha256': digest(original.encode()),
            'reference_source_state': 'committed' if committed.returncode == 0 and committed.stdout == original.encode() else 'working-tree',
            'draft_sha256': digest(text.encode()), 'sections': [{'id': key, 'title': section['title'],
                'source_review': 'pending', 'workflow': 'pending', 'screenshots': 'pending', 'retrieval': 'pending'}
                for key, section in page_sections[relative].items()]})
    manifest = {'format': 1, 'purpose': 'authoring-only',
        'reference_repository': str(Path(reference_repository).resolve()) if reference_repository else str(output),
        'reference_revision': revision, 'branch': git(output, 'branch', '--show-current'),
        'initialization': 'existing-owned-snapshot', 'verified': False, 'publication_ready': False,
        'capture_settings': {'width': 1920, 'height': 1080, 'device_scale_factor': 1,
                             'crops': 'retain the real Full HD master and record crop coordinates'},
        'pages': drafted, 'placeholders': list(placeholders.values()),
        'summary': {'navigation_pages': len(drafted), 'draft_sections': sum(len(page['sections']) for page in drafted),
                    'svg_placeholders': len(placeholders)}}
    (output / '.manuale-authoring.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    (output / 'AUTHORING.md').write_text('''# Bozza del manuale Assozeta

Questa copia conserva le pagine, i titoli e i componenti MDX del manuale originale.
Le immagini `.placeholder.svg` sono provvisorie e misurano **1920 × 1080**.
Non rappresentano l’applicazione e non sono evidenze di uno scenario eseguito.

Per ogni sezione: controllare UI, backend e permessi; correggere le affermazioni;
eseguire il flusso con la fixture isolata; sostituire gli SVG con PNG reali;
conservare l’originale Full HD e le coordinate degli eventuali ritagli.
La verifica finale richiede manifest, codice, scenari, rendering, MCP e risposte.

Il file `.manuale-authoring.json` elenca tutte le pagine e le immagini da completare.
Non pubblicare questa bozza e non installarla come corpus verificato.
''')
    return manifest['summary']


def create(reference, output, branch):
    """Standalone authoring wrapper; the run runner uses initialize directly."""
    reference, output = reference.resolve(), output.resolve()
    if output == reference or output.is_relative_to(reference):
        raise ValueError('The original repository must remain separate from the authoring checkout')
    if output.exists():
        raise ValueError('Authoring output already exists; choose an empty destination')
    revision = git(reference, 'rev-parse', 'HEAD')
    pages = navigation_pages(json.loads((reference / 'mint.json').read_text()))
    sources = {relative: owned(reference, relative).read_bytes() for relative in pages}
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['git', 'clone', '--local', '--no-hardlinks', str(reference), str(output)], check=True, capture_output=True)
    git(output, 'checkout', '-b', branch, revision)
    remote = subprocess.run(['git', '-C', str(reference), 'remote', 'get-url', 'origin'], capture_output=True, text=True)
    if remote.returncode == 0:
        git(output, 'remote', 'set-url', 'origin', remote.stdout.strip())
    for relative, contents in sources.items():
        target = owned(output, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(contents)
    for relative in ('mint.json', 'CLAUDE.md', 'SCREENSHOTS-NEEDED.md'):
        if (reference / relative).exists():
            shutil.copyfile(reference / relative, output / relative)
    return initialize(output, reference_repository=reference, reference_revision=revision)


def reviewed_section_body(mdx, key, title):
    """Use the same introductory-section rule as the MDX publisher."""
    headings = list(re.finditer(r'^#{1,6}[ \t]+(.+?)[ \t]*$', mdx, re.M))
    matches = [i for i, heading in enumerate(headings) if heading[1] == title]
    if len(matches) == 1:
        position = matches[0]
        start = headings[position].start()
        end = headings[position + 1].start() if position + 1 < len(headings) else len(mdx)
    elif not matches and key.rsplit('#', 1)[-1] == 'introduzione' and title == 'Introduzione':
        frontmatter = re.match(r'^---\s*\n[\s\S]*?\n---\s*\n', mdx)
        start = frontmatter.end() if frontmatter else 0
        end = headings[0].start() if headings else len(mdx)
        if not mdx[start:end].strip():
            raise ValueError('Missing or ambiguous reviewed heading: ' + key)
    else:
        raise ValueError('Missing or ambiguous reviewed heading: ' + key)
    return re.sub(r'(/images/[^\s"\u0027)]+)\.placeholder\.svg', r'\1.png', mdx[start:end].strip())


def validate_authoring_input(output):
    """Validate existing reviewed MDX without refreshing or overwriting it."""
    output = output.resolve()
    sidecar_path = owned(output, '.manuale-evidence.json')
    authoring_path = owned(output, '.manuale-authoring.json')
    if not sidecar_path.exists():
        if authoring_path.exists() or (output / 'AUTHORING.md').exists() or any(output.rglob('*.placeholder.svg')):
            raise ValueError('Partial manual authoring state: reviewed content sidecar is missing')
        return False
    sidecar = json.loads(sidecar_path.read_text())
    if sidecar.get('format') != 1 or sidecar.get('purpose') != 'reviewed-content' or sidecar.get('verified') is not False or not isinstance(sidecar.get('sections'), dict) or not sidecar['sections']:
        raise ValueError('Invalid reviewed manual content bindings')
    if authoring_path.exists():
        authoring = json.loads(authoring_path.read_text())
        if authoring.get('format') != 1 or authoring.get('purpose') != 'authoring-only' or authoring.get('verified') is not False or authoring.get('publication_ready') is not False or not isinstance(authoring.get('pages'), list) or not authoring['pages']:
            raise ValueError('Invalid preexisting manual authoring state')
        for page in authoring['pages']:
            owned(output, page['path']).read_bytes()
    for key, binding in sidecar['sections'].items():
        if not isinstance(binding, dict) or '#' not in key or not binding.get('title') or binding.get('content_source') not in (None, 'authored-mdx'):
            raise ValueError('Invalid reviewed section binding: ' + str(key))
        relative, identifier = key.rsplit('#', 1)
        if not identifier or not relative.endswith('.mdx') or not re.fullmatch('[a-f0-9]{64}', binding.get('content_sha256', '')):
            raise ValueError('Invalid reviewed section identity/hash: ' + key)
        module = binding.get('recipe_module', '')
        if not module.startswith('docs/manuale/') or not module.endswith('.mjs') or not re.fullmatch('[a-f0-9]{64}', binding.get('recipe_sha256', '')):
            raise ValueError('Invalid reviewed recipe binding: ' + key)
        if digest(owned(ROOT, module).read_bytes()) != binding['recipe_sha256']:
            raise ValueError('Recipe changed; review existing content before preparation: ' + module)
        mdx = owned(output, relative).read_text()
        body = reviewed_section_body(mdx, key, binding['title'])
        if digest(body.encode()) != binding['content_sha256']:
            raise ValueError('Preserve changed MDX; review-content before preparation: ' + key)
    return True


def remaining(output):
    output = output.resolve()
    manifest = json.loads((output / '.manuale-authoring.json').read_text())
    references = []
    for page in manifest['pages']:
        for match in IMAGE.finditer(owned(output, page['path']).read_text()):
            relative = match['path'].strip().removeprefix('/')
            target = owned(output, relative)
            if relative.endswith('.placeholder.svg') or (target.exists() and MARKER in target.read_text(errors='ignore')[:1000]):
                references.append({'page': page['path'], 'image': relative})
    files = sorted(str(path.relative_to(output)) for path in output.rglob('*.placeholder.svg'))
    return {'purpose': 'authoring-only', 'placeholder_free': not references and not files, 'remaining_references': references,
            'remaining_files': files,
            'publication_ready': False}


def gallery(output, manifest):
    """Local, offline visual overview; this is an authoring aid, never evidence."""
    groups = {}
    for record in manifest['placeholders']:
        group = next(iter(record['pages']), 'Schermate pianificate dai flussi')
        groups.setdefault(group, []).append(record)
    sections = []
    for title, records in groups.items():
        cards = []
        for record in records:
            href = html.escape(record['placeholder_path'], quote=True)
            label = html.escape(record.get('caption') or record['original_path'])
            cards.append(f'<a class="card" href="{href}"><img src="{href}" alt="{label}" width="1920" height="1080" loading="lazy"/><span>{label}</span><small>SVG provvisorio · 1920 × 1080</small></a>')
        sections.append(f'<section><h2>{html.escape(title)}</h2><div class="grid">{"".join(cards)}</div></section>')
    (output / 'SCREENSHOT-PLACEHOLDERS.html').write_text('''<!doctype html><html lang="it"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Schermate da acquisire · Manuale Assozeta</title>
<style>body{margin:0;background:#f7f8fc;color:#253145;font:16px/1.6 system-ui,sans-serif}main{max-width:1440px;margin:auto;padding:48px 24px}h1{font-size:36px;line-height:1.15}h2{font-size:21px;margin-top:44px}.note{padding:16px 20px;border-left:4px solid #7160d6;background:#eeebff;border-radius:8px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,320px),1fr));gap:20px}.card{display:block;background:white;border:1px solid #e4e7ef;border-radius:12px;overflow:hidden;text-decoration:none;color:inherit;box-shadow:0 2px 8px #25314506}.card:focus-visible{outline:3px solid #7160d6;outline-offset:4px}.card img{display:block;width:100%;height:auto;aspect-ratio:16/9}.card span,.card small{display:block;padding:10px 16px;overflow-wrap:anywhere}.card small{padding-top:0;color:#637083}.count{color:#637083}</style>
<main><h1>Schermate da acquisire</h1><p class="note">Questi SVG preparano la stesura del manuale. Non sono schermate dell’applicazione: sostituirli con catture reali prima della verifica finale.</p>'''
        + f'<p class="count">{len(manifest["pages"])} pagine · {len(manifest["placeholders"])} segnaposto · originali finali 1920 × 1080, scala 1</p>'
        + ''.join(sections) + '</main></html>\n')


def review_content(output, selected, *, refresh_recipe_bindings=False, wording_only=False, review_note=''):
    """Record an editorial review; never claim a passed capture or procedure."""
    output = output.resolve()
    if wording_only and (refresh_recipe_bindings or not review_note.strip()):
        raise ValueError('A wording-only review needs --review-note and unchanged implementation recipe bindings')
    sidecar_path = owned(output, '.manuale-evidence.json')
    sidecar = json.loads(sidecar_path.read_text()) if sidecar_path.exists() else {
        'format': 1, 'purpose': 'reviewed-content', 'verified': False, 'sections': {}}
    if sidecar.get('format') != 1 or sidecar.get('purpose') != 'reviewed-content' or sidecar.get('verified') is not False:
        raise ValueError('Invalid reviewed content bindings')
    staged = {}
    discovered = None
    for key in selected:
        binding = sidecar['sections'].get(key)
        if not binding:
            if wording_only:
                raise ValueError('A wording-only review requires an existing reviewed section: ' + key)
            if discovered is None:
                command = 'const {editorialContracts} = await import(process.argv[1]); console.log(JSON.stringify(editorialContracts()));'
                descriptors = json.loads(subprocess.run(['node', '--input-type=module', '-e', command,
                    (ROOT / 'docs/manuale/editorial-contracts.mjs').as_uri()], capture_output=True, text=True, check=True).stdout)
                discovered = {item['path'] + '#' + item['id']: item for item in descriptors}
            descriptor = discovered.get(key)
            if not descriptor:
                raise ValueError('Unknown authored section: ' + key)
            binding = {'title': descriptor['title'], 'recipe_module': descriptor['recipe_module']}
        module = binding['recipe_module']
        if not module.startswith('docs/manuale/') or not module.endswith('.mjs'):
            raise ValueError('Invalid reviewed recipe module: ' + str(module))
        current_recipe = digest(owned(ROOT, module).read_bytes())
        if binding.get('recipe_sha256', current_recipe) != current_recipe and not refresh_recipe_bindings:
            raise ValueError('Recipe changed; update reviewed bindings before editing prose: ' + binding['recipe_module'])
        relative = key.rsplit('#', 1)[0]
        mdx = owned(output, relative).read_text()
        body = reviewed_section_body(mdx, key, binding['title'])
        staged[key] = {'title': binding['title'], 'recipe_module': module,
                       'content_sha256': digest(body.encode()), 'recipe_sha256': current_recipe}
        if wording_only:
            staged[key]['editorial_review'] = {'format': 1, 'kind': 'wording-only',
                'from_sha256': binding['content_sha256'], 'to_sha256': staged[key]['content_sha256'],
                'recipe_sha256': current_recipe, 'note': review_note.strip()}
    for key, value in staged.items():
        binding = sidecar['sections'].setdefault(key, {})
        if binding.get('recipe_sha256') and value['recipe_sha256'] != binding['recipe_sha256']:
            binding['recipe_review'] = {'previous_sha256': binding['recipe_sha256'],
                'sha256': value['recipe_sha256'], 'purpose': 'editorial-review-only'}
        binding.update(value, content_source='authored-mdx')
        if not wording_only:
            binding.pop('editorial_review', None)
        # New editorial review cannot retain a previous captured publication seal.
        for field in ('materialization', 'editorial_content_sha256'):
            binding.pop(field, None)
    sidecar_path.write_text(json.dumps(sidecar, ensure_ascii=False, indent=2) + '\n')
    return {'reviewed_sections': list(staged), 'verified': False, 'publication_ready': False}


def plan(output):
    """Prepare future SVGs too, without inserting unreviewed screenshot positions."""
    output = output.resolve()
    manifest = json.loads((output / '.manuale-authoring.json').read_text())
    backlog_path = output / 'SCREENSHOTS-NEEDED.md'
    backlog_text = (backlog_path.read_text() if backlog_path.exists() else '').replace(
        'A human should take these screenshots from the live application at https://app.bakney.com.',
        'Gli screenshot finali vengono acquisiti automaticamente da un’istanza Assozeta isolata con dati di esempio. Gli SVG sono soltanto segnaposto per la stesura.').replace(
        '- Use PNG format, minimum 1200px width for clarity',
        '- Stesura: SVG 1920 × 1080. Consegna: PNG reali con originale 1920 × 1080, scala 1; eventuali ritagli conservano originale e coordinate.').replace(
        'Capture at 1200-1400px viewport width for optimal display',
        'Acquisire con viewport 1920 × 1080 e scala dispositivo 1; conservare sempre l’originale Full HD')
    backlog_path.write_text(backlog_text)
    records = {item['original_path']: item for item in manifest['placeholders']}
    titles = {page['path']: page['title'] for page in manifest['pages']}
    prefix = ''
    backlog = []
    for line in backlog_text.splitlines():
        for header, folder in [('## Documentation Pages', 'docs'), ('## FAQ Pages', 'faq'), ('## Tutorial Pages', 'tutorials')]:
            if line.startswith(header):
                prefix = folder
        match = re.match(r'^\| `([^`]+\.mdx)` \| ([^|]+) \| ([^|]+) \| (.*) \|$', line)
        if not match:
            continue
        page = prefix + '/' + match[1]
        count = int(match[3].strip()) if match[3].strip().isdigit() else 0
        backlog.append({'page': page, 'original_status': match[2].strip(), 'requested': count,
                        'description': match[4].strip(), 'source_review': 'pending'})
        captions = [part.strip() for part in re.split(r'(?:^|\s)\d+\.\s+', match[4].strip()) if part.strip()]
        for number in range(1, count + 1):
            original = 'images/' + str(Path(page).with_suffix('')) + '/' + str(number) + '.png'
            record = records.setdefault(original, {'original_path': original, 'placeholder_path': str(Path(original).with_suffix('.placeholder.svg')),
                'pages': [page], 'planned_from': 'original-backlog', 'status': 'placeholder', 'verified': False,
                'caption': captions[number - 1] if number <= len(captions) else titles.get(page, page) + ' · ' + str(number),
                'placement': 'requires review of actual workflow', 'width': 1920, 'height': 1080})
            record.setdefault('caption', captions[number - 1] if number <= len(captions) else titles.get(page, page) + ' · ' + str(number))
    # Image slots come from the same workflow inventory as capture and build.
    registry = json.loads((ROOT / 'docs/manuale/recipes.json').read_text())
    for workflow in registry['recipes']:
        for number, checkpoint in enumerate(workflow['checkpoints'], 1):
            original = workflow['capture_prefix'] + str(number) + '.png'
            owned(output, original)
            record = records.setdefault(original, {'original_path': original,
                'placeholder_path': str(Path(original).with_suffix('.placeholder.svg')), 'status': 'placeholder',
                'verified': False, 'width': 1920, 'height': 1080})
            record.update({'pages': workflow['pages'], 'caption': checkpoint['caption'], 'workflow': workflow['id'],
                'checkpoint': checkpoint['id'], 'planned_from': 'docs/manuale/recipes.json',
                'placement': 'assigned by the canonical workflow definition'})
    for record in records.values():
        target = owned(output, record['placeholder_path'])
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and MARKER not in target.read_text():
            raise ValueError('Refusing to replace a non-placeholder SVG: ' + record['placeholder_path'])
        title = titles.get(next(iter(record['pages']), ''), 'Manuale d’uso')
        target.write_text(svg_placeholder(title, record['original_path'], record.get('caption', '')))
        record['sha256'] = digest(target.read_bytes())
    manifest['placeholders'] = list(records.values())
    manifest['screenshot_backlog'] = backlog
    manifest['summary']['svg_placeholders'] = len(records)
    manifest['summary']['backlog_entries'] = len(backlog)
    manifest['summary']['backlog_screenshots_requested'] = sum(item['requested'] for item in backlog)
    manifest['summary']['referenced_placeholder_paths'] = len({match['path'] for page in manifest['pages']
        for match in IMAGE.finditer(owned(output, page['path']).read_text())})
    gallery(output, manifest)
    (output / '.manuale-authoring.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    return manifest['summary']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('create', 'initialize', 'plan', 'review-content', 'status', 'check-placeholders'))
    parser.add_argument('--reference', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--branch', default='add/manuale')
    parser.add_argument('--reference-revision', help='Exact selected manual snapshot commit for initialize')
    parser.add_argument('--section', action='append', help='Reviewed MDX section path#id; repeat for each intentional edit')
    parser.add_argument('--refresh-recipe-bindings', action='store_true',
                        help='After source inspection, rebind only explicitly selected sections to changed recipes; never verifies a workflow')
    parser.add_argument('--wording-only', action='store_true',
                        help='Explicitly review prose corrections without changed behavior; compatible captures may be reused')
    parser.add_argument('--review-note', default='', help='Explain the wording-only correction and why behavior is unchanged')
    args = parser.parse_args()
    if args.command == 'create':
        if args.reference is None:
            parser.error('--reference is required for create')
        create(args.reference, args.output, args.branch)
        result = plan(args.output)
    elif args.command == 'initialize':
        result = initialize(args.output, reference_repository=args.reference, reference_revision=args.reference_revision)
    elif args.command == 'plan':
        result = plan(args.output)
    elif args.command == 'review-content':
        if not args.section:
            parser.error('--section is required for review-content')
        result = review_content(args.output, args.section, refresh_recipe_bindings=args.refresh_recipe_bindings,
                                wording_only=args.wording_only, review_note=args.review_note)
    else:
        result = remaining(args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command == 'check-placeholders' and not result['placeholder_free']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
