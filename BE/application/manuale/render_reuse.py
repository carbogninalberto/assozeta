"""Reuse immutable manual-render evidence without manufacturing browser proof.

Hashes establish byte compatibility between a previously evaluated render and
the current target. They do not establish that the baseline was produced by a
trusted CI job; that producer-authenticity check belongs to the caller. This
module only accepts a completed evaluated run and independently rechecks every
retained artifact it consumes.

The planning API runs after target generation/compilation and before browser
rendering. It stages exact original page records, original reports, origin
metadata, and the layout PNGs referenced by those records. Validation is
independent of the original worktrees and fails closed on later mutation.
"""
import copy
import os
import re
import tempfile
from pathlib import Path

from .index import canonical, digest, png_dimensions
from .reuse import HASH, ReuseError, file_hash, load, owned


FORMAT = 1
RENDER_INPUTS = 'render-inputs.json'
PLAN_PATH = 'render-reuse.json'
APPLICATION_INPUTS = (
    'selfhost/tests/browser/manuale/compile.mjs',
    'selfhost/tests/browser/manuale/verify.mjs',
    'selfhost/tests/browser/package-lock.json',
    'selfhost/tests/browser/playwright.manual.config.mjs',
)
OPTIONAL_APPLICATION_INPUTS = (
    'selfhost/tests/browser/package.json',
)
MANUAL_INPUTS = ('mint.json', 'docs.json')
FULL_HD = {'width': 1920, 'height': 1080}
ORIGIN_REPORT = re.compile(r'render-origins/[a-f0-9]{64}\.json\Z')
ORIGIN_RECORD = re.compile(r'render-origins/[a-f0-9]{64}\.[a-f0-9]{16}\.[a-f0-9]{16}\.origin\.json\Z')


class RenderReuseError(ReuseError):
    """A render-reuse plan or retained render artifact is not safe to consume."""


def _hash_input(root, root_name, relative):
    try:
        return file_hash(root, relative)
    except (OSError, ReuseError, ValueError) as exc:
        raise RenderReuseError(f'missing-renderer-input: {root_name}/{relative}') from exc


def renderer_inputs(state):
    """Return deterministic hashes for renderer code, packages and navigation.

    Application and manual roots are namespaced so the result can be persisted
    as one comparison file. The manifest inputs are required to establish which
    pages the renderer visits. Missing required files raise instead of producing
    a partial fingerprint.
    """
    application, manual = Path(state['application']).resolve(), Path(state['manual']).resolve()
    inputs = {}
    for relative in APPLICATION_INPUTS:
        inputs['application/' + relative] = _hash_input(application, 'application', relative)
    for relative in OPTIONAL_APPLICATION_INPUTS:
        target = owned(application, relative)
        if target.exists():
            inputs['application/' + relative] = _hash_input(application, 'application', relative)
    navigation = []
    for relative in MANUAL_INPUTS:
        try:
            target = owned(manual, relative)
        except (OSError, ReuseError, ValueError) as exc:
            raise RenderReuseError(f'missing-renderer-input: manual/{relative}') from exc
        if target.is_file():
            navigation.append(relative)
    if not navigation:
        raise RenderReuseError('missing-renderer-input: manual/mint.json or manual/docs.json')
    for relative in navigation:
        inputs['manual/' + relative] = _hash_input(manual, 'manual', relative)
    def referenced_assets(value):
        if isinstance(value, dict):
            for item in value.values():
                yield from referenced_assets(item)
        elif isinstance(value, list):
            for item in value:
                yield from referenced_assets(item)
        elif isinstance(value, str) and value.startswith('/') and not value.startswith('//'):
            relative = value.lstrip('/').split('#', 1)[0].split('?', 1)[0]
            if relative and Path(relative).suffix and not relative.endswith('.mdx'):
                asset = owned(manual, relative)
                if asset.is_file():
                    yield relative
    assets = {relative for config in navigation for relative in referenced_assets(load(manual, config))}
    assets.update(path.relative_to(manual).as_posix() for path in manual.rglob('*')
                  if path.is_file() and path.suffix in ('.css', '.js', '.mjs')
                  and not {'.git', 'node_modules'} & set(path.relative_to(manual).parts))
    for relative in sorted(assets):
        inputs['manual/' + relative] = _hash_input(manual, 'manual', relative)
    return dict(sorted(inputs.items()))


def _write_bytes(root, relative, contents):
    """Stage bytes without following a path or overwriting different evidence."""
    target = owned(root, relative)
    if target.exists():
        if target.is_file() and target.read_bytes() == contents:
            return
        raise RenderReuseError('staged-evidence-path-collision: ' + relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(contents)


def _replace_json(root, relative, value):
    target = owned(root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    contents = (canonical(value) + '\n').encode('utf-8')
    descriptor, temporary = tempfile.mkstemp(dir=target.parent, prefix='.' + target.name + '.')
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return digest(contents)


def _relative_page(page):
    relative = page.get('path') if isinstance(page, dict) else None
    if not isinstance(relative, str) or not relative.endswith('.mdx'):
        raise RenderReuseError('invalid-manifest-page-path')
    if Path(relative).is_absolute() or '\\' in relative or any(part in ('', '.', '..') for part in Path(relative).parts):
        raise RenderReuseError('invalid-manifest-page-path: ' + relative)
    return relative, relative[:-4]


def _renderable_pages(manifest, manual):
    pages = manifest.get('pages') if isinstance(manifest, dict) else None
    if not isinstance(pages, list):
        raise RenderReuseError('invalid-manifest-pages')
    result = {}
    for page in pages:
        relative, identifier = _relative_page(page)
        owned(manual, relative)
        if identifier in result:
            raise RenderReuseError('duplicate-manifest-page: ' + identifier)
        sections = page.get('sections', [])
        if not isinstance(sections, list):
            raise RenderReuseError('invalid-manifest-sections: ' + identifier)
        identifiers = []
        for section in sections:
            if not isinstance(section, dict) or not isinstance(section.get('id'), str) or not section['id']:
                raise RenderReuseError('invalid-section-identity: ' + identifier)
            if section.get('status') == 'verified':
                identifiers.append(section['id'])
        if len(identifiers) != len(set(identifiers)):
            raise RenderReuseError('duplicate-section-identity: ' + identifier)
        if identifiers:
            result[identifier] = page
    return result


def _compile_pages(run, root_name='target'):
    report = load(run, 'compile.json')
    if report.get('status') != 'passed' or not isinstance(report.get('pages'), list):
        raise RenderReuseError(f'{root_name}-compile-not-passed')
    pages = {}
    for page in report['pages']:
        if not isinstance(page, dict) or not isinstance(page.get('page'), str):
            raise RenderReuseError(f'invalid-{root_name}-compile-page')
        identifier = page['page']
        if (not identifier or Path(identifier).is_absolute() or '\\' in identifier
                or any(part in ('', '.', '..') for part in Path(identifier).parts)
                or not HASH.fullmatch(page.get('source_sha256', ''))):
            raise RenderReuseError(f'invalid-{root_name}-compile-page')
        if identifier in pages:
            raise RenderReuseError(f'duplicate-{root_name}-compile-page: ' + identifier)
        pages[identifier] = page
    return pages


def _target_contract(state, manifest, root_name='target'):
    application_input = state.get('application_input', {})
    manual_input = state.get('manual_input', {})
    metadata = manifest.get('metadata', {}) if isinstance(manifest, dict) else {}
    application_revision = application_input.get('revision')
    manual_revision = manual_input.get('revision')
    release = state.get('release')
    if (not isinstance(application_revision, str) or not application_revision
            or not isinstance(manual_revision, str) or not manual_revision
            or not isinstance(release, str) or not release):
        raise RenderReuseError(f'invalid-{root_name}-target-state')
    if (metadata.get('application_revision') != application_revision
            or metadata.get('manual_revision') != manual_revision
            or metadata.get('release') != release):
        raise RenderReuseError(f'stale-{root_name}-manifest-target')
    return {
        'run_id': state.get('run_id'),
        'application_revision': application_revision,
        'manual_revision': manual_revision,
        'release': release,
        'reference_date': state.get('reference_date'),
    }


def _status_passed(root, relative, root_name):
    value = load(root, relative)
    if value.get('status') != 'passed':
        raise RenderReuseError(f'{root_name}-{Path(relative).stem}-not-passed')
    return value


def _baseline_context(baseline_run, target_run):
    baseline = Path(baseline_run).resolve()
    if baseline == Path(target_run).resolve():
        raise RenderReuseError('baseline-must-be-a-different-owned-run')
    if not baseline.is_dir():
        raise RenderReuseError('missing-evaluated-baseline')
    state = load(baseline, 'run.json')
    state.update(run=str(baseline))
    if state.get('status') != 'evaluated':
        raise RenderReuseError('baseline-run-not-evaluated')
    if (baseline / 'release-verification.json').exists():
        _status_passed(baseline, 'release-verification.json', 'baseline')
    manifest = load(baseline, 'manifest.json')
    target = _target_contract(state, manifest, 'baseline')
    if not state.get('application') or not state.get('manual'):
        raise RenderReuseError('baseline-worktree-paths-missing')
    state['application'] = str(Path(state['application']).resolve())
    state['manual'] = str(Path(state['manual']).resolve())
    compile_report = _status_passed(baseline, 'compile.json', 'baseline')
    render = _status_passed(baseline, 'render.json', 'baseline')
    evaluation = _status_passed(baseline, 'evaluation.json', 'baseline')
    embedded = _status_passed(baseline, 'embedded-manual.json', 'baseline')
    for name, report in (('evaluation', evaluation), ('embedded-manual', embedded)):
        if (report.get('application_revision') != target['application_revision']
                or report.get('version') != target['release']):
            raise RenderReuseError(f'baseline-{name}-target-mismatch')
    pages = _renderable_pages(manifest, state['manual'])
    compiled = _compile_pages(baseline, 'baseline')
    rendered = _report_pages(render)
    if set(rendered) != set(pages) or not set(pages).issubset(compiled):
        raise RenderReuseError('baseline-render-page-coverage-mismatch')
    stored_inputs = load(baseline, RENDER_INPUTS)
    inputs = renderer_inputs(state)
    if stored_inputs != inputs:
        raise RenderReuseError('baseline-renderer-inputs-changed')
    return {
        'run': baseline,
        'state': state,
        'manifest': manifest,
        'target': target,
        'inputs': inputs,
        'report': {'path': 'render.json',
                   'sha256': file_hash(baseline, 'render.json')},
        'pages': pages,
        'compiled': compiled,
        'render': render,
        'rendered': rendered,
        'compile_report': compile_report,
    }


def _report_pages(report):
    if not isinstance(report, dict) or not isinstance(report.get('pages'), list):
        raise RenderReuseError('invalid-render-pages')
    pages = {}
    for page in report['pages']:
        if not isinstance(page, dict) or not isinstance(page.get('page'), str):
            raise RenderReuseError('invalid-render-page')
        identifier = page['page']
        if (not identifier or Path(identifier).is_absolute() or '\\' in identifier
                or any(part in ('', '.', '..') for part in Path(identifier).parts)):
            raise RenderReuseError('invalid-render-page')
        if identifier in pages:
            raise RenderReuseError('duplicate-render-page: ' + identifier)
        pages[identifier] = page
    return pages


def _displayed(section):
    screenshots = section.get('screenshots', [])
    if not isinstance(screenshots, list):
        raise RenderReuseError('invalid-section-screenshots')
    displayed = section.get('displayed_screenshots')
    if displayed is not None:
        if (not isinstance(displayed, list) or len(displayed) != len(set(displayed))
                or any(not isinstance(path, str) for path in displayed)):
            raise RenderReuseError('invalid-displayed-screenshots')
        available = {image.get('path') for image in screenshots if isinstance(image, dict)}
        if not set(displayed).issubset(available):
            raise RenderReuseError('displayed-screenshot-not-bound')
        selected = set(displayed)
    else:
        selected = None
    return [image for image in screenshots if selected is None or image.get('path') in selected]


def _page_facts(manifest_page, state, compile_page):
    relative, identifier = _relative_page(manifest_page)
    page_sha256 = file_hash(state['manual'], relative)
    if compile_page.get('source_sha256') != page_sha256:
        raise RenderReuseError('compiled-page-source-changed: ' + identifier)
    sections = [section for section in manifest_page.get('sections', []) if section.get('status') == 'verified']
    section_ids = [section['id'] for section in sections]
    screenshot_hashes, section_records = {}, []
    for section in sections:
        recorded = []
        for image in section.get('screenshots', []):
            if (not isinstance(image, dict) or not isinstance(image.get('path'), str)
                    or not HASH.fullmatch(image.get('sha256', ''))):
                raise RenderReuseError('invalid-screenshot-record: ' + identifier)
            actual = file_hash(state['manual'], image['path'])
            previous = screenshot_hashes.get(image['path'])
            if actual != image['sha256'] or (previous is not None and previous != actual):
                raise RenderReuseError('target-screenshot-changed: ' + image['path'])
            screenshot_hashes[image['path']] = actual
            recorded.append({'path': image['path'], 'sha256': actual,
                             'checkpoint': image.get('checkpoint'),
                             'caption': image.get('caption'),
                             'master': copy.deepcopy(image.get('master'))})
        displayed = _displayed(section)
        section_records.append({
            'id': section['id'],
            'screenshots': recorded,
            'displayed_screenshots': [image['path'] for image in displayed],
        })
    citation_anchors = {}
    for section in sections:
        anchor = section.get('citation_anchor')
        if anchor is not None:
            if not isinstance(anchor, str) or not anchor:
                raise RenderReuseError('invalid-citation-anchor: ' + identifier)
            citation_anchors[section['id']] = anchor
    return {
        'page': identifier,
        'path': relative,
        'page_sha256': page_sha256,
        'section_ids': section_ids,
        'section_records': section_records,
        'screenshot_hashes': dict(sorted(screenshot_hashes.items())),
        'citation_anchors': citation_anchors,
    }


def _layout_bytes(run, record):
    relative = record.get('path') if isinstance(record, dict) else None
    if (not isinstance(relative, str) or not relative.startswith('renders/')
            or not relative.endswith('.png') or '\\' in relative):
        raise RenderReuseError('invalid-layout-path')
    contents = owned(run, relative).read_bytes()
    dimensions = png_dimensions(contents)
    if (digest(contents) != record.get('sha256')
            or dimensions['width'] != record.get('width')
            or dimensions['height'] != record.get('height')
            or dimensions != FULL_HD):
        raise RenderReuseError('layout-png-mutated: ' + relative)
    return relative, digest(contents)


def _inspect_page_record(page_record, facts, run):
    """Validate one exact renderer page record and all of its layout evidence."""
    if page_record.get('page') != facts['page']:
        raise RenderReuseError('render-page-identity-changed')
    if (page_record.get('source_sha256') != facts['page_sha256']
            or page_record.get('anchors') != facts['section_ids']):
        raise RenderReuseError('render-page-sections-changed: ' + facts['page'])
    expected_images = [path for section in facts['section_records']
                       for path in section['displayed_screenshots']]
    if page_record.get('images') != expected_images:
        raise RenderReuseError('render-page-images-changed: ' + facts['page'])
    layouts = page_record.get('layouts')
    if not isinstance(layouts, list):
        raise RenderReuseError('missing-layout-evidence: ' + facts['page'])
    by_section = {section['id']: section for section in facts['section_records']}
    heading_seen, image_seen, paths = set(), set(), set()
    hashes = {}
    for layout in layouts:
        if not isinstance(layout, dict) or not layout.get('section') in by_section:
            raise RenderReuseError('layout-section-changed: ' + facts['page'])
        relative, sha256 = _layout_bytes(run, layout)
        if relative in paths:
            raise RenderReuseError('duplicate-layout-path: ' + relative)
        paths.add(relative)
        hashes[relative] = sha256
        section_id = layout['section']
        if layout.get('kind') == 'heading':
            if section_id in heading_seen:
                raise RenderReuseError('duplicate-heading-layout: ' + section_id)
            heading_seen.add(section_id)
        elif layout.get('kind') == 'image':
            key = (section_id, layout.get('image'), layout.get('checkpoint'))
            if key in image_seen:
                raise RenderReuseError('duplicate-image-layout: ' + section_id)
            image_seen.add(key)
            section = by_section[section_id]
            expected = next((image for image in section['screenshots']
                             if image['path'] == layout.get('image')), None)
            if expected is None:
                raise RenderReuseError('layout-image-not-referenced: ' + facts['page'])
            if (layout.get('checkpoint') != expected.get('checkpoint')
                    or layout.get('caption') != expected.get('caption')
                    or layout.get('master') != (expected.get('master') or {}).get('path')):
                raise RenderReuseError('layout-image-binding-changed: ' + facts['page'])
        else:
            raise RenderReuseError('invalid-layout-kind: ' + facts['page'])
    expected_images = {(section['id'], image['path'], image.get('checkpoint'))
                       for section in facts['section_records'] for image in section['screenshots']
                       if image['path'] in section['displayed_screenshots']}
    if heading_seen != set(by_section) or image_seen != expected_images:
        raise RenderReuseError('incomplete-layout-evidence: ' + facts['page'])
    return hashes


def _origin(state):
    return {
        'run_id': state.get('run_id'),
        'application_revision': state['application_input']['revision'],
        'manual_revision': state['manual_input']['revision'],
        'release': state['release'],
    }


def _origin_record_path(report_sha256, origin, page):
    return ('render-origins/' + report_sha256 + '.' + digest(canonical(origin))[:16]
            + '.' + digest(page)[:16] + '.origin.json')


def _record_for_page(report_path, report_sha256, origin, facts, layout_hashes,
                     citation_anchors, renderer_inputs_value):
    dependencies = {
        'page_path': facts['path'],
        'page_sha256': facts['page_sha256'],
        'section_ids': facts['section_ids'],
        'section_records': facts['section_records'],
        'screenshot_hashes': facts['screenshot_hashes'],
        'layout_sha256': dict(sorted(layout_hashes.items())),
        'renderer_inputs': copy.deepcopy(renderer_inputs_value),
    }
    record = {
        'format': FORMAT,
        'report': {'path': report_path, 'sha256': report_sha256},
        'origin': origin,
        'page': facts['page'],
        'dependencies': dependencies,
        'citation_anchors': citation_anchors,
    }
    return record


def _fresh_baseline_entry(context, page, report_sha256):
    manifest_page = context['pages'][page]
    facts = _page_facts(manifest_page, context['state'], context['compiled'][page])
    if set(facts['citation_anchors']) != set(facts['section_ids']):
        raise RenderReuseError('missing-original-citation-anchors: ' + page)
    render_page = copy.deepcopy(context['rendered'][page])
    layout_hashes = _inspect_page_record(render_page, facts, context['run'])
    origin = _origin(context['state'])
    report_path = 'render-origins/' + report_sha256 + '.json'
    origin_record = _record_for_page(report_path, report_sha256, origin, facts,
                                     layout_hashes, facts['citation_anchors'],
                                     context['inputs'])
    origin_record_path = _origin_record_path(report_sha256, origin, page)
    entry = {
        'page': page,
        'render': render_page,
        'citation_anchors': copy.deepcopy(facts['citation_anchors']),
        'origin': origin,
        'original_report': {'path': report_path, 'sha256': report_sha256},
        'origin_record': {'path': origin_record_path,
                          'sha256': digest((canonical(origin_record) + '\n').encode('utf-8'))},
        'dependencies': origin_record['dependencies'],
    }
    return {
        'entry': entry,
        'report_source': 'render.json',
        'origin_source': None,
        'origin_bytes': (canonical(origin_record) + '\n').encode('utf-8'),
    }


def _prior_entry(context, page, prior):
    entry = copy.deepcopy(prior['pages'][page])
    required = ('render', 'citation_anchors', 'origin', 'original_report', 'origin_record', 'dependencies')
    if any(key not in entry for key in required):
        raise RenderReuseError('incomplete-preceding-render-plan')
    return {
        'entry': entry,
        'report_source': entry['original_report']['path'],
        'origin_source': entry['origin_record']['path'],
    }


def _target_facts(state, manifest_pages, compile_pages, page):
    if page not in manifest_pages:
        raise RenderReuseError('page-not-rendered: ' + page)
    if page not in compile_pages:
        raise RenderReuseError('page-not-compiled: ' + page)
    return _page_facts(manifest_pages[page], state, compile_pages[page])


def _compare_target(entry, facts, inputs):
    dependencies = entry['dependencies']
    if dependencies.get('page_path') != facts['path']:
        raise RenderReuseError('page-path-changed')
    if dependencies.get('page_sha256') != facts['page_sha256']:
        raise RenderReuseError('page-mdx-changed')
    if dependencies.get('section_ids') != facts['section_ids']:
        raise RenderReuseError('section-identities-changed')
    if dependencies.get('section_records') != facts['section_records']:
        raise RenderReuseError('screenshot-bindings-changed')
    if dependencies.get('screenshot_hashes') != facts['screenshot_hashes']:
        raise RenderReuseError('screenshot-hashes-changed')
    if dependencies.get('renderer_inputs') != inputs:
        raise RenderReuseError('renderer-inputs-changed')


def _stage_candidate(target_run, context, candidate):
    entry = candidate['entry']
    report = owned(context['run'], candidate['report_source']).read_bytes()
    if digest(report) != entry['original_report']['sha256']:
        raise RenderReuseError('original-report-bytes-changed')
    _write_bytes(target_run, entry['original_report']['path'], report)
    if candidate['origin_source'] is not None:
        record = owned(context['run'], candidate['origin_source']).read_bytes()
    else:
        record = candidate['origin_bytes']
    if digest(record) != entry['origin_record']['sha256']:
        raise RenderReuseError('original-origin-record-changed')
    _write_bytes(target_run, entry['origin_record']['path'], record)
    for relative in entry['dependencies']['layout_sha256']:
        contents = owned(context['run'], relative).read_bytes()
        if digest(contents) != entry['dependencies']['layout_sha256'][relative]:
            raise RenderReuseError('layout-bytes-changed: ' + relative)
        _write_bytes(target_run, relative, contents)


def _plan_reasons(fresh):
    return [{'page': page, 'reasons': reasons} for page, reasons in sorted(fresh.items())]


def plan_render_reuse(state, baseline_run=None):
    """Select compatible pages to retain and every other page for a fresh render."""
    run = Path(state['run']).resolve()
    run.mkdir(parents=True, exist_ok=True)
    manifest = load(run, 'manifest.json')
    target_contract = _target_contract(state, manifest)
    manifest_pages = _renderable_pages(manifest, state['manual'])
    if not manifest_pages:
        raise RenderReuseError('no-renderable-pages')
    compile_pages = _compile_pages(run)
    missing_compile = set(manifest_pages) - set(compile_pages)
    if missing_compile:
        raise RenderReuseError('target-compile-coverage-mismatch: ' + ', '.join(sorted(missing_compile)))
    try:
        current_inputs = renderer_inputs(state)
        inputs_error = None
    except (OSError, ValueError, KeyError, TypeError, RenderReuseError) as exc:
        current_inputs, inputs_error = None, str(exc)
        fresh = {page: [inputs_error] for page in manifest_pages}
        fresh_via_input_error = True
    else:
        fresh, fresh_via_input_error = {}, False
        _replace_json(run, RENDER_INPUTS, current_inputs)

    context = None
    prior = None
    baseline_error = None
    if baseline_run is None:
        baseline_error = 'no-compatible-evaluated-baseline'
    else:
        try:
            context = _baseline_context(baseline_run, run)
        except (OSError, ValueError, KeyError, TypeError, RenderReuseError) as exc:
            baseline_error = 'unusable-evaluated-baseline: ' + str(exc)
        if context is not None:
            plan_file = context['run'] / PLAN_PATH
            if plan_file.exists():
                try:
                    prior = validate_render_reuse(context['state'])
                except (OSError, ValueError, KeyError, TypeError, RenderReuseError) as exc:
                    baseline_error = 'invalid-preceding-render-plan: ' + str(exc)
                    context = None
            elif not fresh_via_input_error and context['inputs'] != current_inputs:
                changed = sorted(path for path in set(context['inputs']) | set(current_inputs)
                                 if context['inputs'].get(path) != current_inputs.get(path))
                baseline_error = 'renderer-inputs-changed: ' + ', '.join(changed)
                context = None

    reused = {}
    if context is not None and not fresh_via_input_error:
        for page in sorted(manifest_pages):
            try:
                facts = _target_facts(state, manifest_pages, compile_pages, page)
                if prior is not None and page in prior['reused_pages']:
                    candidate = _prior_entry(context, page, prior)
                elif page in context['rendered']:
                    candidate = _fresh_baseline_entry(context, page, context['report']['sha256'])
                else:
                    raise RenderReuseError('page-added-since-baseline')
                _compare_target(candidate['entry'], facts, current_inputs)
                candidate['entry']['dependencies']['renderer_inputs'] = copy.deepcopy(current_inputs)
                _stage_candidate(run, context, candidate)
                reused[page] = candidate['entry']
            except (OSError, ValueError, KeyError, TypeError, RenderReuseError) as exc:
                fresh[page] = ['render-evidence-incompatible: ' + str(exc)]
    elif not fresh_via_input_error:
        for page in manifest_pages:
            fresh[page] = [baseline_error or 'no-compatible-evaluated-baseline']

    target = {**target_contract,
              'renderer_inputs_sha256': digest(canonical(current_inputs)) if current_inputs is not None else None}
    plan = {
        'format': FORMAT,
        'status': 'validated',
        'target': target,
        'render_inputs': current_inputs,
        'fresh_pages': sorted(fresh),
        'reused_pages': sorted(reused),
        'rejected_pages': _plan_reasons(fresh),
        'pages': reused,
    }
    plan_sha256 = _replace_json(run, PLAN_PATH, plan)
    pointer = {'path': PLAN_PATH, 'sha256': plan_sha256,
               'fresh_pages': plan['fresh_pages'], 'reused_pages': plan['reused_pages']}
    state['render_reuse'] = pointer
    run_state = owned(run, 'run.json')
    if run_state.exists():
        stored = load(run, 'run.json')
        stored['render_reuse'] = pointer
        _replace_json(run, 'run.json', stored)
    else:
        _replace_json(run, 'run.json', state)
    return plan


def _load_plan(state):
    pointer = state.get('render_reuse')
    if pointer is not None:
        if (not isinstance(pointer, dict) or pointer.get('path') != PLAN_PATH
                or not HASH.fullmatch(pointer.get('sha256', ''))):
            raise RenderReuseError('invalid-render-reuse-pointer')
        if file_hash(state['run'], PLAN_PATH) != pointer['sha256']:
            raise RenderReuseError('render-reuse-plan-changed')
    return load(state['run'], PLAN_PATH)


def _validate_origin_entry(state, page, entry, facts):
    report_pointer = entry.get('original_report', {})
    report_path, report_sha256 = report_pointer.get('path'), report_pointer.get('sha256')
    if not isinstance(report_path, str) or not ORIGIN_REPORT.fullmatch(report_path) \
            or report_path != 'render-origins/' + report_sha256 + '.json' \
            or file_hash(state['run'], report_path) != report_sha256:
        raise RenderReuseError('original-report-path-or-hash-invalid: ' + page)
    report = load(state['run'], report_path)
    if report.get('status') != 'passed':
        raise RenderReuseError('original-report-not-passed: ' + page)
    original_pages = _report_pages(report)
    if original_pages.get(page) != entry.get('render'):
        raise RenderReuseError('false-original-render-page: ' + page)
    record_pointer = entry.get('origin_record', {})
    record_path = record_pointer.get('path')
    if (not isinstance(record_path, str) or not ORIGIN_RECORD.fullmatch(record_path)
            or file_hash(state['run'], record_path) != record_pointer.get('sha256')):
        raise RenderReuseError('original-origin-record-invalid: ' + page)
    record = load(state['run'], record_path)
    if (record.get('format') != FORMAT or record.get('page') != page
            or record.get('report') != report_pointer or record.get('origin') != entry.get('origin')
            or record.get('dependencies') != entry.get('dependencies')
            or record.get('citation_anchors') != entry.get('citation_anchors')):
        raise RenderReuseError('false-origin-provenance: ' + page)
    origin = entry.get('origin', {})
    if (not isinstance(origin.get('run_id'), str) or not origin['run_id']
            or not isinstance(origin.get('application_revision'), str) or not origin['application_revision']
            or not isinstance(origin.get('manual_revision'), str) or not origin['manual_revision']
            or not isinstance(origin.get('release'), str) or not origin['release']):
        raise RenderReuseError('invalid-origin-identity: ' + page)
    if (entry['dependencies'].get('page_sha256') != facts['page_sha256']
            or entry['dependencies'].get('section_ids') != facts['section_ids']
            or entry['dependencies'].get('section_records') != facts['section_records']
            or entry['dependencies'].get('screenshot_hashes') != facts['screenshot_hashes']
            or entry['dependencies'].get('renderer_inputs') != renderer_inputs(state)):
        raise RenderReuseError('current-target-differs-from-render-origin: ' + page)
    if facts['citation_anchors'] != entry.get('citation_anchors'):
        raise RenderReuseError('retained-citation-anchor-changed: ' + page)
    try:
        layout_hashes = _inspect_page_record(entry.get('render'), facts, state['run'])
    except (OSError, ValueError, KeyError, TypeError, RenderReuseError) as exc:
        raise RenderReuseError('staged-layout-evidence-invalid: ' + page) from exc
    if layout_hashes != entry['dependencies'].get('layout_sha256'):
        raise RenderReuseError('staged-layout-hashes-changed: ' + page)


def validate_render_reuse(state):
    """Fail closed unless staged render reuse still matches the exact target."""
    plan = _load_plan(state)
    pointer = state.get('render_reuse')
    if pointer is not None and (
            pointer.get('fresh_pages') != plan.get('fresh_pages')
            or pointer.get('reused_pages') != plan.get('reused_pages')):
        raise RenderReuseError('render-reuse-pointer-selection-mismatch')
    run = Path(state['run']).resolve()
    manifest = load(run, 'manifest.json')
    target = _target_contract(state, manifest)
    if plan.get('format') != FORMAT or plan.get('status') != 'validated':
        raise RenderReuseError('invalid-render-reuse-plan')
    inputs = renderer_inputs(state)
    stored_inputs = load(run, RENDER_INPUTS)
    expected_renderer_hash = digest(canonical(inputs))
    if (plan.get('render_inputs') != inputs or stored_inputs != inputs
            or plan.get('target', {}).get('renderer_inputs_sha256') != expected_renderer_hash):
        raise RenderReuseError('renderer-inputs-changed-after-plan')
    if any(plan.get('target', {}).get(key) != value for key, value in target.items()):
        raise RenderReuseError('stale-render-reuse-target')
    manifest_pages = _renderable_pages(manifest, state['manual'])
    compile_pages = _compile_pages(run)
    fresh_pages, reused_pages = plan.get('fresh_pages'), plan.get('reused_pages')
    if (not isinstance(fresh_pages, list) or not isinstance(reused_pages, list)
            or len(fresh_pages) != len(set(fresh_pages)) or len(reused_pages) != len(set(reused_pages))
            or set(fresh_pages) & set(reused_pages)
            or set(fresh_pages) | set(reused_pages) != set(manifest_pages)
            or set(reused_pages) != set(plan.get('pages', {}))):
        raise RenderReuseError('render-reuse-selection-mismatch')
    rejected = plan.get('rejected_pages', [])
    if (not isinstance(rejected, list) or {item.get('page') for item in rejected if isinstance(item, dict)} != set(fresh_pages)
            or any(not isinstance(item.get('reasons'), list) or not item['reasons'] for item in rejected if isinstance(item, dict))):
        raise RenderReuseError('missing-fresh-page-reasons')
    reused, target_facts = {}, {}
    for page in reused_pages:
        entry = plan['pages'][page]
        if not isinstance(entry, dict) or entry.get('page') != page or not isinstance(entry.get('render'), dict):
            raise RenderReuseError('invalid-reused-page-entry: ' + page)
        facts = _target_facts(state, manifest_pages, compile_pages, page)
        target_facts[page] = facts
        _validate_origin_entry(state, page, entry, facts)
        reused[page] = entry
    for page in fresh_pages:
        target_facts[page] = _target_facts(state, manifest_pages, compile_pages, page)
    render_file = owned(run, 'render.json')
    if render_file.exists():
        final = load(run, 'render.json')
        final_pages = _report_pages(final)
        if final.get('status') != 'passed' or set(final_pages) != set(target_facts):
            raise RenderReuseError('final-render-report-stale')
        for page, entry in reused.items():
            if final_pages[page] != entry['render']:
                raise RenderReuseError('final-render-relabelled-reused-page: ' + page)
        for page in fresh_pages:
            _inspect_page_record(final_pages[page], target_facts[page], run)
    return plan
