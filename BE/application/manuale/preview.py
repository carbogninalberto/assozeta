"""Explicit development-only reader preview; never used by assistant or MCP."""
import json
import re
from pathlib import Path
from urllib.parse import quote

from django.conf import settings

from .development import development_enabled
from .index import digest, owned_path, plain_text, promote, reader_blocks, sections, verify_capture_frame


def load_preview():
    if not development_enabled():
        return None
    root = Path(settings.MANUAL_DEVELOPMENT_ROOT) / 'preview'
    pointer = root / 'active.json'
    if not pointer.exists():
        return None
    identity = json.loads(pointer.read_text())['identity']
    if not re.fullmatch(r'[a-f0-9]{64}', identity):
        raise ValueError('Invalid preview identity')
    package = root / identity
    contents = (package / 'index.json').read_bytes()
    if digest(contents) != identity:
        raise ValueError('Preview index changed')
    return json.loads(contents), package


def preview_results(package, query):
    results = package['sections']
    if query:
        terms = query.casefold().split()
        results = [item for item in results if all(term in (item['page_title'] + ' ' + item['title'] + ' ' + item['text']).casefold() for term in terms)]
        results = sorted(results, key=lambda item: -sum(term in item['title'].casefold() for term in terms))[:10]
    return {'status': 'development_preview', 'results': results,
            'message': '' if results else 'Nessuna sezione trovata per questa ricerca.'}


def install_preview(manual_root, run_root):
    if not development_enabled():
        raise ValueError('Manual previews are only available in ordinary development')
    manual_root, run_root = Path(manual_root).resolve(), Path(run_root).resolve()
    images = {}
    for report_path in sorted(run_root.glob('*.json')):
        report = json.loads(report_path.read_text())
        if report.get('status') != 'passed':
            continue
        for capture in report.get('screenshots', []):
            verify_capture_frame(run_root, capture)
            images[capture['path']] = {**capture, 'url': '/api/manuale/assets/preview/' + capture['path']}
    pages = []
    def walk(items):
        for item in items:
            if isinstance(item, str):
                if item not in pages:
                    pages.append(item)
            elif isinstance(item, dict):
                walk(item.get('pages', []))
    walk(json.loads((manual_root / 'mint.json').read_text())['navigation'])
    chunks = []
    for page_order, page in enumerate(pages):
        mdx = owned_path(manual_root, page + '.mdx').read_text()
        def metadata(name):
            match = re.search(r'^' + name + r':\s*(.*?)\s*$', mdx.split('---', 2)[1], re.M)
            return match[1].strip('\"\'') if match else ''
        for section_order, (slug, section) in enumerate(sections(mdx).items()):
            body = section['mdx'].replace('.placeholder.svg', '.png')
            captures = [image for path, image in images.items() if '/' + path in body]
            identifier = page + '#' + slug
            url = '/#/manuale?section=' + quote(identifier, safe='')
            chunks.append({'id': identifier, 'page': page, 'section': slug, 'title': section['title'],
                           'page_title': metadata('title') or page, 'page_description': metadata('description'),
                           'page_order': page_order, 'section_order': section_order, 'status': 'draft',
                           'language': 'it', 'text': plain_text(body), 'reader': reader_blocks(body, captures),
                           'screenshots': captures, 'url': url, 'embedded_url': url})
    if not chunks:
        raise ValueError('No preview sections found')
    contents = json.dumps({'sections': chunks}, ensure_ascii=False).encode()
    identity = digest(contents)
    root = Path(settings.MANUAL_DEVELOPMENT_ROOT) / 'preview'
    package = root / identity
    package.mkdir(parents=True, exist_ok=True)
    for path, capture in images.items():
        data = owned_path(run_root / 'captures', path).read_bytes()
        if digest(data) != capture['sha256']:
            raise ValueError('Capture changed during installation')
        destination = owned_path(package, path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    (package / 'index.json').write_bytes(contents)
    promote({'identity': identity}, root / 'active.json')
    return {'status': 'development_preview', 'pages': len(pages), 'sections': len(chunks), 'images': len(images)}
