"""Authored synthetic installed corpus for browser transport/presentation checks.

This intentionally small sealed model and fictional screenshot are test data,
not reviewed manual evidence or a publishable release package.
"""
import json
import struct
import zlib

WORKFLOW_SOURCES = (
    'src/routes/manuale/Manuale.svelte', 'src/routes/manuale/ManualSteps.svelte',
    'src/routes/manuale/ManualImage.svelte', 'src/routes/manuale/ManualMarkdown.svelte',
    'src/routes/manuale/ManualContent.svelte', 'src/routes/manuale/ManualLink.svelte',
    'src/routes/manuale/manualPresentation.js',
)


def prepare_manual(work):
    from application.manuale.index import canonical, digest
    revision = 'a' * 40
    release = 'profile-browser-fixture'
    root = work/'release-manual'; root.mkdir()
    # A deterministic 640x360 RGB test pattern exercises responsive image sizing;
    # a tiny pixel fixture would not reveal horizontal overflow on mobile.
    def png_chunk(kind, payload):
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload))
    pixels = b''.join(b'\0' + bytes((224, 232, 247) if y % 80 < 40 else (63, 81, 181)) * 640 for y in range(360))
    png = (b'\x89PNG\r\n\x1a\n' + png_chunk(b'IHDR', struct.pack('>IIBBBBB', 640, 360, 8, 2, 0, 0, 0))
           + png_chunk(b'IDAT', zlib.compress(pixels)) + png_chunk(b'IEND', b''))
    chunks = []
    for number, (section, title, text, audience) in enumerate((
        ('start', 'Leggere la guida fixture', 'Questa procedura usa dati fittizi per verificare il lettore.', 'public'),
        ('image', 'Osservare la schermata fixture', 'Apri la schermata per visualizzarla e poi chiudila.', 'public'),
        ('private', 'Private maintainer canary', 'Private evidence must never appear in the reader.', 'maintainer'),
    )):
        image_path = 'images/'+section+'.png'
        path = root/image_path; path.parent.mkdir(exist_ok=True); path.write_bytes(png)
        chunks.append(dict(id='docs/fixture#'+section, page='docs/fixture', section=section, title=title,
            text=text, status='verified', url='https://manual.example.test/docs/fixture#'+section,
            screenshots=[dict(path=image_path, sha256=digest(png))], scenario_ids=['browser-fixture'],
            page_title='Guida di prova', page_description='Istruzioni con dati fittizi.',
            page_order=1, section_order=number, language='it', intent='read', audience=audience,
            features=[], evidence=[], runtime_dependencies={}, private_secret='private-source-canary',
            reader=[dict(title='Passaggio di prova', markdown=text, screenshots=[image_path])]))
    corpus = dict(format=1, embedding='italian-tfidf-lsa-v5', dependencies={}, chunks=chunks,
        content_identity='d'*64, semantics=dict(vocabulary=['fixture'], idf=[1.0], basis=[[1.0]],
                                               vectors=[[1.0] for _ in chunks]),
        metadata=dict(application_revision=revision, release=release,
            publication_status='release-bundled', code_state='committed', manual_revision='c'*40,
            manual_state='generated-from-committed', verification={'corpus_identity': 'd'*64}))
    corpus['identity'] = digest(canonical(corpus))
    (root/'index.json').write_text(json.dumps(corpus, ensure_ascii=False))
    (root/'bundle.json').write_text(json.dumps(dict(format=1, distribution='immutable-backend-image',
        index_sha256=digest((root/'index.json').read_bytes()), corpus_identity=corpus['identity'],
        application_revision=revision, release=release, manual_revision='c'*40, verified_corpus_identity='d'*64)))
    (work/'CODE_REVISION').write_text(revision)
    (work/'VERSION').write_text(release)
    (work/'MANUAL_REQUIRED').write_text('true')
    return dict(MANUAL_BUNDLE_PATH=str(root), MANUAL_BUNDLE_REQUIRED=True,
                MANUAL_APPLICATION_REVISION=revision), dict(
        page_title='Guida di prova', public_ids=[row['id'] for row in chunks[:2]],
        titles=[row['title'] for row in chunks[:2]], prose=[row['text'] for row in chunks[:2]],
        query='fixture', absent_query='xyznotfound', image_sha256=digest(png),
        image_width=640, image_height=360, private_path='images/private.png')
