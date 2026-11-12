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
str.upper() for the section-name characters the port accepts); without it the
editor/random corpus plus the full corpus are written to a temporary file and
checked with ``go test -tags=bs4integration`` (``--seed N`` and ``--scale K``
widen the random part). Run with the production Python.

``--write-full`` (``--full-out PATH`` for another seed and scale) writes the
full golden corpus: for every input document the html.parser callback stream,
the bs4 tree and prettify()'s output or the exception bs4 raises. That file
targets the port of all input, not just the declared subset.
"""
import html.parser
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import warnings

ROOT = Path(__file__).resolve().parents[3]
EDITOR = ROOT / 'BE_GO/internal/bs4prettify/testdata/editor.json'
FULL = ROOT / 'BE_GO/internal/bs4prettify/testdata/full.json'
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


def _has_surrogate(value):
    return any(0xd800 <= ord(c) <= 0xdfff for c in value)


def enc(value):
    """Encode one golden value so JSON keeps it byte-exact.

    A string with a surrogate code point becomes ``{"hex": ...}`` (a JSON
    parser would not give the same string back); lists, tuples and dicts are
    encoded element-wise; None stays None.
    """
    if isinstance(value, str):
        return {'hex': value.encode('utf-8', 'surrogatepass').hex()} if _has_surrogate(value) else value
    if isinstance(value, (list, tuple)):
        return [enc(v) for v in value]
    if isinstance(value, dict):
        return {k: enc(v) for k, v in value.items()}
    return value


class Recorder(html.parser.HTMLParser):
    """html.parser callbacks as a list, convert_charrefs=False as bs4 uses."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.events = []

    @staticmethod
    def _attrs(attrs):
        return [[name, value] for name, value in attrs]

    def handle_starttag(self, name, attrs):
        self.events.append(['start', name, self._attrs(attrs)])

    def handle_startendtag(self, name, attrs):
        # bs4's parser turns this into a start and an end; record only the
        # single callback html.parser itself delivered here.
        self.events.append(['startend', name, self._attrs(attrs)])

    def handle_endtag(self, name):
        self.events.append(['end', name])

    def handle_data(self, data):
        self.events.append(['data', data])

    def handle_charref(self, name):
        self.events.append(['charref', name])

    def handle_entityref(self, name):
        self.events.append(['entityref', name])

    def handle_comment(self, data):
        self.events.append(['comment', data])

    def handle_decl(self, data):
        self.events.append(['decl', data])

    def handle_pi(self, data):
        self.events.append(['pi', data])

    def unknown_decl(self, data):
        self.events.append(['unknown_decl', data])


def events(markup):
    """(event list, exception class name or None) for one markup string."""
    parser = Recorder()
    try:
        parser.feed(markup)
        parser.close()
    except Exception as exc:
        return parser.events, type(exc).__name__
    return parser.events, None


def _dump(soup):
    """Dump a soup's children iteratively (no Python recursion)."""
    from bs4 import Tag
    out = []
    stack = [(child, out) for child in reversed(list(soup.children))]
    while stack:
        node, siblings = stack.pop()
        if isinstance(node, Tag):
            item = ['tag', node.name, [[name, value] for name, value in node.attrs.items()], []]
            siblings.append(item)
            stack.extend((child, item[3]) for child in reversed(list(node.children)))
        else:
            siblings.append(['str', type(node).__name__, str(node)])
    return out


def tree(markup):
    """The bs4 tree as nested lists, or None when BeautifulSoup raises."""
    from bs4 import BeautifulSoup
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        try:
            soup = BeautifulSoup(markup, features='html.parser')
        except Exception:
            return None
    return _dump(soup)


def full_case(kind, markup):
    """One full golden case: input, parser events, tree, prettify output."""
    from bs4 import BeautifulSoup
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        ev, ev_error = events(markup)
        tr = None
        out = None
        error = None
        try:
            soup = BeautifulSoup(markup, features='html.parser')
        except Exception as exc:
            error = type(exc).__name__
        else:
            tr = _dump(soup)
            try:
                out = soup.prettify()
            except Exception as exc:
                error = type(exc).__name__
                out = None
    return {'kind': kind, 'input': enc(markup), 'events': enc(ev), 'events_error': ev_error,
            'tree': enc(tr), 'output': enc(out), 'error': error}


META_DOCS = [
    '<meta charset="iso-8859-1">',
    '<META CHARSET=latin1>',
    '<meta charset>',
    '<meta charset="">',
    '<meta charset="utf-8">',
    '<meta charset=utf-8>',
    "<meta charset='utf-8'>",
    '<meta charset="a" charset="b">',
    '<meta CHARSET="a" Charset="b">',
    '<meta http-equiv="Content-Type" content="text/html; charset=iso-8859-1">',
    '<meta http-equiv="content-type" content="text/html; charset=utf-8">',
    '<meta http-equiv=content-type content="text/html;charset=iso-8859-1">',
    '<meta content="text/html; charset=iso-8859-1" http-equiv="Content-Type">',
    '<meta http-equiv="Content-Type">',
    '<meta http-equiv="Content-Type" content>',
    '<meta http-equiv="Content-Type" content="">',
    '<meta content="charset=x">',
    '<meta content="text/html;charset=X; q=1" http-equiv=refresh>',
    '<meta content="text/html;charset=X; q=1" http-equiv=content-type>',
    '<meta content="x" charset="y">',
    '<meta content="x" charset="">',
    '<meta content="charset=x" http-equiv="content-type">',
    '<meta content="CHARSET = z">',
    '<meta content="CHARSET=z" http-equiv="content-type">',
    '<meta content="charset = z" http-equiv="content-type">',
    '<meta content=" my ; charset = z ; q">',
    '<meta content="\ncharset=z">',
    '<meta content="a\ncharset=b">',
    '<meta content=";charset=z">',
    '<meta content="x;charset=z;charset=y">',
    '<meta content="charset=">',
    '<meta content="charset=; charset=y">',
    '<meta content="x" http-equiv="Content-Type" charset="y">',
    '<meta http-equiv="content-type" content="text/html; charset=x" charset="y">',
    '<p charset="x">y</p>',
    '<div charset="x" content="charset=y">z</div>',
    '<meta name="charset" content="charset=x">',
    '<meta http-equiv="refresh" content="0; url=http://x">',
    '<meta http-equiv="CONTENT-TYPE" content="charset=x">',
    '<meta http-equiv="Content-Type " content="charset=x">',
    '<meta content="charset=x" http-equiv="Content-Type" http-equiv="refresh">',
    '<meta content="charset=x" http-equiv="refresh" http-equiv="Content-Type">',
    '<meta charset="x"><meta charset="y">',
    '<meta><meta charset=a>',
    '<meta charset=x/>',
    '<meta charset=x />',
]

# Short strings without "<" that exercise the constructor's pre-check.
BOUNDARY_DOCS = [
    'http://x/\ud800',
    'https:\ud800',
    'http: \ud800',
    'x' * 255 + '\ud800',
    'x' * 256 + '\ud800',
    'x' * 256,
    'x' * 257,
    'x\ud800\n',
    '\ud800\n',
]

HAND_SEEDS = [
    '<!-- c -->', '<!---->', '<!-->', '<!--->', '<!--x--!>y', '<!--x', '<!--x--', '<!--x--!', '<!--x-', '<!-- a -- b -->',
    '<!DOCTYPE html>', '<!doctype html>', '<!DOCTYPE>', '<!DOCTYPE html', '<!DOCTYPEhtml>',
    '<!DocType  HTML PUBLIC "-//W3C//DTD HTML 4.01//EN">',
    '<![CDATA[x]]>', '<![CDATA[x]]>y', '<![CDATA[x', '<![CDATA[]]>', '<![cdata[x]]>', '<![CDATA[a]]b]]>',
    '<p><![CDATA[<b>&amp;]]></p>',
    '<!x>', '<!>', '<![if gte mso 9]>x<![endif]>', '<!ELEMENT br EMPTY>', '<!', '<!-', '<!x',
    '<?x?>', '<?xml version="1.0" encoding="utf-8"?>', '<?php echo 1 ?>', '<?', '<?x', '<?>',
    '<script>x</script>', '<script>a<b && c>d</script>', '<script>"</script>"</script>', '<SCRIPT>a</SCRIPT>',
    '<script>a</scripty>b</script >c',
    '<script>a</script x>b', '<script>a', '<script>a</scrip', '<script/>a</script>', '<script><!-- x --></script>',
    '<script>&amp;&#65;</script>',
    '<style>a>b{c:"</style>"}</style>', '<style>p{}</STYLE>x', '<xmp><b>x</b></xmp>', '<iframe><p>x</iframe>',
    '<noembed><p></noembed>', '<noframes>&amp;</noframes>',
    '<plaintext><p>x</plaintext>y', '<noscript><p>x</p></noscript>', '<textarea>a</textarea>',
    '<textarea> a\n <b>b</b>&amp;&#65;&foo; </textarea>x',
    '<textarea>a</textareax>b</textarea>', '<title>a &lt; b</title>', '<title><b>x</b></title>', '<TITLE>a</title>',
    '<textarea>a', '<title>&amp',
    '<template><p>x</p></template>', '<ruby>a<rt>b</rt><rp>(</rp></ruby>', '<pre>\n a\n  b\n</pre>',
    '<pre><textarea>\n x </textarea></pre>',
    'a < b', 'a <b', 'a<', 'a</', '<', '</', '</>', '</ p>', '</3>', '</p x="1">', '</p/>', '</p', '</p <', '<>',
    '< p>', '<1>', '<p/ >', '<p / >',
    'a &b', 'a & b', '&', '&;', '&amp', '&amp;', '&ampx', '&ampx;', '&amp=', '&foo;', '&foo', '&notit;',
    '&not;it', '&NotEqualTilde;', '&AMP;', '&Amp;',
    '&nbsp', '&nbsp;x', '&lt;p&gt;', '&apos;', '&quot;', '&#', '&#;', '&#x', '&#x;', '&#0;', '&#00065;', '&#65',
    '&#65x', '&#x41', '&#X41;', '&#x4g;',
    '&#128;', '&#129;', '&#130;', '&#150;', '&#159;', '&#160;', '&#255;', '&#256;', '&#x80;', '&#x81;', '&#x9f;',
    '&#xD800;', '&#xDFFF;', '&#xFFFE;',
    '&#x10FFFF;', '&#x110000;', '&#99999999999;', '&#xFFFFFFFFFFFF;', '&#9;', '&#10;', '&#13;', '&#1;', '&#127;',
    '&# 65;', '&#-65;', '&a.b-c;', '&a1',
    '<p class=x>', '<p class=x y=z>', '<p class="a  b\tc">', '<p class=\'a"b\'>', '<p a="1" a="2">',
    '<p A="1" a="2">', '<p a=1 a>', '<p a = "b">',
    '<p a="b"c="d">', '<p a="b" / c>', '<p a=">">x', '<p a=\'>\'>x', '<p a=>x', '<p a=b>c>d', '<p a="b>c',
    '<p a=\'b>c', '<p a= \'x>', '<p ="x">',
    '<p "a">', '<p \'a\'=b>', '<p a="&amp;&lt;&quot;&apos;&#65;&foo;&amp">', '<p a="&notit;&not;&copy=1&copy">',
    '<p a=&amp;b>', '<p a="\n b \n">',
    '<p a=""">', '<p a=\'\'\'>', '<p a="x" b=\'y\' c=z d>', '<a href="?a=1&b=2&copy=3&amp=4">x</a>', '<p a=b/>',
    '<p a=b />', '<p a/>', '<p/a>', '<p//>',
    '<p\na\n=\n"b"\n>', '<p\x0ba>', '<p\x00>', '<p a\x00b="c">', '<p a="\x00">', 'x\x00y', '\x00', '\x0b', '\x0c',
    '\x1c', '\x85', '\xa0', '\u2028', '\u3000',
    '\ud800', 'a\udfffb', '<p>\ud83d</p>', '<p a="\ud800">', '<\ud800>', '\U0001f600', '<p>\U0001f600</p>',
    '<P CLASS="x">Maiuscolo</P><BR>', '<D\u0130V>x</D\u0130V>', '<div>x</D\u0130V>', '<A\u03a3>x</a\u03c2>',
    '<a\u212a>x</ak>', '<SCR\u0130PT>x</SCR\u0130PT>', '<x-foo>y</x-foo>', '<o:p>x</o:p>',
    '<p.q>x</p.q>', '<p_q a_b="1">', '<a1b2>', '<\u00e9>', '<p\u00e9>x</p\u00e9>',
    '<p>a<p>b', '<p><div></p></div>', '<b><i>x</b>y</i>', '<ul><li>a<li>b</ul>', '<table><tr><td>a<td>b</table>',
    '</div>x</div>', '<a><b><c></a>d',
    '<br></br>', '<br/></br>', '</br>', '<br>x</br>y', '<br><br></br></br>', '<p></br></p>', '<img src=x></img>',
    '<hr/><hr></hr>', '<input><input/></input>',
    '<br a=1>x<br/ >', '<br/>x', '<img/src=x>', '<wbr>a<wbr/>b</wbr>',
    '<html><head><title>T</title></head><body>x</body></html>', '<body><p>x', '<p>  </p>', '<p> \n </p>', '<p>\n</p>',
    ' ', '\n', '\n\n', ' \n ', '\t',
    '\r\n', 'a\r\nb', '<p>a</p>\r\n<p>b</p>', '  a  ', '<b> a </b> <i> b </i>', '<p>\xa0</p>', '<p>\u2028a\u2028</p>',
    '<p> \x1c </p>', '<pre> </pre>',
    '<pre>\n</pre>', '<textarea>\n</textarea>', '<pre><b> a </b>\n<!-- c -->\n</pre>x', '<script> </script>',
    '<script>\n</script>', '<style>\n a \n</style>',
    '<p><!-- c --></p>', '<p>a<!-- c -->b</p>', '<!-- a --> <!-- b -->', '<!--  -->', '<!-- \n -->', '<![CDATA[ ]]>',
    '<?  ?>', '<!DOCTYPE  >',
    '<p>&lt;&gt;&amp;&quot;&apos;</p>', '<p>< > & " \'</p>', '<p a="<>&amp;">', '<p a="\'">', '<p a=\'"\'>',
    '<p a="&quot;\'">', '<p>&amp;amp;</p>',
    '<p>&#38;</p>', '<p>&#60;b&#62;</p>', 'http://example.com/x', 'a.txt', 'x' * 300, '<a' + ' b' * 50 + '>',
]

HAND_EXTRA = [
    # comments in more shapes
    '<!----->', '<!-- -- -->', '<!--a--b-->', '<!--a--->', '<!--\x00-->', '<!-->y<!-->', '<!--x--!-->', '<!---x-->',
    '<!--<p>-->', '<!-- -->y<!-- -->', '<p><!-- x --><!-- y --></p>', '<p>a<!---->b</p>',
    # doctypes
    '<!DOCTYPE svg>', '<!doctype HTML PUBLIC "x">', '<!DOCTYPE\nhtml>', '<!DOCTYPE html >', '<!DOCTYPE html><p>x</p>',
    '<!DOCTYPE html SYSTEM "about:legacy-compat">', '<!DOCTYPE html PUBLIC>', '<!DOCTYPE html [<!ENTITY x>]>',
    '<!doctypehtml>', '<!DOCTYPEhtml><p>x', '<!DOCTYPE html><!--x-->',
    # CDATA
    '<![CDATA[a]]]>', '<![CDATA[&amp;]]>', '<![CDATA[\x00]]>', '<![cdata[x', '<![CDATA[x]]', '<p><![CDATA[x]]></p>',
    '<![CDATA[a]]><![CDATA[b]]>', '<![CDATA[<p>x</p>]]>', '<![CDATA[ ]] ]]>', '<![CDATA[<>&amp;]]>',
    # bogus declarations
    '<![if]>', '<![endif]>', '<!->', '<!---->x<!>', '<![if !IE]><p>x</p><![endif]>', '<!ELEMENT>', '<![CDATAx]>',
    # processing instructions
    '<?x y z?>', '<?xml?>', '<?x?>y<?z?>', '<?x', '<? x ?>', '<?x>', '<?>x', '<??>', '<?xml version="1.0"?><p>x</p>',
    # raw text elements
    '<script></script>', '<script>\n</script>', '<script>a</script >', '<script>a</script\t>b',
    '<script>a</script/>b', '<script>a</SCRIPT>b</script>', '<script>if (a < b && c > d) {}</script>',
    '<script src=x></script>', '<script>a\ud800b</script>',
    '<style>a</style >x', '<style>a</STYLE >', '<style>p > a { color: red }</style>', '<style></style>',
    '<style>a\udfffb</style>',
    '<xmp>a</xmp>b</xmp>', '<xmp/>a</xmp>', '<iframe>a</iframe >b', '<iframe src=x></iframe>', '<noembed></noembed>',
    '<noframes>a', '<noframes>a</noframes>b', '<plaintext>a\nb</plaintext>', '<plaintext>', '<plaintext>a<b>c</b>',
    '<noscript>a</noscript>b', '<noscript/>a</noscript>', '<noscript></noscript>',
    # rcdata elements
    '<textarea></textarea>', '<textarea>a&amp;b</textarea>', '<textarea>a</TEXTAREA>b', '<title>a</title>b</title>',
    '<title></title>', '<title>a</titlex>b</title>', '<title>a</title >b', '<textarea>a</textarea >b',
    '<title>&amp;&#65;&foo;</title>', '<textarea>\n\n</textarea>',
    # pre
    '<pre>a</pre>', '<pre>\n\n</pre>', '<pre><code>\n x\n</code></pre>', '<pre><script>\n</script></pre>',
    '<pre><template>\n</template></pre>', '<pre> x <b> y </b> </pre>', '<pre><pre>x</pre></pre>',
    # template
    '<template>a</template>', '<template><template>x</template></template>', '<template><rt>x</rt></template>',
    '<template><!--c-->x</template>', '<template/>x</template>', '<template><b>x</b>y</template>',
    '<template>\nx\n</template>', '<TEMPLATE>x</template>', '<template><script>a</script>b</template>',
    # ruby: rt and rp text
    '<ruby><rt>a</rt><rt>b</rt><rt>c</rt></ruby>', '<ruby><rt>a</rt><rp>(</rp><rt>b</rt><rp>)</rp></ruby>',
    '<ruby><rp>(</rp><rp>)</rp></ruby>', '<rt>a</rt><rp>b</rp>', '<ruby>x<rt>y</rt></ruby>',
    '<ruby><rt>\u304b</rt><rp>\uff08</rp></ruby>', '<ruby><rt>a<b>c</b></rt></ruby>', '<ruby><rt></rt></ruby>',
    '<RUBY><RT>x</RT></RUBY>', '<ruby><rt>a</rt></ruby><!--r--><ruby><rt>b</rt></ruby>',
    # unknown and unusual tag names
    '<x-foo a=1>x</x-foo>', '<o:p>x</o:p>y</o:p>', '<a1b2>3</a1b2>', '<p.q.x>y</p.q.x>', '<p_q a_b="1">x</p_q>',
    '<\u00e9>a</\u00e9>', '<p\u00e9>x</P\u00c9>', '<x:y>z</x:y>', '<_x>a</_x>', '<-x>a</-x>', '<x y>z</x>',
    # closing-tag garbage
    '</p x=1>', '</ p>', '</p/>', '</>', '</3>', '</p\n>', '</p\n', '</p x', '</P>', '</p >x', '<p>a</p x=1>b',
    '<p>a</ >b', '<p>a< /p>b', '<p>a</p', '<p>a<//p>b', '</ p >x', '</\ud800>',
    # attributes
    '<p =x>', '<p ==>', '<p "a"=b>', '<p \'a\'>', '<p a=/>', '<p a=>', '<p a b c>', '<p CLASS="a" class="b">',
    '<p class="a\tb\nc">', '<p class="">', '<p class="  ">', '<p a="&amp" b="&#x41" c="&notit;">',
    '<p a="\r\n">', '<p a\x00b>', '<p a = >', '<a href="x">', '<a href=x rel="a b" rev="c">',
    '<img src="x" alt="a>b">', '<input checked>', '<p a=&amp;>', '<p a="&#x80;">',
    # entities
    '&lt;&gt;&amp;&quot;&apos;', '&LT;', '&Lt;', '&nbsp&nbsp;', '&#x1f600;', '&#128512;', '&#X1F600;',
    '&notin;', '&notin', '&noti', '&sube;', '&sup;', '&frac12;', '&frac12', '&hellipx;', '&hellip;x',
    '&#x0;', '&#0', '&#x0000;', '&#x00;', '&#x1;', '&#X1;', '&#1114111;', '&#1114112;', '&#-1;', '&#x-1;',
    '&#x1g;', '&#xg;', '&#  ;', '&#;x', '&;x', '& x', '&x y', '&&amp;', '&amp;;', '&amp;amp;amp;',
    # white space of every kind
    '\f', '\x0b', '\x1c\x1d\x1e\x1f', '\x85', '\u2028', '\u3000', '\u200b', '\ufeff', '\xa0\xa0',
    'a\tb\nc\rd\fe', '<p>a\u2028b</p>', '<p>\u3000</p>', '<p>\x85</p>', '<p>\x00</p>', '<p>\x0c</p>',
    '<p>\u200b</p>', '<p>\ufeff</p>', '<p>\x1f</p>',
    # surrogate and non-BMP characters
    '\udfff', 'a\ud800b', '<p>a\ud800b</p>', '<p a="\udfff">x</p>', '<b>\ud800\udfff</b>',
    '\U0001f600\U0001f600', '<p>\U0001f389</p>', '<p>\U0001f600\U00010000</p>', '\U00010000',
    # upper and mixed case tag names
    '<DIV CLASS="x">y</DIV>', '<DiV>y</dIv>', '<\u0130>x</\u0130>', '<\u03a3>x</\u03a3>', '<\u00c4>x</\u00e4>',
    '<B><I>x</b></i>', '<BR/>',
    # structure and misnesting
    '<p>a<div>b</div>c</p>', '<b>a<i>b</i>c</b>', '<ul><li>a<li>b<li>c</ul>', '<dl><dt>a<dd>b</dl>',
    '<p>a</p></p>', '<div><p>a</div>b', '<a href=x><a href=y>z</a></a>', '<table><td>a</table>',
    # void elements
    '<br/>', '<br />', '<br\t/>', '<hr>', '<hr/>', '<img src=x/>', '<input/>', '<wbr/>', '<col/>', '<area>',
    '<br></br></br>', '<p>a<br>b<br/>c</p>', '<br/></p>', '<br / >', '<br a="1"/>',
    # empty and white-space-only documents
    '<p></p>', '<p> </p>', '<div>\n</div>', '<span>\t</span>', '<body></body>', '<html></html>',
    # incomplete constructs at the very end
    '<p a="', '<p a=', '<p ', '<p/', '<!-', '<![CDATA[', '<!DOCTYPE', '<?', '&a', '&#x', '</p', '<script>a</scrip',
]

HAND_LONG = [
    '<div>' * 200 + 'x',
    '<br>' * 300,
    '</p>' * 100,
    '&#' + '1' * 5000 + ';',
    '&#x' + '1' * 5000 + ';',
    '<p ' + 'a=1 ' * 200 + '>',
    '<!--' * 50,
    '<' * 200,
    '&' * 200,
    '<b>' * 50 + '</b>' * 60,
    '<pre>' + '<p> x </p>\n' * 30 + '</pre>',
    '<div>' * 100 + 'x' + '</div>' * 100,
    '<p>' * 60 + 'y',
    '<span>a</span>' * 60,
    'x' * 500,
    '<p>' + 'a' * 500 + '</p>',
    '<p a="' + 'b' * 500 + '">x</p>',
    '&#x1F600;' * 80,
    '&amp;' * 100,
    '<script>' + 'a' * 400 + '</script>',
    '<style>' + 'a{}' * 150 + '</style>',
    '<textarea>' + 'a' * 400 + '</textarea>',
    '<template>' + 'a' * 400 + '</template>',
    '<rt>' + 'a' * 400 + '</rt>',
    '<rp>' + 'a' * 400 + '</rp>',
    '<ruby>' + '<rt>a</rt>' * 30 + '</ruby>',
    '<o:p>' * 40 + 'x',
    '<![CDATA[' + 'x' * 400 + ']]>',
    '<!--' + 'x' * 400 + '-->',
    '<?' + 'x' * 400 + '?>',
    '<!DOCTYPE ' + 'x' * 400 + '>',
    '<p ' + 'a=1 ' * 100 + 'b="' + 'c' * 100 + '">x</p>',
    '&#' + '1' * 4299 + ';',
    '&#' + '1' * 4300 + ';',
    '&#' + '1' * 4301 + ';',
    '&#' + '0' * 4298 + '65;',
    '&#' + '0' * 4299 + '65;',
    '&#' + '0' * 4300 + '65;',
    '&#' + '0' * 4301 + '65;',
    '&#' + '0' * 4300 + ';',
    '&#' + '0' * 4301 + ';',
    '&#x' + '0' * 5000 + '41;',
    '&#x' + '0' * 5000 + '110000;',
]

HAND_PICKED = list(dict.fromkeys(HAND_SEEDS + HAND_EXTRA + HAND_LONG + META_DOCS + BOUNDARY_DOCS))

# Fragments for the malformed token soup: every construct category, weighted by
# repetition.
MALFORMED = (
    ['<!--', '-->', '<!--x-->', '<!-->', '<!--->', '<!--x--!>', '<!-- a -- b -->', '<!--', '-->'] * 2
    + ['<!DOCTYPE html>', '<!doctype', '<!DOCTYPE>', '<!x>', '<!>', '<![if x]>', '<![endif]>', '<!-'] * 2
    + ['<![CDATA[x]]>', '<![CDATA[', '<![cdata[', ']]>', '<![if gte mso 9]>'] * 2
    + ['<?x?>', '<?xml version="1.0"?>', '<?', '?>', '<?php ?>'] * 2
    + ['<script>', '</script>', '<script>a<b>c</script>', '<style>', '</style>', '<style>a{}</style>'] * 2
    + ['<textarea>', '</textarea>', '<title>', '</title>', '<xmp>', '</xmp>', '<iframe>', '</iframe>'] * 2
    + ['<noembed>', '</noembed>', '<noframes>', '</noframes>', '<plaintext>', '<noscript>', '</noscript>'] * 2
    + ['<pre>', '</pre>', '<template>', '</template>', '<rt>', '</rt>', '<rp>', '</rp>', '<ruby>', '</ruby>'] * 2
    + ['<p>', '</p>', '<p', '<p/', '<p a=1>', '<p a="1">', "<p a='1'>", '<p a=1 a=2>', '</p x=1>', '</ p>', '</p/>']
    * 2
    + ['<br>', '<br/>', '<br />', '</br>', '<img src=x>', '<img/src=x>', '<input>', '<hr>', '<wbr/>', '<br a=1>']
    * 2
    + ['<', '>', '</', '<>', '</>', '</3>', '< p>', '<1>', '<a', '>']
    + ['&', '&;', '&amp', '&amp;', '&ampx', '&ampx;', '&amp=', '&foo;', '&notit;', '&NotEqualTilde;', '&amp;']
    + ['&#', '&#;', '&#x', '&#x;', '&#0;', '&#65', '&#x41', '&#X41;', '&#128;', '&#x9f;', '&#xD800;', '&#x110000;',
       '&#99999999999;', '&#xFFFFFFFFFFFF;', '&# 65;', '&#-65;'] * 2
    + [' ', '\t', '\n', '\r', '\f', '\x0b', '\x1c', '\x85', '\xa0', '\u2028', '\u3000', '\u200b', '\ufeff'] * 2
    + ['\x00', '\x01', '\x7f', '\ud800', '\udfff', '\U0001f600', '\u4e2d\u6587', '\u00e9'] * 2
    + ['a', 'b', 'x', 'P', 'BR', 'DIV', 'x-foo', 'o:p', 'p.q', '\u0130', '\u03a3', '\u212a'] * 2
    + ['http://x/y', 'a.txt', '--', '/>', '"', "'", '=', 'x=1']
)

# Chars the mutator may insert.
HOSTILE_CHARS = '<>&/"\'= ;#!-?[]:\x00\x0b\x0c\x1c\x85\xa0\u2028\u3000\ud800\udfff\U0001f600aApPbB'

# All tag names random_structured draws from.
NORMAL_TAGS = TAGS
RAW_TAGS = 'script style xmp iframe noembed noframes plaintext'.split()
RCDATA_TAGS = 'textarea title'.split()
SPECIAL_TAGS = ['pre', 'template', 'rt', 'rp', 'noscript']
CUSTOM_TAGS = ['x-foo', 'o:p', 'a1b2', 'p.q', 'p_q', '\u00e9', 'p\u00e9', 'D\u0130V', 'A\u03a3', 'a\u212a', 'x:y',
               'x', 'my-tag']
# Weighted so the less common tag kinds show up often enough in the corpus.
TAG_CHOICES = NORMAL_TAGS * 2 + RAW_TAGS * 2 + RCDATA_TAGS * 2 + SPECIAL_TAGS * 3 + CUSTOM_TAGS * 4

ATTR_NAMES = ['class', 'style', 'href', 'rel', 'title', 'data-id', 'id', 'Alt', 'A', 'checked', 'a_b', 'a.b', 'x:y',
              'CLASS', 'class', 'a', 'a', 'b', '=', '"', "'", 'a\x00b']
ATTR_VALUES = ['', 'x', 'a b', 'a  b\tc', '>', '<', '&amp;', '&amp', '&amp=', '&#65;', '&#x41', '&#x80;', '&notit;',
               '&NotEqualTilde;', '&foo', "'", '"', 'a=b', '\n', '\r\n', ' ', '\x00', '\ud800', '\U0001f600',
               'a' * 30]
ATTR_SHAPES = ['%(n)s', '%(n)s=%(v)s', '%(n)s="%(v)s"', "%(n)s='%(v)s'", '%(n)s = "%(v)s"',
               '%(n)s="%(v)s"%(n)s="y"', '%(n)s="%(v)s"c="d"', '%(n)s="%(v)s', '%(n)s=%(v)s%(n)s=%(v)s',
               '=%(v)s', '"%(v)s"', "'%(v)s'", '%(n)s="%(v)s" /', '%(n)s=/%(v)s', '%(n)s %(n)s']

TEXT_BITS = ['a', 'b', 'x y', ' ', '\t', '\n', '\r', '\f', '\x0b', '\x1c', '\x85', '\xa0', '\u2028', '\u3000', '\u200b',
             '\x00', '\ud800', '\udfff', '\U0001f600', '\u4e2d\u6587', '\u03a9', '\u00e9', '<', '>', '&', '&#',
             '&#x', '&#X', '&amp;', '&amp', '&ampx', '&amp=', '&foo;', '&NotEqualTilde;', '&notit;', '&copy=1', '&#0;',
             '&#128;', '&#x80;', '&#X41;', '&#xD800;', '&#x110000;', '&#99999999999;', '&#xFFFFFFFFFFFF;', '&#65',
             '&#x41', '&# 65;', '&#-65;', '&#65a', '&lt;', '"', "'", '=', '/', '--', '</', '/>', '<!--', '-->', '<?',
             '?>', '<!', '<![CDATA[', ']]>', '</p>', '<p>', '</br>', '<br>']

CLOSERS = ['</p>', '</ p>', '</p >', '</p x=1>', '</p/>', '</>', '</3>', '</p', '</P>', '</br>', '</div>',
           '</script>', '</style>', '</textarea>', '</title>', '</template>', '</rt>', '</rp>', '</x-foo>', '</o:p>',
           '</\ud800>']

BLOCKS = ['<!--x-->', '<!-->', '<!--->', '<!--x--!>', '<!-- x -- y -->', '<!--', '<![CDATA[x]]>', '<![CDATA[',
          '<![cdata[x]]>', '<!DOCTYPE html>', '<!doctype html>', '<!DOCTYPE', '<!x>', '<!>', '<![if x]>', '<?x?>',
          '<?xml version="1.0"?>', '<?', '<script>a</script>', '<style>p{}</style>', '<textarea>t</textarea>',
          '<template>x</template>', '<pre>\n x\n</pre>']


def random_hostile_text(rng):
    return ''.join(rng.choice(TEXT_BITS) for _ in range(rng.randint(0, 4)))


META_ATTRS = [
    'charset="iso-8859-1"', 'charset=latin1', 'charset', 'charset=""', 'charset="a" charset="b"',
    'http-equiv="Content-Type" content="text/html; charset=iso-8859-1"',
    'http-equiv="content-type" content="text/html;charset=X; q=1"',
    'content="charset=x"', 'content="x" charset="y"', 'content="CHARSET = z"',
    'content="text/html;charset=X; q=1" http-equiv=refresh',
    'content="a\ncharset=b" http-equiv="Content-Type"',
    'charset="x" content="charset=y" http-equiv="content-type"',
]


def random_meta(rng):
    return '<%s%s%s%s>' % (rng.choice(['meta', 'META', 'Meta']),
                           rng.choice(['', ' ', '\n']),
                           rng.choice(META_ATTRS),
                           rng.choice(['', '/', ' /']))


def random_full_attributes(rng):
    attrs = []
    for _ in range(rng.randint(0, 3)):
        shape = rng.choice(ATTR_SHAPES)
        piece = shape % {'n': rng.choice(ATTR_NAMES), 'v': rng.choice(ATTR_VALUES)}
        attrs.append(rng.choice([' ', '  ', '\n', '\t', ' \r\n ']) + piece)
    return ''.join(attrs)


def random_structured(rng, depth=0):
    """Nested document drawing from every tag kind and quoting style."""
    out = []
    length = 0
    for _ in range(rng.randint(1, 4 if depth < 2 else 2)):
        choice = rng.random()
        if choice < 0.18:
            part = random_hostile_text(rng)
        elif choice < 0.3:
            tag = rng.choice(VOIDS)
            name = rng.choice([tag, tag, tag.upper()])
            part = '<%s%s%s>' % (name, random_full_attributes(rng), rng.choice(['', '/', ' /']))
        elif choice < 0.52:
            tag = rng.choice(TAG_CHOICES)
            name = rng.choice([tag, tag, tag.upper(), tag.capitalize()])
            part = '<%s%s%s>' % (name, random_full_attributes(rng), rng.choice(['', '', ' ']))
            part += random_structured(rng, depth + 1)
            if rng.random() < 0.8:
                part += rng.choice(['</%s>', '</%s >', '</%s\n>', '</ %s>', '</%s/>']) % rng.choice([tag, name])
        elif choice < 0.6:
            part = rng.choice(CLOSERS)
        elif choice < 0.7:
            part = rng.choice(BLOCKS)
        elif choice < 0.78:
            part = rng.choice(['<script>', '<style>', '<textarea>', '<title>', '<xmp>', '<iframe>', '<plaintext>'])
            part += random_hostile_text(rng) + rng.choice(['</script>', '</style>', '</textarea>', '</title>',
                                                           '</xmp>', '</iframe>', ''])
        elif choice < 0.86:
            part = rng.choice(['<template>', '<rt>', '<rp>', '<ruby>', '<pre>'])
            part += random_hostile_text(rng) + rng.choice(['</template>', '</rt>', '</rp>', '</ruby>', '</pre>', ''])
        elif choice < 0.93:
            part = rng.choice(['<p>', '<div>', '<b>', '<span>', '<li>'])
            part += random_hostile_text(rng) + rng.choice(CLOSERS)
        elif choice < 0.97:
            part = random_meta(rng)
        else:
            part = rng.choice(['<p a="', '<p a=', '<', '</', '&', '&#', '&a', '<!-', '<!--', '<?', '<![CDATA[',
                               '<p/', '<!'])
        out.append(part)
        length += len(part)
        if length > 280:
            break
    return ''.join(out)[:400]


def random_malformed(rng):
    """Token soup from the weighted fragment alphabet."""
    return ''.join(rng.choices(MALFORMED, k=rng.randint(1, 30)))[:400]


MUTATION_BASE = [doc for doc in HAND_PICKED + EDITOR_DOCS if 0 < len(doc) <= 120]


def _mutate(rng, doc):
    edit = rng.randrange(4)
    if edit == 0 and doc:
        at = rng.randrange(len(doc))
        return doc[:at] + doc[at + 1:]
    if edit == 1:
        at = rng.randrange(len(doc) + 1)
        return doc[:at] + rng.choice(HOSTILE_CHARS) + doc[at:]
    if edit == 2 and doc:
        start = rng.randrange(len(doc))
        end = rng.randrange(start, min(len(doc), start + 30) + 1)
        at = rng.randrange(len(doc) + 1)
        return doc[:at] + doc[start:end] + doc[at:]
    if doc:
        return doc[:rng.randrange(len(doc) + 1)]
    return doc


def random_mutated(rng):
    """A seeded document with 1..3 random edits."""
    choice = rng.random()
    if choice < 0.5:
        doc = rng.choice(MUTATION_BASE)
    elif choice < 0.75:
        doc = random_structured(rng)
    else:
        doc = random_document(rng)
    for _ in range(rng.randint(1, 3)):
        doc = _mutate(rng, doc)
    return doc[:400]


def full_corpus(seed=20261111, scale=1):
    rng = random.Random(seed)
    cases = [full_case('hand', doc) for doc in HAND_PICKED]
    for kind, generate in (('malformed', random_malformed), ('structured', random_structured),
                           ('mutated', random_mutated)):
        for _ in range(1200 * scale):
            cases.append(full_case(kind, generate(rng)))
    return cases


def write_full(path, cases):
    lines = [json.dumps(case, ensure_ascii=True, separators=(',', ':')) for case in cases]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[\n' + ',\n'.join(lines) + '\n]\n')


def full_stats(cases):
    kinds = {}
    strings = {}
    raises = 0
    for case in cases:
        if case['error']:
            raises += 1
        for event in case['events']:
            kinds[event[0]] = kinds.get(event[0], 0) + 1
        stack = list(case['tree'] or [])
        while stack:
            node = stack.pop()
            if node[0] == 'tag':
                stack.extend(node[3])
            else:
                strings[node[1]] = strings.get(node[1], 0) + 1
    return raises, kinds, strings


def print_full_stats(cases, path):
    raises, kinds, strings = full_stats(cases)
    print('cases=%d raises=%d file=%s bytes=%d' % (len(cases), raises, path, path.stat().st_size))
    print('events: ' + ' '.join('%s=%d' % (name, kinds[name]) for name in sorted(kinds)))
    print('strings: ' + ' '.join('%s=%d' % (name, strings[name]) for name in sorted(strings)))


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
    if '--write-full' in sys.argv:
        cases = full_corpus(20261111, 1)
        write_full(FULL, cases)
        print(f'wrote {FULL.relative_to(ROOT)}')
        print_full_stats(cases, FULL)
        return
    if '--full-out' in sys.argv:
        target = Path(sys.argv[sys.argv.index('--full-out') + 1])
        seed = int(sys.argv[sys.argv.index('--seed') + 1]) if '--seed' in sys.argv else 20261111
        scale = int(sys.argv[sys.argv.index('--scale') + 1]) if '--scale' in sys.argv else 1
        cases = full_corpus(seed, scale)
        write_full(target, cases)
        print(f'wrote {target}')
        print_full_stats(cases, target)
        return
    seed = int(sys.argv[sys.argv.index('--seed') + 1]) if '--seed' in sys.argv else 20261109
    scale = int(sys.argv[sys.argv.index('--scale') + 1]) if '--scale' in sys.argv else 1
    cases = corpus(seed, 6000 * scale, 4000 * scale) + full_corpus(seed, scale)
    with tempfile.TemporaryDirectory(prefix='az-bs4-corpus-') as directory:
        path = Path(directory) / 'corpus.json'
        path.write_text(json.dumps(enc(cases)))
        env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
        subprocess.run(['go', 'test', '-count=1', '-tags=bs4integration', PACKAGE, '-run', '^TestPrettifyAgainstBS4$', '-v'],
                       cwd=ROOT / 'BE_GO', env={**env, 'ASSOZETA_BS4_CORPUS': str(path)}, check=True, timeout=300)
    print(json.dumps(dict(cases=len(cases), subset=sum(bool(c.get('subset')) for c in cases))))


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
    main()
