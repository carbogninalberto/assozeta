"""Verify generated pages through the in-app reader projection.

This replaces the former Mintlify preview server as the render gate. The
in-app reader (``UI/src/routes/manuale``) displays the ``reader`` blocks that
``index.reader_blocks`` derives from each verified section, and deep-links to
sections by their corpus identity (``/#/manuale?section=<page>#<section>``).
This check therefore runs the same section split and block projection the
published bundle uses, without a server or a browser, and fails closed when a
verified section, its exact text or any displayed screenshot would not reach
the reader. The real-browser rendering of that reader against the real backend
remains the separate ``embedded.mjs`` acceptance check.

No layout screenshots are produced: page records carry ``layouts: []`` and the
``renderer`` label, so render-reuse validation can distinguish them from
retained Mintlify-era records (which still require their Full HD layouts).
"""
from pathlib import Path

from .index import EvidenceError, digest, png_dimensions, reader_blocks, sections
from .reuse import ReuseError, owned


READER_RENDERER = 'assozeta-reader'
READER_RENDERER_FORMAT = 1
READER_CHECKS = [
    'every verified section is a unique in-app reader section with its exact verified text',
    'citation anchors are the in-app reader section identities',
    'every displayed screenshot is a committed PNG with its recorded hash and appears in the reader blocks',
    'no Mintlify server or browser is required; the real-browser reader check is embedded-manual.json',
]


class ReaderRenderError(EvidenceError):
    """A generated page would not be displayed faithfully by the in-app reader."""


def _displayed(section):
    screenshots = section.get('screenshots', [])
    if not isinstance(screenshots, list) or any(
            not isinstance(image, dict) or not isinstance(image.get('path'), str) for image in screenshots):
        raise ReaderRenderError('invalid-section-screenshots')
    displayed = section.get('displayed_screenshots')
    if displayed is None:
        return screenshots
    if (not isinstance(displayed, list) or len(displayed) != len(set(displayed))
            or not set(displayed).issubset({image['path'] for image in screenshots})):
        raise ReaderRenderError('displayed-screenshot-not-bound')
    return [image for image in screenshots if image['path'] in displayed]


def render_page(manual, manifest_page):
    """Return the render record for one manifest page with verified sections.

    Assigns ``citation_anchor`` on each verified manifest section in place.
    """
    relative = manifest_page.get('path') if isinstance(manifest_page, dict) else None
    if not isinstance(relative, str) or not relative.endswith('.mdx'):
        raise ReaderRenderError('invalid-manifest-page-path')
    page = relative[:-4]
    try:
        contents = owned(manual, relative).read_bytes()
    except (OSError, ReuseError) as exc:
        raise ReaderRenderError('missing-generated-page: ' + relative) from exc
    available = sections(contents.decode('utf-8'))
    record = {'renderer': READER_RENDERER, 'page': page, 'source_sha256': digest(contents),
              'anchors': [], 'images': [], 'layouts': [], 'sections': []}
    for section in manifest_page.get('sections', []):
        if section.get('status') != 'verified':
            continue
        identifier = section.get('id')
        if identifier not in available:
            raise ReaderRenderError(f'reader-section-missing: {page}#{identifier}')
        body = available[identifier]['mdx']
        if digest(body) != section.get('content_sha256'):
            raise ReaderRenderError(f'reader-section-text-differs: {page}#{identifier}')
        displayed = _displayed(section)
        for image in displayed:
            try:
                data = owned(manual, image['path']).read_bytes()
            except (OSError, ReuseError) as exc:
                raise ReaderRenderError('missing-displayed-screenshot: ' + image['path']) from exc
            png_dimensions(data)
            if digest(data) != image.get('sha256'):
                raise ReaderRenderError('displayed-screenshot-changed: ' + image['path'])
        blocks = reader_blocks(body, section.get('screenshots', []))
        if not blocks:
            raise ReaderRenderError(f'reader-section-empty: {page}#{identifier}')
        shown = {path for block in blocks for path in block['screenshots']}
        missing = [image['path'] for image in displayed if image['path'] not in shown]
        if missing:
            raise ReaderRenderError(f'reader-image-not-displayed: {page}#{identifier}: ' + ', '.join(missing))
        # The reader, Bot and MCP cite sections by corpus identity.
        section['citation_anchor'] = identifier
        record['anchors'].append(identifier)
        record['images'].extend(image['path'] for image in displayed)
        record['sections'].append({'id': identifier, 'citation_anchor': identifier,
                                   'reader_blocks': len(blocks), 'images': [image['path'] for image in displayed]})
    if not record['anchors']:
        raise ReaderRenderError('no-verified-sections: ' + page)
    return record


def render_pages(manual, manifest, pages):
    """Render the selected page identifiers (``docs/x`` form) in manifest order."""
    selected = set(pages)
    records = []
    for manifest_page in manifest.get('pages', []):
        path = manifest_page.get('path', '')
        if path.removesuffix('.mdx') in selected:
            records.append(render_page(Path(manual), manifest_page))
    found = {record['page'] for record in records}
    if found != selected:
        raise ReaderRenderError('render-page-coverage-mismatch: ' + ', '.join(sorted(selected - found)))
    return records
