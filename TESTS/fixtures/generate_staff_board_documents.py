"""Author the staff-board rich-document validator corpus.

Expectations are written here from the documented contract, not observed from
Django. Size boundaries use the contract's definition (Python ``json.dumps``
byte length, default separators and ASCII escaping), so the stdlib encoder
computes padding only. Run with ``python3 -I`` and commit the output.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('staff-board-documents.json')
MAX = 14 * 1024 * 1024
DOC = 'Documento non valido.'
SIZE = 'Il messaggio con le immagini è troppo grande (massimo 14 MB).'
STRUCT = 'Struttura del documento non valida.'
ATTRS = 'Attributi non validi.'
TEXT = 'Testo non valido.'
IMAGE = 'Usa immagini PNG, JPEG, GIF o WebP, oppure un URL HTTP/HTTPS.'
DIM = 'Dimensione non valida.'
FORMAT = 'Formattazione non valida.'
LINK = 'Link non valido.'
CONTENT = 'Contenuto non valido.'
EMPTY = 'Scrivi un messaggio o aggiungi un’immagine.'
LONG = 'Il testo può contenere al massimo 10000 caratteri.'
PIXEL = 'data:image/png;base64,iVBORw0KGgo='

cases = []


def add(name, document, *, error=None, cleaned=None, text=None, exception=None, template=None):
    case = {'name': name, 'document_json': document if isinstance(document, str) else json.dumps(document, ensure_ascii=False)}
    if template:
        case['repeat'] = template
    if exception:
        case['expect'] = {'exception': exception}
    elif error:
        case['expect'] = {'error': error}
    else:
        case['expect'] = {'cleaned': cleaned, 'text': text}
    cases.append(case)


def text(value, **extra):
    return {'type': 'text', 'text': value, **extra}


def para(*children, **extra):
    node = {'type': 'paragraph', **extra}
    if children:
        node['content'] = list(children)
    return node


def doc(*children):
    node = {'type': 'doc'}
    if children:
        node['content'] = list(children)
    return node


def link(href):
    return doc(para(text('x', marks=[{'type': 'link', 'attrs': {'href': href}}])))


def link_ok(href):
    return doc(para(text('x', marks=[{'type': 'link', 'attrs': {'href': href, 'target': '_blank', 'rel': 'noopener noreferrer'}}])))


def image(src, **attrs):
    return doc({'type': 'image', 'attrs': {'src': src, **attrs}})


simple = doc(para(text('Ciao')))
add('plain-paragraph', simple, cleaned=simple, text='Ciao')
add('heading-sanitized-attributes',
    doc({'type': 'heading', 'attrs': {'class': 'x', 'textAlign': 'center', 'onclick': 'y', 'level': 2}, 'content': [text('T')]}),
    cleaned=doc({'type': 'heading', 'attrs': {'level': 2, 'textAlign': 'center'}, 'content': [text('T')]}), text='T')
add('unknown-text-align-dropped', doc(para(text('a'), attrs={'textAlign': 'middle'})), cleaned=doc(para(text('a'))), text='a')
add('unhashable-text-align-dropped', doc(para(text('a'), attrs={'textAlign': ['left']})), cleaned=doc(para(text('a'))), text='a')
for label, value in (('null', None), ('empty-list', []), ('zero', 0), ('zero-float', 0.0), ('false', False), ('empty-string', ''), ('empty-object', {})):
    add(f'falsy-attrs-{label}', doc(para(text('a'), attrs=value)), cleaned=doc(para(text('a'))), text='a')
for label, value in (('string', 'x'), ('list', [1]), ('number', 1), ('true', True)):
    add(f'invalid-attrs-{label}', doc(para(text('a'), attrs=value)), error=ATTRS)
add('text-not-string', doc(para({'type': 'text', 'text': 5})), error=TEXT)
add('text-missing', doc(para({'type': 'text'})), error=TEXT)
add('text-extra-keys-dropped', doc(para(text('a', foo=1, html='<b>'))), cleaned=doc(para(text('a'))), text='a')
add('text-in-block-ignored', doc(para(text('a'), text='hidden')), cleaned=doc(para(text('a'))), text='a')
add('marks-normalized',
    doc(para(text('a', marks=[{'type': 'bold', 'attrs': {'x': 1}}, {'type': 'link', 'attrs': {'href': 'https://example.org/p?q=1#f', 'target': '_self', 'x': 1}}]))),
    cleaned=doc(para(text('a', marks=[{'type': 'bold'}, {'type': 'link', 'attrs': {'href': 'https://example.org/p?q=1#f', 'target': '_blank', 'rel': 'noopener noreferrer'}}]))), text='a')
add('marks-empty-list-omitted', doc(para(text('a', marks=[]))), cleaned=doc(para(text('a'))), text='a')
add('marks-null', doc(para(text('a', marks=None))), error=FORMAT)
add('marks-object', doc(para(text('a', marks={}))), error=FORMAT)
add('marks-ten', doc(para(text('a', marks=[{'type': 'bold'}] * 10))), cleaned=doc(para(text('a', marks=[{'type': 'bold'}] * 10))), text='a')
add('marks-eleven', doc(para(text('a', marks=[{'type': 'bold'}] * 11))), error=FORMAT)
add('mark-unknown', doc(para(text('a', marks=[{'type': 'color'}]))), error=FORMAT)
add('mark-not-object', doc(para(text('a', marks=['bold']))), error=FORMAT)
add('mark-unhashable-type', doc(para(text('a', marks=[{'type': {}}]))), exception='TypeError')
for label, href in (('javascript', 'javascript:alert(1)'), ('ftp', 'ftp://example.org'), ('no-netloc', 'http://'),
                    ('opaque-https', 'https:example.org'), ('scheme-relative', '//example.org'), ('space', 'https://a b'),
                    ('tab', 'https://a\tb'), ('delete-char', 'https://a\x7f'), ('leading-space', ' https://a'),
                    ('open-bracket', 'http://[::1'), ('close-bracket', 'http://::1]'), ('bracketed-ipv4', 'http://[127.0.0.1]/'),
                    ('bad-ipvfuture', 'http://[vz.x]'), ('ipv4-leading-zero', 'http://[::ffff:01.2.3.4]'),
                    ('empty-scope', 'http://[fe80::1%]'), ('data-after-bracket', 'http://[::1]x'),
                    ('data-before-bracket', 'http://x[::1]'), ('nine-hextets', 'http://[1:2:3:4:5:6:7:8:9]'),
                    ('long-hextet', 'http://[12345::]'), ('double-skip', 'http://[1::2::3]'),
                    ('bracket-in-userinfo', 'http://[x@host]'), ('nfkc-slash', 'https://exa℀mple.org'),
                    ('nfkc-colon', 'https://a︓b.example'), ('not-string', 5)):
    add(f'link-rejected-{label}', link(href), error=LINK)
add('link-attrs-null', doc(para(text('x', marks=[{'type': 'link', 'attrs': None}]))), error=LINK)
add('link-attrs-string', doc(para(text('x', marks=[{'type': 'link', 'attrs': 'x'}]))), error=LINK)
add('link-attrs-missing-href', doc(para(text('x', marks=[{'type': 'link', 'attrs': {}}]))), error=LINK)
for label, href in (('mailto', 'mailto:a@b.example'), ('tel', 'tel:+3900'), ('tel-empty', 'tel:'), ('uppercase-scheme', 'HTTPS://EXAMPLE.ORG'),
                    ('ipv6', 'http://[::1]/'), ('ipv6-port', 'http://[::1]:80'), ('ipv6-userinfo', 'http://u@[::1]:8080/x'),
                    ('ipv6-any', 'http://[::]'), ('ipv4-mapped', 'http://[::ffff:1.2.3.4]'), ('scoped', 'http://[fe80::1%25eth0]'),
                    ('ipvfuture', 'http://[v1.x]'), ('ipvfuture-upper', 'http://[V1F.a:b]'), ('fullwidth-host', 'https://ｅxample.org'),
                    ('unicode-host', 'https://例え.jp/p'), ('unicode-path', 'https://example.org/caffè')):
    add(f'link-accepted-{label}', link(href), cleaned=link_ok(href), text='x')
add('image-data-png', image(PIXEL, alt='a', title=5, width=10),
    cleaned=doc({'type': 'image', 'attrs': {'src': PIXEL, 'alt': 'a'}}), text='')
add('image-http-with-dimension', image('https://img.example/x.png', level=2, title='t'),
    cleaned=doc({'type': 'image', 'attrs': {'src': 'https://img.example/x.png', 'title': 't', 'level': 2}}), text='')
add('image-and-text', doc(para(text(' a ')), {'type': 'image', 'attrs': {'src': PIXEL}}),
    cleaned=doc(para(text(' a ')), {'type': 'image', 'attrs': {'src': PIXEL}}), text='a')
for label, src in (('svg', 'data:image/svg+xml;base64,PHN2Zz4='), ('mailto', 'mailto:a@b'), ('empty-payload', 'data:image/png;base64,'),
                   ('three-padding', 'data:image/png;base64,AAA==='), ('uppercase-data', 'DATA:image/png;base64,AAAA'),
                   ('not-string', 5), ('tel', 'tel:1')):
    add(f'image-rejected-{label}', image(src), error=IMAGE)
add('image-missing-src', doc({'type': 'image'}), error=IMAGE)
for label, value, ok in (('level-zero', ('level', 0), False), ('level-seven', ('level', 7), False), ('level-six', ('level', 6), True),
                         ('level-true', ('level', True), False), ('level-float', ('level', 2.0), False), ('level-string', ('level', '2'), False),
                         ('level-null', ('level', None), False), ('start-1000', ('start', 1000), True), ('start-1001', ('start', 1001), False),
                         ('colspan-one', ('colspan', 1), True), ('rowspan-huge', ('rowspan', 99999999999999999999), False),
                         ('level-negative', ('level', -1), False)):
    node = para(text('a'), attrs={value[0]: value[1]})
    if ok:
        add(f'dimension-{label}', doc(node), cleaned=doc(para(text('a'), attrs={value[0]: value[1]})), text='a')
    else:
        add(f'dimension-{label}', doc(node), error=DIM)
task = lambda attrs=None: {'type': 'taskItem', **({'attrs': attrs} if attrs is not None else {}), 'content': [para(text('t'))]}
for label, attrs, checked in (('true', {'checked': True}, True), ('string', {'checked': 'true'}, False), ('one', {'checked': 1}, False), ('absent', None, False)):
    add(f'task-checked-{label}', doc({'type': 'taskList', 'content': [task(attrs)]}),
        cleaned=doc({'type': 'taskList', 'content': [{'type': 'taskItem', 'attrs': {'checked': checked}, 'content': [para(text('t'))]}]}), text='t')
for label, document in (('array', '[]'), ('string', '"doc"'), ('paragraph-root', para(text('a'))), ('missing-type', {'content': []}),
                        ('null', 'null')):
    add(f'root-rejected-{label}', document, error=DOC)
add('root-duplicate-type-last-wins', '{"type":"paragraph","content":[{"type":"paragraph","content":[{"type":"text","text":"a"}]}],"type":"doc"}',
    cleaned=simple | {'content': [para(text('a'))]}, text='a')
for label, child in (('script', {'type': 'script'}), ('not-object', 'p'), ('null-type', {'type': None}), ('number-type', {'type': 1}),
                     ('missing-type', {})):
    add(f'node-rejected-{label}', doc(child), error=STRUCT)
add('node-unhashable-type', doc({'type': []}), exception='TypeError')
for label, value in (('object', {}), ('null', None), ('string', 'x')):
    add(f'content-rejected-{label}', {'type': 'doc', 'content': value}, error=CONTENT)


def nested(depth):
    node = para(text('d'))
    for _ in range(depth - 1):
        node = {'type': 'blockquote', 'content': [node]}
    return doc(node)


# The paragraph sits at depth n-1 and its text node at depth n.
add('depth-thirty', nested(29), cleaned=nested(29), text='d')
add('depth-thirty-one', nested(30), error=STRUCT)
many = lambda count: doc(para(*[text('a') for _ in range(count)]))
add('node-count-5000', many(4998), cleaned=many(4998), text='a' * 4998)
add('node-count-5001', many(4999), error=STRUCT)
add('text-10000', doc(para(text('  ' + 'a' * 10000 + '  '))), cleaned=doc(para(text('  ' + 'a' * 10000 + '  '))), text='a' * 10000)
add('text-10001', doc(para(text('a' * 10001))), error=LONG)
add('empty-paragraph', doc(para()), error=EMPTY)
add('python-whitespace-only', doc(para(text(' 　\x1c\x1f\x85 \t'))), error=EMPTY)
add('zero-width-space-is-text', doc(para(text('​'))), cleaned=doc(para(text('​'))), text='​')
add('hard-break-newlines', doc(para(text('a'), {'type': 'hardBreak'}, text('b')), para(text('c'))),
    cleaned=doc(para(text('a'), {'type': 'hardBreak'}, text('b')), para(text('c'))), text='a\nb\nc')
rich = doc({'type': 'heading', 'attrs': {'level': 1}, 'content': [text('Titolo')]},
           {'type': 'bulletList', 'content': [{'type': 'listItem', 'content': [para(text('uno'))]}]},
           {'type': 'orderedList', 'attrs': {'start': 3}, 'content': [{'type': 'listItem', 'content': [para(text('due', marks=[{'type': 'italic'}]))]}]},
           {'type': 'codeBlock', 'content': [text('x = 1')]}, {'type': 'horizontalRule'}, {'type': 'pageBreak'},
           {'type': 'table', 'content': [{'type': 'tableRow', 'content': [{'type': 'tableHeader', 'attrs': {'colspan': 2, 'rowspan': 1}, 'content': [para(text('h'))]}]}]},
           {'type': 'blockquote', 'content': [para(text('q'))]})
add('editor-rich-document', rich, cleaned=rich, text='Titolo\nuno\n\ndue\n\nx = 1\nh\nq')
add('lone-surrogate-text-accepted', '{"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":"\\ud800"}]}]}',
    cleaned=None, text=None)
cases[-1]['expect'] = {'cleaned_json': '{"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "\\ud800"}]}]}',
                       'text_json': '"\\ud800"'}
add('astral-text-length', doc(para(text('😀' * 10000))), cleaned=doc(para(text('😀' * 10000))), text='😀' * 10000)


def boundary(name, pad, extra, over):
    """Pad an ignored attribute so json.dumps length is MAX or MAX+1."""
    prefix = {'type': 'doc', 'content': [para(text('a'), attrs={'p': '@@REPEAT@@', **extra})]}
    raw = json.dumps(prefix, ensure_ascii=False)
    encoded = json.dumps(json.loads(raw))  # contract definition, with an empty repeat
    unit = len(json.dumps(pad)) - 2
    base = len(encoded) - len('@@REPEAT@@')
    count, remainder = divmod(MAX + (1 if over else 0) - base, unit)
    assert remainder == 0, (name, remainder)
    add(name, raw, template={'unit': pad, 'count': count}, error=SIZE if over else None,
        cleaned=None if over else doc(para(text('a'))), text=None if over else 'a')


boundary('size-ascii-at-limit', 'a', {}, False)
boundary('size-ascii-over-limit', 'a', {}, True)
# A two-byte UTF-8 character encodes as six bytes under ASCII escaping.
boundary('size-escaped-over-limit', 'a', {'e': 'é'}, True)
boundary('size-escaped-at-limit', 'a', {'e': 'é'}, False)
# Number tokens are re-encoded with Python repr before measuring.
for label, token in (('exponent', '1e300'), ('infinity', '1e400'), ('negative-zero', '-0'), ('float-zero', '0e5'), ('big-int', '123456789012345678901234567890')):
    for over in (False, True):
        name = f'size-number-{label}-{"over" if over else "at"}-limit'
        boundary(name, 'a', {'n': '@@NUM@@'}, over)
        case = cases[-1]
        case['document_json'] = case['document_json'].replace('"@@NUM@@"', token)
        value = json.loads(token)
        shift = len(json.dumps(value)) - len('"@@NUM@@"')
        case['repeat']['count'] -= shift

for case in cases:
    if case['expect'].get('cleaned') is None and 'error' not in case['expect'] and 'exception' not in case['expect'] and 'cleaned_json' not in case['expect']:
        raise SystemExit(f'missing expectation: {case["name"]}')
names = [case['name'] for case in cases]
assert len(names) == len(set(names))
lines = ',\n'.join(json.dumps(case, ensure_ascii=False, separators=(',', ':')) for case in cases)
OUTPUT.write_text(f'{{"format":1,"max_document_bytes":{MAX},"cases":[\n{lines}\n]}}\n')
print(len(cases), 'cases')
