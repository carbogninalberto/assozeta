"""BeautifulSoup(markup, features="html.parser").prettify() golden corpus for Go.

BE_GO/internal/bs4prettify ports bs4 4.13.3 prettify() for a declared subset.
This script runs the real bs4 (production Python 3.13.16) on

- documents the subscription-template editor (TipTap 3: paragraphs, headings,
  marks, links, lists, task lists, tables, images, mentions, code blocks,
  alignment styles) produces, and the stored values those become after one and
  two prettify() round trips (every save prettifies the stored HTML again);
- seeded random documents generated from the subset grammar (each must be
  accepted by Go with byte-identical output);
- seeded random markup soup and one-character mutations of generated documents
  (Go must refuse them or match).

``--write-editor`` refreshes the committed editor golden file used by the plain
Go unit test (and BE_GO/internal/dto/testdata/python_upper.json, CPython's
str.upper() for the section-name characters the port accepts); without it the full corpus is written to a temporary file and
checked with ``go test -tags=bs4integration`` (``--seed N`` and ``--scale K`` widen
the random part). Run with the production Python.
"""
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
EDITOR = ROOT / 'BE_GO/internal/bs4prettify/testdata/editor.json'
PACKAGE = './internal/bs4prettify'
UPPER = ROOT / 'BE_GO/internal/dto/testdata/python_upper.json'


def accepted_upper(c):
    """dto.profileTemplateUpperSupported, restated."""
    if c in (0xdf, 0x149, 0x1f0, 0xfffd):
        return False
    return (0x20 <= c < 0x7f or 0xa0 <= c < 0x250 or 0x2000 <= c < 0x2c00 or 0x3000 <= c < 0xa000 or 0x1f000 <= c < 0x1fb00
            or c in (9, 10, 13))

TAGS = ('a b blockquote code div em h1 h2 h3 h4 h5 h6 i label li mark ol p pre s span strong sub sup table tbody td th '
        'thead tr u ul colgroup del ins small center font figure figcaption caption section article').split()
VOIDS = 'br hr img input col wbr'.split()
NAMED = ('amp lt gt quot apos nbsp agrave aacute egrave eacute igrave iacute ograve oacute ugrave uacute Agrave Egrave '
         'Eacute Igrave Ograve Ugrave ccedil euro copy reg trade laquo raquo ldquo rdquo lsquo rsquo ndash mdash hellip '
         'deg middot bull times sect').split()
SPACES = [' ', '\t', '\n', '\r', '\f', '\xa0', ' ', ' ', ' ', ' ', ' ', ' ', ' ',
          ' ', '　', '​', '﻿']
WORDS = ['Regolamento', 'art.', 'quota', 'àèéìòù', 'ÀÈÉ', 'perché', '€ 20,00', '“iscrizione”', 'l’atleta', '>', '"', "'",
         '=', '/', ';', '#', 'x', '0', '😀', '中文', 'Ω', 'ß', 'ﬁ', '…', '—', 'á', '­']

EDITOR_DOCS = [
    '',
    '<p></p>',
    '<p>Regolamento</p>',
    '<p>Il socio si impegna a rispettare lo statuto.</p><p>Seconda riga</p>',
    '<h1>Titolo</h1><h2>Sottotitolo</h2><h3>Sezione</h3><p>Testo</p>',
    '<p><strong>grassetto</strong> <em>corsivo</em> <u>sottolineato</u> <s>barrato</s> <code>codice</code></p>',
    '<p>Prima<br>Seconda<br><br>Terza</p>',
    '<p style="text-align: center">Centrato</p><p style="text-align: right">Destra</p><h2 style="text-align: justify">G</h2>',
    '<ul><li><p>uno</p></li><li><p>due</p><ul><li><p>annidato</p></li></ul></li></ul>',
    '<ol start="3"><li><p>tre</p></li><li><p>quattro</p></li></ol>',
    '<ul data-type="taskList"><li data-checked="true" data-type="taskItem"><label><input type="checkbox" checked="checked">'
    '<span></span></label><div><p>fatto</p></div></li><li data-checked="false" data-type="taskItem"><label>'
    '<input type="checkbox"><span></span></label><div><p>da fare</p></div></li></ul>',
    '<p><a target="_blank" rel="noopener noreferrer nofollow" href="https://example.test/statuto?a=1&amp;b=2">Statuto</a></p>',
    '<blockquote><p>Citazione</p></blockquote><hr><p>Dopo</p>',
    '<pre><code>riga 1\n  riga 2\n\tfine</code></pre><p>dopo</p>',
    '<pre><code class="language-js">if (a &lt; b &amp;&amp; c &gt; d) {\n  x = "y";\n}</code></pre>',
    '<table style="min-width: 75px"><colgroup><col style="min-width: 25px"><col style="min-width: 25px"><col style="min-width: 25px">'
    '</colgroup><tbody><tr><th colspan="1" rowspan="1"><p>A</p></th><th colspan="1" rowspan="1"><p>B</p></th>'
    '<th colspan="1" rowspan="1"><p>C</p></th></tr><tr><td colspan="1" rowspan="1"><p>1</p></td><td colspan="1" rowspan="1">'
    '<p>2</p></td><td colspan="1" rowspan="1"><p></p></td></tr></tbody></table>',
    '<p><img src="data:image/png;base64,iVBORw0KGgo=" alt="logo" title="Logo &quot;ASD&quot;"></p>',
    '<p>Gentile <span class="mention" data-type="mention" data-id="first_name" data-label="Nome">@Nome</span>, benvenuto</p>',
    '<p>Spazi&nbsp;non&nbsp;separabili&nbsp;</p><p>&nbsp;</p>',
    '<p>Simboli &amp; &lt;tag&gt; "doppi" \'singoli\' € ©</p>',
    '<p>Accenti: perché, città, più, così</p>',
    '<p>   spazi   multipli   </p>\n\n<p>\n  a capo\n</p>',
    '<h4>Quote</h4><p>Quota annuale: <strong>€ 50,00</strong></p><p><em><strong>nidificato</strong></em></p>',
    '<p><mark>evidenziato</mark> e <sup>apice</sup> <sub>pedice</sub></p>',
    '<div class="  a   b\tc "><p class="">vuoto</p></div>',
    '<p>emoji 😀 e cinese 中文</p>',
    '<ul><li><p>a</p></li></ul><ol><li><p>b</p></li></ol><p></p><p></p>',
    'Testo senza tag',
    '<p>senza chiusura',
    '<p>a</p></div><p>b</p>',
    '<P CLASS="x">Maiuscolo</P><BR>',
    '<p>auto<br/>chiuso<br /></p><p/>',
    '<input disabled type="text" value="a&amp;b"><label for="x">L</label>',
]


def random_text(rng, allow_entities=True):
    parts = []
    for _ in range(rng.randint(0, 5)):
        choice = rng.random()
        if choice < 0.35:
            parts.append(rng.choice(WORDS))
        elif choice < 0.6:
            parts.append(''.join(rng.choice(SPACES) for _ in range(rng.randint(1, 3))))
        elif choice < 0.75 and allow_entities:
            parts.append('&%s;' % rng.choice(NAMED))
        elif choice < 0.85 and allow_entities:
            code = rng.choice([0x41, 0x20, 0xe8, 0xa0, 0x20ac, 0x2003, 0x1f600, 0x7e, 0xfffd, 0x3000, 0x10ffff - 2])
            parts.append(rng.choice(['&#%d;', '&#x%x;', '&#X%X;']) % code)
        else:
            parts.append(''.join(rng.choice('abcdefghijklmnopqrstuvwxyz ABC0123456789.,:!?') for _ in range(rng.randint(1, 8))))
    return ''.join(parts)


def random_attributes(rng, tag):
    attrs = []
    for _ in range(rng.randint(0, 3)):
        key = rng.choice(['class', 'style', 'href', 'rel', 'title', 'data-id', 'data-type', 'id', 'Alt', 'colspan', 'rev',
                          'accesskey', 'headers', 'x:y', 'a.b', 'checked', 'class'])
        if rng.random() < 0.15:
            attrs.append(key)
            continue
        value = random_text(rng)
        if rng.random() < 0.3:
            value = rng.choice(SPACES) + 'a' + rng.choice(SPACES) + rng.choice(SPACES) + 'b' + rng.choice(SPACES)
        if '"' in value and "'" in value:
            value = value.replace('"', '&quot;')
        quote = "'" if '"' in value else rng.choice(['"', "'"]) if "'" not in value else '"'
        attrs.append('%s=%s%s%s' % (key, quote, value, quote))
    return ''.join(rng.choice([' ', '  ', '\n', '\t', ' \r\n ']) + a for a in attrs)


def random_document(rng, depth=0):
    out = []
    for _ in range(rng.randint(0, 4 if depth < 3 else 1)):
        choice = rng.random()
        if choice < 0.35:
            out.append(random_text(rng))
        elif choice < 0.5:
            tag = rng.choice(VOIDS)
            out.append('<%s%s%s>' % (rng.choice([tag, tag.upper()]), random_attributes(rng, tag), rng.choice(['', '/', ' /'])))
        elif choice < 0.6:
            tag = rng.choice(TAGS)
            out.append(rng.choice(['</%s>', '</%s >', '</%s\n>']) % rng.choice([tag, tag.upper()]))
        else:
            tag = rng.choice(TAGS + ['pre'] * 3)
            name = rng.choice([tag, tag, tag.upper(), tag.capitalize()])
            if rng.random() < 0.08:
                out.append('<%s%s/>' % (name, random_attributes(rng, tag)))
                continue
            out.append('<%s%s%s>' % (name, random_attributes(rng, tag), rng.choice(['', '', ' '])))
            out.append(random_document(rng, depth + 1))
            if rng.random() < 0.85:
                out.append('</%s>' % rng.choice([tag, name]))
    return ''.join(out)


def random_soup(rng):
    alphabet = ['<', '>', '&', '/', '"', "'", '=', ' ', '\n', ';', '#', 'x', 'p', 'b', 'a', 'br', '!--', '--', '?', '!',
                'script', 'style', 'textarea', 'title', 'amp', 'nbsp', 'foo', '1', '\x00', '\x0b', '\x1c', '\x85', 'é',
                '<p>', '</p>', '<a href=x>', '<!DOCTYPE html>', '<![CDATA[x]]>', '&#0;', '&#128;', '&#x110000;', '&amp',
                '&ampx;', '&#65', '<td', 'A', '-->', '<?php ?>', '\r', '\t', '\xa0']
    return ''.join(rng.choice(alphabet) for _ in range(rng.randint(1, 25)))


def prettify(markup, raises=False):
    """prettify(), or None where bs4 itself raises (the view's 500 page)."""
    from bs4 import BeautifulSoup
    try:
        return BeautifulSoup(markup, features='html.parser').prettify()
    except Exception:
        if raises:
            raise
        return None


def editor_cases():
    cases = []
    for doc in EDITOR_DOCS:
        once = prettify(doc, raises=True)
        twice = prettify(once, raises=True)
        for name, markup in (('editor', doc), ('stored', once), ('stored-twice', twice)):
            cases.append(dict(kind=name, subset=True, input=markup, output=prettify(markup, raises=True)))
    return cases


def corpus(seed=20261109, generated=6000, soup=4000):
    rng = random.Random(seed)
    cases = editor_cases()
    for _ in range(generated):
        doc = random_document(rng)
        cases.append(dict(kind='generated', subset=True, input=doc, output=prettify(doc, raises=True)))
        if rng.random() < 0.1:
            once = cases[-1]['output']
            cases.append(dict(kind='generated-stored', subset=True, input=once, output=prettify(once, raises=True)))
    for _ in range(soup):
        doc = random_soup(rng)
        cases.append(dict(kind='soup', subset=False, input=doc, output=prettify(doc)))
        # Near misses: a generated document with one character inserted or removed.
        doc = random_document(rng) or '<p>x</p>'
        at = rng.randrange(len(doc))
        if rng.random() < 0.5:
            doc = doc[:at] + doc[at + 1:]
        else:
            doc = doc[:at] + rng.choice('<>&/"\'= ;#!-?\x00\x0bpPa') + doc[at:]
        cases.append(dict(kind='mutated', subset=False, input=doc, output=prettify(doc)))
    return cases


def main():
    import bs4
    assert bs4.__version__ == '4.13.3', bs4.__version__
    assert sys.version_info[:3] == (3, 13, 16), sys.version
    if '--write-editor' in sys.argv:
        EDITOR.parent.mkdir(parents=True, exist_ok=True)
        EDITOR.write_text(json.dumps(editor_cases(), ensure_ascii=False, indent=1) + '\n')
        # str.upper() for every code point the template port accepts in a section name.
        accepted = [c for c in range(0x110000) if accepted_upper(c)]
        UPPER.write_text(json.dumps({'accepted': len(accepted), 'upper': {str(c): chr(c).upper() for c in accepted
                                                                          if chr(c).upper() != chr(c)}}, ensure_ascii=False) + '\n')
        print(f'wrote {EDITOR.relative_to(ROOT)} and {UPPER.relative_to(ROOT)}')
        return
    seed = int(sys.argv[sys.argv.index('--seed') + 1]) if '--seed' in sys.argv else 20261109
    scale = int(sys.argv[sys.argv.index('--scale') + 1]) if '--scale' in sys.argv else 1
    cases = corpus(seed, 6000 * scale, 4000 * scale)
    with tempfile.TemporaryDirectory(prefix='az-bs4-corpus-') as directory:
        path = Path(directory) / 'corpus.json'
        path.write_text(json.dumps(cases))
        env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
        subprocess.run(['go', 'test', '-count=1', '-tags=bs4integration', PACKAGE, '-run', '^TestPrettifyAgainstBS4$', '-v'],
                       cwd=ROOT / 'BE_GO', env={**env, 'ASSOZETA_BS4_CORPUS': str(path)}, check=True, timeout=300)
    print(json.dumps(dict(cases=len(cases), subset=sum(c['subset'] for c in cases))))


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
    main()
