"""Offline hybrid retrieval and atomic, evidence-checked corpus construction.

LSA learns a latent semantic space from the verified corpus's TF-IDF matrix.
It needs no model credentials or network. Its limited vocabulary is deliberate:
unknown questions cannot be made authoritative by a high similarity score.
"""
import hashlib
import html
import importlib.util
import json
import math
import os
import re
import tempfile
import threading
import unicodedata
from collections import Counter, OrderedDict
from pathlib import Path
from urllib.parse import quote, urlsplit

FORMAT = 1
EMBEDDING = 'italian-tfidf-lsa-v5'
_RUNTIME_INDEX_CACHE = OrderedDict()
_RUNTIME_INDEX_LOCK = threading.RLock()
STOPWORDS = set('a ad al alla alle ai agli anche che chi con da dal dalla dei del della delle di e ed è gli i il in la le lo l ma nel nella nelle o per più quale quali se si su sul sulla un una uno come cosa posso devo fare manuale istruzioni guida tutorial'.split())
OPERATION_PATTERNS = {
    'approve': r'\b(incassare|incasso|incassa|approvare|approvo|approva)\b',
    'download': r'\b(scaricare|scarico|scarica|scarichi)\b',
    'generate': r'\b(generare|genero|genera|generazione)\b',
    'create': r'\b(creare|creo|crea|aggiungere|aggiungo|aggiungi|aggiunge)\b',
    'assign': r'\b(assegnare|assegno|assegna|assegni|assegnano)\b',
    'delete': r'\b(eliminare|elimino|elimina|eliminano|rimuovere|rimuovo|rimuovi|cancellare|cancella)\b',
    'export': r'\b(esportare|esporto|esporta|esporti)\b',
    'import': r'\b(importare|importo|importa|importi)\b',
    'configure': r'\b(configurare|configuro|configura|impostare|imposto|imposta)\b',
    'invite': r'\b(invitare|invito|invita|inviti)\b',
    'print': r'\b(stampare|stampo|stampa|stampi)\b',
    'search': r'\b(cercare|cerco|cerca|cercano|ricerca|trovare|trovo|trova)\b',
    'update': r'\b(modificare|modifico|modifica|modifiche|cambiare|cambio|cambia)\b',
    'archive': r'\b(archiviare|archivio|archivia|archiviazione)\b',
    'restore': r'\b(ripristinare|ripristino|ripristina|ripristinata|ripristinato)\b',
}


def operations(text):
    return {name for name, pattern in OPERATION_PATTERNS.items() if re.search(pattern, text.lower())}


class EvidenceError(ValueError):
    pass


def digest(value):
    if isinstance(value, str):
        value = value.encode('utf-8')
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def file_digest(path):
    return digest(Path(path).read_bytes())


def png_dimensions(contents):
    if len(contents) < 24 or contents[:8] != b'\x89PNG\r\n\x1a\n' or contents[12:16] != b'IHDR':
        raise EvidenceError('Capture is not a PNG')
    return {'width': int.from_bytes(contents[16:20], 'big'), 'height': int.from_bytes(contents[20:24], 'big')}


def verify_capture_frame(artifact_root, capture):
    """Verify retained masters and crops without claiming a browser result."""
    master = capture.get('master', {})
    if master.get('path') != 'masters/' + capture['path']:
        raise EvidenceError('Missing or unowned Full HD master')
    for relative, record in [('captures/' + capture['path'], capture), (master['path'], master)]:
        contents = owned_path(artifact_root, relative).read_bytes()
        size = png_dimensions(contents)
        if digest(contents) != record.get('sha256') or any(size[key] != record.get(key) for key in size):
            raise EvidenceError('Capture bytes or dimensions differ from the report')
    if (master['width'], master['height']) != (1920, 1080):
        raise EvidenceError('Capture master is not Full HD')
    stability = capture.get('stability')
    if stability is not None:
        if (stability.get('scope') != 'same-state-consecutive-captures'
                or stability.get('path') != 'masters/repeats/' + capture['path']
                or stability.get('sha256') != master.get('sha256')
                or stability.get('changed_pixel_fraction') != 0
                or file_digest(owned_path(artifact_root, stability['path'])) != master['sha256']):
            raise EvidenceError('Repeated checkpoint differs from its stable Full HD master')
    clip = capture.get('clip')
    if clip:
        if (any(type(clip.get(key)) is not int for key in ('x', 'y', 'width', 'height'))
                or clip['x'] < 0 or clip['y'] < 0 or clip['width'] <= 0 or clip['height'] <= 0
                or clip['x'] + clip['width'] > 1920 or clip['y'] + clip['height'] > 1080
                or (clip['width'], clip['height']) != (capture['width'], capture['height'])):
            raise EvidenceError('Crop is outside the Full HD frame or has different dimensions')
    elif capture['sha256'] != master['sha256']:
        raise EvidenceError('Uncropped capture differs from its Full HD master')


def owned_path(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path == root:
        raise EvidenceError('Source path escapes its repository')
    return path


def matches_capture(capture, recorded):
    """A reviewed caption may differ; the immutable capture record may not."""
    if 'caption' in capture and not isinstance(capture['caption'], str):
        return False
    return {key: value for key, value in capture.items() if key != 'caption'} == {
        key: value for key, value in recorded.items() if key != 'caption'}


def retained_evidence(manual_root, code_root, artifact_root, metadata):
    """Validate the explicit target binding with locally retained original proof."""
    binding = metadata.get('evidence_reuse')
    if not binding:
        return None
    if artifact_root is None or binding.get('path') != 'evidence-reuse.json':
        raise EvidenceError('Retained evidence requires its owned compatibility plan')
    plan_path = owned_path(artifact_root, binding['path'])
    if file_digest(plan_path) != binding.get('sha256'):
        raise EvidenceError('Retained evidence plan changed')
    state = json.loads(owned_path(artifact_root, 'run.json').read_text())
    if (state.get('application_input', {}).get('revision') != metadata['application_revision']
            or state.get('manual_input', {}).get('revision') != metadata['manual_revision']
            or state.get('release') != metadata['release']
            or state.get('reference_date') != metadata.get('reference_date')
            or state.get('tooling_hashes') != metadata.get('tooling_hashes')
            or state.get('evidence_reuse') != binding):
        raise EvidenceError('Retained evidence target differs from the current corpus')
    # Docker relocates these mounts; compatibility never reads a baseline host path.
    state.update(application=str(code_root), manual=str(manual_root), run=str(artifact_root))
    spec = importlib.util.spec_from_file_location('manual_index_retained_evidence', Path(__file__).with_name('reuse.py'))
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    try:
        plan = helper.validate_reuse_plan(state)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise EvidenceError('Retained evidence rejected: ' + str(exc)) from exc
    if binding.get('reused_recipes') != plan['reused_recipes']:
        raise EvidenceError('Retained evidence selection differs from the corpus binding')
    return plan


def slug(text):
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', '-', text).strip('-') or 'introduzione'


def sections(mdx):
    """Return original section text; frontmatter and JSX remain outside search text."""
    mdx = re.sub(r'\A---\s*\n.*?\n---\s*\n', '', mdx, count=1, flags=re.S)
    found = list(re.finditer(r'^#{1,6}\s+(.+?)\s*$', mdx, re.M))
    result = []
    if not found or mdx[:found[0].start()].strip():
        result.append(('introduzione', 'Introduzione', mdx[:found[0].start()] if found else mdx))
    for i, match in enumerate(found):
        end = found[i + 1].start() if i + 1 < len(found) else len(mdx)
        result.append((slug(match[1]), match[1], mdx[match.start():end]))
    ids = [item[0] for item in result]
    if len(ids) != len(set(ids)):
        raise EvidenceError('Duplicate section headings need explicit unique names')
    return {key: {'title': title, 'mdx': body.strip()} for key, title, body in result}


def plain_text(mdx):
    mdx = re.sub(r'```.*?```', '', mdx, flags=re.S)
    mdx = re.sub(r'!\[([^]]*)\]\([^)]*\)', r'\1', mdx)
    mdx = re.sub(r'\[([^]]*)\]\([^)]*\)', r'\1', mdx)
    mdx = re.sub(r'<(?:Step|Card)\b[^>]*title="([^"]*)"[^>]*>', r'\1. ', mdx)
    mdx = re.sub(r'<[^>]*>', ' ', mdx)
    return re.sub(r'\s+', ' ', mdx).strip()


def reader_content(mdx, images):
    """Project supported MDX components without evaluating JSX or moving images."""
    allowed = {image['path'] for image in images}
    component = re.compile(
        r'<(?P<name>CardGroup|Card|Warning|Note|Tip|Info)\b(?P<attrs>[^>]*)>'
        r'(?P<body>.*?)</(?P=name)>|!\[(?P<alt>[^]]*)\]\((?P<image>[^)]*)\)', re.S)
    result = []

    def prose(value):
        value = re.sub(r'^#{1,6}\s+.*$', '', value, flags=re.M)
        value = re.sub(r'<br\s*/?>', '\n\n', value, flags=re.I)
        value = re.sub(r'<[^>]*>', '', value).strip()
        if value:
            result.append({'kind': 'markdown', 'markdown': value})

    cursor = 0
    for match in component.finditer(mdx):
        prose(mdx[cursor:match.start()])
        cursor = match.end()
        if match['image'] is not None:
            image_path = match['image'].removeprefix('/')
            if image_path in allowed:
                result.append({'kind': 'image', 'path': image_path, 'alt': match['alt']})
            continue
        attrs = dict(re.findall(r'([a-zA-Z][\w-]*)\s*=\s*["\']([^"\']*)["\']', match['attrs']))
        children = reader_content(match['body'], images)
        if match['name'] == 'CardGroup':
            columns = re.search(r'\bcols\s*=\s*(?:\{(\d+)\}|["\'](\d+)["\'])', match['attrs'])
            result.append({'kind': 'cards', 'columns': min(3, max(1, int(next(filter(None, columns.groups()))) if columns else 2)),
                           'cards': [item for item in children if item['kind'] == 'card']})
        elif match['name'] == 'Card':
            result.append({'kind': 'card', 'title': attrs.get('title', ''),
                           'href': attrs.get('href', ''), 'icon': attrs.get('icon', ''), 'content': children})
        else:
            result.append({'kind': 'callout', 'style': match['name'].lower(), 'content': children})
    prose(mdx[cursor:])
    return result


def reader_blocks(mdx, images):
    """Keep reviewed Markdown, step boundaries, and only verified images."""
    def html_image(match):
        attrs = {key: html.unescape(value) for key, _, value in
                 re.findall(r'([\w-]+)\s*=\s*(["\'])(.*?)\2', match[1], re.S)}
        source = attrs.get('src', '')
        if not source.startswith('/images/') or any(char in source for char in '\n\r)'):
            return ''
        alt = re.sub(r'[\[\]\r\n]', ' ', attrs.get('alt', ''))
        return f'![{alt}]({source})'
    mdx = re.sub(r'<img\b([^>]*)/?>', html_image, mdx)
    steps = list(re.finditer(r'<Step\b[^>]*title="([^"]*)"[^>]*>(.*?)</Step>', mdx, re.S))
    parts = []
    cursor = 0
    # Keep introductory text and notes in their original positions around steps.
    for match in steps:
        parts.extend([('', mdx[cursor:match.start()]), (match[1], match[2])])
        cursor = match.end()
    parts.append(('', mdx[cursor:]))
    result = []
    for title, body in parts:
        structured_body = body
        paths = re.findall(r'!\[[^]]*\]\(/([^)]*)\)', body)
        body = re.sub(r'!\[[^]]*\]\([^)]*\)', '', body)
        body = re.sub(r'^#{1,6}\s+.*$', '', body, flags=re.M)
        markdown = re.sub(r'<[^>]*>', '', body).strip()
        readable = re.sub(r'^\s*(?:[-+*]|\d+[.)]|>)\s+', '', body, flags=re.M)
        text = plain_text(readable).replace('**', '').replace('__', '').replace('`', '')
        allowed_paths = {image['path'] for image in images}
        captures = list(dict.fromkeys(path for path in paths if path in allowed_paths))
        if title or text or captures:
            content = reader_content(structured_body, images)
            block = {'title': title, 'text': text, 'markdown': markdown, 'screenshots': captures,
                     'content': content}
            # A standalone notice keeps the existing outer styling. Notices
            # inside a step or surrounding prose remain in their actual place.
            if not title and len(content) == 1 and content[0]['kind'] == 'callout':
                block['kind'] = content[0]['style']
                block['content'] = content[0]['content']
            result.append(block)
    return result


def tokens(text):
    words = [word for word in re.findall(r'[a-zà-ÿ0-9]+', text.lower()) if word not in STOPWORDS and len(word) > 1]
    # Normalize the explicitly supported action vocabulary. This does not
    # equate distinct business entities such as associates and athletes.
    return [('operazione_' + sorted(operations(word))[0]) if operations(word) else word for word in words]


def verify_source(root, source, *, catalog=None):
    path = source.get('path', '')
    allowed = Path(path).suffix in ('.py', '.js', '.svelte', '.json') or (
        path.startswith('BE/templates/') and Path(path).suffix == '.html')
    if not path.startswith(('BE/', 'UI/')) or '..' in Path(path).parts or not allowed:
        raise EvidenceError('Evidence must reference application source, not credentials/runtime files')
    actual = owned_path(root, path)
    if file_digest(actual) != source.get('sha256'):
        raise EvidenceError(f'Stale code evidence: {path}')
    lines = actual.read_text().splitlines()
    start, end = source.get('start', 0), source.get('end', 0)
    if not isinstance(start, int) or not isinstance(end, int) or not 1 <= start <= end <= len(lines):
        raise EvidenceError(f'Invalid source range: {path}')
    snippet = '\n'.join(lines[start - 1:end])
    if not source.get('symbol') or source['symbol'] not in snippet:
        raise EvidenceError(f'Source symbol absent from cited range: {path}')
    if catalog is not None:
        binding = catalog['files'].get(path, {})
        revision = catalog['application_revision']
        if (binding.get('sha256') != source['sha256'] or binding.get('base_revision') != revision
                or source.get('base_revision') != revision
                or binding.get('source_state') != source.get('source_state')
                or source.get('snippet_sha256') != digest(snippet)):
            raise EvidenceError('Source differs from validated Git provenance: ' + path)
        if source['source_state'] == 'committed':
            if binding.get('base_blob_sha256') != source['sha256'] or source.get('commit_revision') != revision:
                raise EvidenceError('Commit citation does not match the validated Git blob: ' + path)
            repository = binding.get('repository_url')
            expected_url = (repository + '/blob/' + revision + '/' + quote(path)
                            + '#L' + str(start) + '-L' + str(end)) if repository else None
            if source.get('commit_url') != expected_url:
                raise EvidenceError('Commit citation differs from its source range: ' + path)
        elif source['source_state'] == 'working_tree':
            if source.get('commit_revision') or source.get('commit_url'):
                raise EvidenceError('Working-tree evidence cannot cite a committed file: ' + path)
            if source.get('snapshot_reference') != 'application/' + path + '#L' + str(start) + '-L' + str(end):
                raise EvidenceError('Working-tree evidence requires its retained snapshot: ' + path)
        else:
            raise EvidenceError('Unknown source provenance state: ' + path)
    return {**source, 'snippet': snippet, 'snippet_sha256': digest(snippet)}


def load_source_catalog(manual_root, code_root, manifest, artifact_root):
    binding = manifest['metadata'].get('source_catalog')
    if binding is None:
        return None  # Small deterministic unit fixtures and existing format-1 packages.
    if artifact_root is None or binding.get('format') != 1:
        raise EvidenceError('Source catalog requires the retained host validation artifact')
    path = owned_path(artifact_root, binding['path'])
    if file_digest(path) != binding.get('sha256'):
        raise EvidenceError('Validated source catalog changed')
    catalog = json.loads(path.read_text())
    if (catalog.get('format') != 1 or catalog.get('status') != 'source_targets_validated'
            or catalog.get('application_revision') != manifest['metadata']['application_revision']
            or catalog.get('manual_revision') != manifest['metadata']['manual_revision']):
        raise EvidenceError('Source catalog belongs to different repository revisions')
    navigation = {page + '.mdx' for group in json.loads((Path(manual_root) / 'mint.json').read_text())['navigation']
                  for page in group['pages']}
    declared = [page['path'] for page in catalog['pages']]
    manifest_pages = [page['path'] for page in manifest['pages']]
    if (set(declared) != navigation or len(declared) != len(navigation)
            or set(manifest_pages) != navigation or len(manifest_pages) != len(navigation)):
        raise EvidenceError('Source catalog omits or duplicates a navigation page')
    if set(catalog['manual_files']) != navigation | {'mint.json', 'SCREENSHOTS-NEEDED.md'}:
        raise EvidenceError('Source catalog omits manual source bindings')
    for relative, expected in catalog['manual_files'].items():
        if file_digest(owned_path(manual_root, relative)) != expected:
            raise EvidenceError('Manual changed after catalog validation: ' + relative)
    for relative, record in catalog['files'].items():
        allowed = Path(relative).suffix in ('.py', '.js', '.svelte', '.json') or (
            relative.startswith('BE/templates/') and Path(relative).suffix == '.html')
        if not relative.startswith(('BE/', 'UI/')) or '..' in Path(relative).parts or not allowed:
            raise EvidenceError('Invalid catalog source path')
        if record['path'] != relative or file_digest(owned_path(code_root, relative)) != record['sha256']:
            raise EvidenceError('Source changed after catalog validation: ' + relative)
    for page in manifest['pages']:
        recorded = next(item for item in catalog['pages'] if item['path'] == page['path'])
        current = sections(owned_path(manual_root, page['path']).read_text())
        inventories = {item['id']: item for item in recorded['sections']}
        if set(inventories) != set(current) or len(inventories) != len(recorded['sections']):
            raise EvidenceError('Source catalog omits a manual section')
        reviewed = {item['id']: item for item in page.get('sections', [])}
        for key, item in inventories.items():
            if (item['content_sha256'] != digest(current[key]['mdx'])
                    or item['status'] != reviewed.get(key, {}).get('status', 'pending')):
                raise EvidenceError('Source catalog changed section content or review status')
    return catalog


def fit_semantics(chunks, dimensions=64):
    import numpy as np
    counts = [Counter(tokens(chunk['text'])) for chunk in chunks]
    vocabulary = sorted(set().union(*(set(count) for count in counts))) if counts else []
    if not vocabulary:
        return {'vocabulary': [], 'idf': [], 'basis': [], 'vectors': []}
    n = len(chunks)
    idf = np.array([math.log((n + 1) / (1 + sum(word in count for count in counts))) + 1 for word in vocabulary])
    matrix = np.array([[math.log1p(count[word]) for word in vocabulary] for count in counts]) * idf
    matrix /= np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-12)
    _, _, vt = np.linalg.svd(matrix, full_matrices=False)
    # Retain a reduced space when possible; full rank merely reproduces lexical cosine.
    rank = min(dimensions, max(1, len(vt) // 2))
    basis = vt[:rank]
    for row in basis:
        if row[np.argmax(np.abs(row))] < 0:
            row *= -1
    vectors = matrix @ basis.T
    vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
    return {'vocabulary': vocabulary, 'idf': idf.round(12).tolist(),
            'basis': basis.round(12).tolist(), 'vectors': vectors.round(12).tolist()}


def build_index(manual_root, code_root, manifest, *, artifact_root=None):
    """Require explicitly reviewed section hashes, current code, and capture evidence.

    The manifest is a curated input, not an automatic semantic proof. The builder
    verifies its provenance and never upgrades a pending section to verified.
    """
    metadata = manifest['metadata']
    for key in ('application_revision', 'manual_revision', 'release', 'manual_url'):
        if not metadata.get(key):
            raise EvidenceError(f'Missing corpus binding: {key}')
    url = urlsplit(metadata['manual_url'])
    if url.scheme not in ('http', 'https') or not url.netloc or url.username or url.password or url.query or url.fragment:
        raise EvidenceError('Invalid public manual URL')
    catalog = load_source_catalog(manual_root, code_root, manifest, artifact_root)
    chunks, gaps, dependencies = [], [], {}
    tooling = metadata.get('tooling_hashes', {})
    for path, expected in tooling.items():
        if not path.startswith(('BE/', 'UI/', 'docs/manuale/', 'docs/scripts/manuale', 'selfhost/')) or any(part.startswith('.env') for part in Path(path).parts):
            raise EvidenceError('Invalid scenario tooling path')
        if file_digest(owned_path(code_root, path)) != expected:
            raise EvidenceError(f'Scenario tooling changed: {path}')
        dependencies[path] = expected
    reuse_plan = retained_evidence(manual_root, code_root, artifact_root, metadata)
    scenarios = {scenario['id']: scenario for scenario in manifest.get('scenarios', [])}
    seen_pages = set()
    for page_order, page in enumerate(manifest['pages']):
        if page['path'] in seen_pages:
            raise EvidenceError('Duplicate manual page')
        seen_pages.add(page['path'])
        path = owned_path(manual_root, page['path'])
        if path.suffix != '.mdx':
            raise EvidenceError('Manual pages must be MDX')
        mdx = path.read_text()
        available = sections(mdx)
        heading = re.search(r'^title:\s*(.+?)\s*$', mdx, re.M)
        page_title = heading[1].strip().strip('\"\'') if heading else page['path']
        # Original frontmatter is discovery text too. Only expose a description
        # explicitly reviewed as part of the verified page content.
        page_description = page.get('reviewed_description', '')
        mapped = set()
        for section in page.get('sections', []):
            key = section['id']
            if key in mapped or key not in available:
                raise EvidenceError(f'Duplicate or missing section: {page["path"]}#{key}')
            mapped.add(key)
            if section.get('status') != 'verified':
                gaps.append({'page': page['path'], 'section': key, 'status': section.get('status', 'pending'), 'reason': section.get('reason', '')})
                continue
            body = available[key]['mdx']
            if digest(body) != section.get('content_sha256'):
                raise EvidenceError(f'Stale manual text: {page["path"]}#{key}')
            if not section.get('evidence'):
                raise EvidenceError('Verified sections need implementation evidence')
            sources = [verify_source(code_root, source, catalog=catalog) for source in section['evidence']]
            for source in sources:
                dependencies[source['path']] = source['sha256']
            captures = []
            capture_provenance = []
            runtime_dependencies = {source['path']: source['sha256'] for source in sources}
            scenario_ids = section.get('scenario_ids', [])
            if section.get('kind') == 'workflow' and not scenario_ids:
                raise EvidenceError('Verified procedures need a passed browser scenario')
            for scenario_id in scenario_ids:
                scenario = scenarios.get(scenario_id, {})
                reused = reuse_plan['recipes'].get(scenario_id) if reuse_plan else None
                expected_revision = reused['origin']['application_revision'] if reused else metadata['application_revision']
                if scenario.get('status') != 'passed' or scenario.get('application_revision') != expected_revision:
                    raise EvidenceError(f'Missing compatible passed scenario: {scenario_id}')
                if reused:
                    expected_reuse = {'path': metadata['evidence_reuse']['path'],
                                      'sha256': metadata['evidence_reuse']['sha256'], 'recipe_id': scenario_id}
                    if (scenario.get('reuse') != expected_reuse
                            or scenario.get('report_path') != reused['report_path']
                            or scenario.get('report_sha256') != reused['report_sha256']
                            or scenario.get('capture_id') != reused['origin']['capture_id']):
                        raise EvidenceError('Scenario differs from retained capture provenance')
                    editorial = next((item for item in reused['sections'] if item['path'] == page['path'] and item['id'] == key), None)
                    if not editorial or editorial['published_content_sha256'] != section['content_sha256']:
                        raise EvidenceError('Section has no matching retained editorial evidence')
                    for relative in reused['production_paths']:
                        expected = reused['dependency_hashes'][relative]
                        dependencies[relative] = runtime_dependencies[relative] = expected
                elif scenario.get('reuse'):
                    raise EvidenceError('Scenario has no validated retained evidence binding')
                for source in sources:
                    if scenario.get('source_hashes', {}).get(source['path']) != source['sha256']:
                        raise EvidenceError(f'Scenario evidence is stale: {scenario_id}')
                if artifact_root is None or not scenario.get('report_path'):
                    raise EvidenceError('Browser evidence needs a retained report')
                report_path = owned_path(artifact_root, scenario['report_path'])
                if file_digest(report_path) != scenario.get('report_sha256'):
                    raise EvidenceError('Browser report changed')
                report = json.loads(report_path.read_text())
                if report.get('status') != 'passed' or report.get('backend') != 'real' or report.get('id') != scenario_id or report.get('application_revision') != expected_revision:
                    raise EvidenceError('Browser report does not prove this scenario passed')
                if report.get('source_hashes') != scenario.get('source_hashes'):
                    raise EvidenceError('Browser report source binding differs')
                for relative, expected in report.get('source_hashes', {}).items():
                    if relative.startswith(('BE/', 'UI/')):
                        if file_digest(owned_path(code_root, relative)) != expected:
                            raise EvidenceError('Scenario implementation changed: ' + relative)
                        runtime_dependencies[relative] = expected
                if not reused and report.get('tooling_hashes', {}) != tooling:
                    raise EvidenceError('Browser report tooling binding differs')
                if report.get('capture_format') == 'full-hd-v1':
                    if report.get('viewport') != {'width': 1920, 'height': 1080} or report.get('device_scale_factor') != 1:
                        raise EvidenceError('Browser report has incompatible Full HD capture settings')
                    for frame in report.get('screenshots', []):
                        verify_capture_frame(artifact_root, frame)
                captures.extend(report.get('screenshots', []))
                capture_provenance.append({'scenario_id': scenario_id, 'application_revision': report['application_revision'],
                                           'capture_id': report.get('capture_id'), 'report_sha256': scenario['report_sha256'],
                                           'reused': bool(reused)})
            images = []
            for capture in section.get('screenshots', []):
                image = owned_path(manual_root, capture['path'])
                if image.suffix.lower() != '.png' or 'placeholder' in image.name.lower():
                    raise EvidenceError('Draft placeholders cannot enter verified documentation')
                if file_digest(image) != capture.get('sha256') or not any(matches_capture(capture, recorded) for recorded in captures):
                    raise EvidenceError('Screenshot has no matching passed capture')
                images.append({**capture, 'url': metadata['manual_url'].rstrip('/') + '/' + quote(capture['path'])})
            text = plain_text(body)
            page_id = str(Path(page['path']).with_suffix(''))
            chunks.append({'id': page_id + '#' + key, 'page': page_id, 'section': key,
                           'title': available[key]['title'], 'text': text, 'content_sha256': digest(body),
                           'page_title': page_title, 'page_description': page_description, 'page_order': page_order,
                           'section_order': list(available).index(key), 'language': 'it', 'intent': section.get('intent', ''),
                           'reader': reader_blocks(body, images),
                           'audience': section.get('audience', 'public'), 'features': section.get('features', []),
                           'status': 'verified', 'citation_anchor': section.get('citation_anchor', key),
                           'url': metadata['manual_url'].rstrip('/') + '/' + quote(page_id) + '#' + quote(section.get('citation_anchor', key), safe=''),
                           'evidence': sources, 'screenshots': images, 'scenario_ids': scenario_ids,
                           'capture_provenance': capture_provenance,
                           'runtime_dependencies': runtime_dependencies})
        for key in sorted(set(available) - mapped):
            gaps.append({'page': page['path'], 'section': key, 'status': 'pending', 'reason': 'No reviewed implementation evidence'})
        if page_description and not any(page_description in chunk['text'] for chunk in chunks if chunk['page'] == str(Path(page['path']).with_suffix(''))):
            raise EvidenceError('Chapter description has no matching verified content')
    chunks.sort(key=lambda chunk: chunk['id'])
    if len({chunk['id'] for chunk in chunks}) != len(chunks):
        raise EvidenceError('Duplicate corpus chunk')
    result = {'format': FORMAT, 'embedding': EMBEDDING, 'metadata': metadata, 'chunks': chunks,
              'gaps': gaps, 'dependencies': dependencies, 'semantics': fit_semantics(chunks)}
    if catalog is not None:
        result['catalog'] = catalog
    # Capture locations/nonces can differ between runs; maintain a stable
    # identity for the reviewed implementation and section contents as well.
    return seal_index(result)


def seal_index(value):
    value = {key: item for key, item in value.items() if key != 'identity'}
    metadata = value['metadata']
    value['content_identity'] = digest(canonical({'revision': metadata['application_revision'],
        'release': metadata['release'], 'dependencies': value['dependencies'],
        'sections': [{'id': chunk['id'], 'content_sha256': chunk['content_sha256'],
                      'audience': chunk['audience'], 'features': chunk['features']} for chunk in value['chunks']]}))
    value['identity'] = digest(canonical(value))
    return value


def promote(index, output):
    """Replace only after complete construction/validation; never mutate in place."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=output.parent, prefix='.manual-index-')
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(canonical(index) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class ManualIndex:
    def __init__(self, value):
        value = dict(value)
        identity = value.pop('identity', None)
        if value.get('format') != FORMAT or value.get('embedding') != EMBEDDING or digest(canonical(value)) != identity:
            raise EvidenceError('Invalid or unsupported manual index')
        # Each application path has one captured content hash. A sealed payload
        # can still declare contradictory hashes; never let dict.update choose
        # which declaration controls source invalidation or installation.
        bindings = dict(value['dependencies'])
        for chunk in value['chunks']:
            declarations = [(source['path'], source['sha256']) for source in chunk.get('evidence', [])]
            declarations.extend(chunk.get('runtime_dependencies', {}).items())
            for path, expected in declarations:
                if path in bindings and bindings[path] != expected:
                    raise EvidenceError(f'Conflicting implementation evidence hashes: {path}')
                bindings[path] = expected
        self.value = {**value, 'identity': identity}

    @classmethod
    def load(cls, path):
        return cls(json.loads(Path(path).read_text()))

    @classmethod
    def load_runtime(cls, path):
        """Reuse a verified, read-only runtime index until its file changes.

        Source compatibility and asset hashes remain request-time checks. Use
        load() for publication/editing callers that need a mutable fresh copy.
        """
        path = Path(path).resolve()
        def signature():
            value = path.stat()
            return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        with _RUNTIME_INDEX_LOCK:
            stamp = signature()
            key = (cls, str(path), stamp)
            if key in _RUNTIME_INDEX_CACHE:
                _RUNTIME_INDEX_CACHE.move_to_end(key)
                return _RUNTIME_INDEX_CACHE[key]
            index = cls.load(path)
            if signature() != stamp:
                raise EvidenceError('Manual index changed while loading')
            _RUNTIME_INDEX_CACHE[key] = index
            while len(_RUNTIME_INDEX_CACHE) > 2:
                _RUNTIME_INDEX_CACHE.popitem(last=False)
            return index

    def check_version(self, *, revision, release):
        meta = self.value['metadata']
        if not revision or revision != meta['application_revision'] or not release or release != meta['release']:
            raise EvidenceError('No verified manual for the running application revision/release')

    def verify_unchanged_dependencies(self, code_root):
        """Strict build/install gate; tooling is provenance, not a reading gate."""
        bindings = dict(self.value['dependencies'])
        for chunk in self.value['chunks']:
            bindings.update(chunk.get('runtime_dependencies', {}))
        for path, expected in bindings.items():
            if file_digest(owned_path(code_root, path)) != expected:
                raise EvidenceError(f'Implementation changed since manual verification: {path}')

    def stale_sections(self, code_root):
        """Invalidate each guide using its captured application sources."""
        if code_root is None:
            return {}
        # Common access/context behavior affects every chapter. Existing format-1
        # packages retain their original hashes; no evidence is re-signed here.
        shared = {path: expected for path, expected in self.value['dependencies'].items()
                  if path in {'BE/core/authentication.py', 'BE/core/middleware.py', 'BE/core/settings.py',
                              'BE/application/permissions.py', 'BE/application/permissions_registry.py',
                              'BE/application/impersonation.py', 'BE/application/impersonation_scope.py',
                              'BE/application/urls.py', 'UI/src/routes.js', 'UI/src/utils/Permissions.js',
                              'UI/src/utils/ApiMiddleware.js', 'UI/src/utils/checkAuth.js'}}
        checked = {}
        stale = {}
        for chunk in self.value['chunks']:
            bindings = {source['path']: source['sha256'] for source in chunk['evidence']}
            bindings.update(chunk.get('runtime_dependencies', {}))
            bindings.update(shared)
            changed = []
            for path, expected in bindings.items():
                if path not in checked:
                    try:
                        checked[path] = file_digest(owned_path(code_root, path))
                    except OSError:
                        checked[path] = None
                if checked[path] != expected:
                    changed.append(path)
            if changed:
                stale[chunk['id']] = sorted(changed)
        return stale

    def applicable(self, *, revision, release, features=(), maintainer=False, code_root=None):
        self.check_version(revision=revision, release=release)
        stale = self.stale_sections(code_root)
        return [chunk for chunk in self.value['chunks']
                if chunk['status'] == 'verified' and (chunk['audience'] == 'public' or maintainer)
                and chunk['id'] not in stale and set(chunk['features']).issubset(set(features))]

    def search(self, query, *, limit=5, **context):
        import numpy as np
        requested_operations = operations(query)
        # These protocol qualifiers distinguish procedures from neighbouring
        # local features. A disclaimer mentioning OAuth or electronic invoices
        # in body text does not verify that integration. Require a procedural
        # heading naming the requested protocol before returning instructions.
        protocol_patterns = (r'\belettronic[aohe]+\b', r'\boauth\b', r'\b(?:sdi|interscambio)\b')
        requested_protocols = [pattern for pattern in protocol_patterns if re.search(pattern, query, re.I)]
        candidates = [chunk for chunk in self.applicable(**context)
                      if requested_operations.issubset(operations(chunk['title'] + ' ' + chunk['text']))
                      and all(re.search(pattern, chunk['title'] + ' ' + ' '.join(
                          block.get('title', '') for block in chunk.get('reader', [])), re.I)
                          for pattern in requested_protocols)]
        q = Counter(tokens(query))
        if not q:
            return []
        all_counts = {chunk['id']: Counter(tokens(chunk['text'] + ' ' + chunk['title'])) for chunk in candidates}
        vocabulary = self.value['semantics']['vocabulary']
        if not vocabulary:
            return []
        model = self.value['semantics']
        vector = np.array([math.log1p(q[word]) for word in vocabulary]) * np.array(model['idf'])
        latent = vector @ np.array(model['basis']).T
        latent /= max(float(np.linalg.norm(latent)), 1e-12)
        vectors = dict(zip((chunk['id'] for chunk in self.value['chunks']), model['vectors']))
        n = len(candidates)
        avg_length = sum(sum(count.values()) for count in all_counts.values()) / max(n, 1)
        ranked = []
        for chunk in candidates:
            count = all_counts[chunk['id']]
            overlap = set(q) & set(count)
            # No shared content words is not sufficient evidence for a procedure.
            if not overlap:
                continue
            # A shared generic verb ("creare") does not establish that the
            # retrieved procedure concerns the requested object ("fattura").
            if not any(not word.startswith('operazione_') for word in overlap):
                continue
            bm25 = 0.0
            for word in overlap:
                df = sum(word in terms for terms in all_counts.values())
                weight = math.log(1 + (n - df + .5) / (df + .5))
                freq = count[word]
                bm25 += weight * freq * 2.2 / (freq + 1.2 * (.25 + .75 * sum(count.values()) / max(avg_length, 1)))
            lexical = bm25 / (1 + bm25)
            semantic = max(0.0, float(latent @ np.array(vectors[chunk['id']])))
            coverage = len(overlap) / len(q)
            # A short subsection can repeat an operation without explaining it.
            # Favor headings that name the requested action and object, while
            # retaining body evidence for queries about a particular step.
            title_overlap = set(q) & set(tokens(chunk['title']))
            title_coverage = len(title_overlap) / len(q)
            # Prefer an actual procedural step over a passing mention of the
            # operation in an overview or warning. This uses authored headings,
            # not hard-coded question-to-section routes.
            step_coverage = max((len(set(q) & set(tokens(block.get('title', '')))) / len(q)
                for block in chunk.get('reader', [])
                if requested_operations and requested_operations.issubset(operations(block.get('title', '')))), default=0)
            score = .75 * (.7 * lexical + .3 * semantic) * coverage + .25 * title_coverage + .2 * step_coverage
            if score >= .18:
                ranked.append({**chunk, 'score': round(score, 6), 'lexical': round(lexical, 6), 'semantic': round(semantic, 6)})
        return sorted(ranked, key=lambda item: (-item['score'], item['id']))[:max(1, min(int(limit), 10))]
