"""Write the paragraph parser data of BE_GO/internal/pdfexport (pdf-exports.md).

Two files come from reportlab 4.2.5 itself:

- ``entities_gen.go``: the entity names ``ParaParser`` knows;
- ``testdata/paragraphs.json``: what ``Paragraph(text, style)`` makes of a text,
  the fragments (font, text, line break), an exception, or fragments the Go
  writer does not lay out (``other``).

Run with the project interpreter: ``python -I TESTS/fixtures/generate_pdf_paragraphs.py``
(``--check`` compares instead of writing).
"""
import json
import random
import shutil
import subprocess
import sys
from pathlib import Path

from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph
from reportlab.platypus.paraparser import known_entities

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / 'BE_GO/internal/pdfexport'
REFUSED = ('a', 'bullet', 'font', 'greek', 'img', 'index', 'link', 'nobr', 'ondraw', 'seq', 'seqchain', 'seqdefault', 'seqformat',
           'seqreset', 'span', 'strike', 'sub', 'sup', 'super', 'u', 'unichar')
FONTS = ('Helvetica', 'Helvetica-Bold', 'Helvetica-Oblique', 'Helvetica-BoldOblique')

TEXTS = [
    '', 'ASD Aurora', 'Sport & Fun', 'Sport &amp; Fun', 'A < B', 'A <b>B</b>', 'A > B', '&', '<', '&amp;', 'a&b', 'a &b c', 'a&b;c',
    'R&D', 'AT&T Italia', 'Yoga &amp; Pilates &lt;adulti&gt;', '&lt;b&gt;', 'a<br/>b', 'a<br>b', 'a<br></br>b', 'a</br>b', '<br/>',
    '<b>grassetto</b>', '<i>corsivo</i> e <b><i>entrambi</i></b>', '<strong>a</strong><em>b</em>', '<b>a', 'a</b>', '<b><i>a</b></i>',
    '<para>a</para>', '<para>a', 'a</para>b', '<para align="center">a</para>', '< para>a</para>', '<para/>', '<para >a</para>',
    '<h1>Titolo</h1>', '<div class="x">a</div>', '<unknown>a</unknown>', '<x', '<x y="1', 'a <3 b', '1<2 e 3>2', 'a<=b', '<>', '</>',
    '<!-- nota -->a', '<!--a', '<!-->a', '<!--->a', '<!-- a --!>b', '<?xml version="1.0"?>a', '<!DOCTYPE html>a', '<![CDATA[a<b]]>c',
    '<![CDATA[a', '<!x>a', '</ x>a', '</3>a', '<title>a&amp;b</title>c', '<title>a', '<script>a<b>c</script>d', '<style>a</style',
    '<textarea><b>a</b></textarea>b', '<plaintext>a<b>', '<xmp>&amp;</xmp>', '&#65;', '&#x41;', '&#X41;', '&#65', '&#65 b', '&#;',
    '&#x;', '&#xZ;', '&#12a;', '&#1114111;', '&#1114112;', '&#x110000;', '&#0;', '&#55296;', '&#9;a', '&#32;a', 'a&#32;', '&#955;',
    '&alpha;&beta;', '&Alpha;', '&AMP;', '&amp', '&amp b', '&nbsp;', 'a&nbsp;b', '&foo;', '&foo', '&foo bar', '&a.b;', '&a-b c', '&&',
    '&;', '& ;', '&#', '&#x', '&# 1', 'poli\xadsportiva', 'a\xad', '\xad', '<b>a</b >', '<b >a</b>', '<B>a</B>', '<b/>a', '<br />',
    '<BR/>a', '<br/><br/>', '<br><b></b></br>', '<br><br/></br>', '<b x>a</b>', '<b x=1>a</b>', '<b x="1" y=\'2\'>a</b>',
    '<b x="1>a</b>', '<b x= "1>a</b>', '<b x=\'>\'>a</b>', '<b x=>a</b>', '<b =>a</b>', '<b "x">a</b>', '<b/ >a</b>', '<b / x>a</b>',
    '<u>a</u>', '<a href="x">a</a>', '<font size="12">a</font>', '<font color="red">a</font>', '<sup>2</sup>', '<sub>2</sub>',
    '<strike>a</strike>', '<span>a</span>', '<greek>a</greek>', '<img src="x"/>', '<bullet>a</bullet>b', '<seq/>', '<nobr>a b</nobr>',
    '<unichar code="65"/>', '<link href="x">a</link>', '<super>a</super>', '<onDraw name="x"/>', '<index item="a"/>',
    'a\nb', '  a   b  ', 'a\tb', 'a\xa0b', '<b>a</b> <i>b</i>', '<b>a </b>b', 'a<b> b</b>', '<b> </b>', 'x &#x1F600; y', 'è &egrave; €',
    '<em>a<strong>b</strong></em>c', '<i><i>a</i></i>', '<b>a<br/>b</b>', 'a &lt; b &gt; c &quot;d&quot; &apos;e&apos;',
]
TOKENS = [
    'a', 'b c', ' ', 'Città', '&', '&amp;', '&lt;', '&gt;', '&nbsp;', '&#65;', '&#x41;', '&#X41;', '&#;', '&#12a', '&foo;', '&alpha;',
    '&amp', '<', '>', '<b>', '</b>', '<i>', '</i>', '<br/>', '<br>', '</br>', '<strong>', '</strong>', '<em>', '</em>', '<x>', '</x>',
    '<x y="1">', "<x y='>'>", '<x y="', '<!-- c -->', '<!--', '<?pi?>', '<!DOCTYPE x>', '<![CDATA[x]]>', '<para>', '</para>',
    '<title>', '</title>', '<script>', '</script>', '<b/>', '</>', '< b>', '<b >', '<B>', '</B >', '<a href="x">', '</a>', '<u>',
    '</u>', '<font size="12">', '\xad', 'é', '"', "'", '=', '/', '<x/>', '<x a=b c>', '<3', '&&', '&;', '&#', '&#x;', '-', '.', ';',
    '<plaintext>', '<textarea>', '</textarea>', '<b', '</b', '<x "a">', "<x 'a>", '\n', '\t', '\xa0', '<br />', '<BR/>', '&AMP;',
    '&Alpha;', '&#0;', '&#55296;', '&#1114112;', '&#955;', 'x= "y', '<x y= "z', '<x/y>', '<x//>', '<!>', '<!->', '-->', ']]>',
]


def refusable(text):
    lowered = text.lower()
    return any('<' + tag in lowered or '</' + tag in lowered for tag in REFUSED) or '<para' in lowered


def outcome(text, font):
    styles = getSampleStyleSheet()
    style = ParagraphStyle('Body', parent=styles['Normal'], fontName=font, fontSize=9, leading=12)
    entry = {'text': text, 'font': font}
    if refusable(text):
        entry['refusable'] = True
    try:
        paragraph = Paragraph(text, style)
    except Exception:
        entry['error'] = True
        return entry
    other = paragraph.style is not style or bool(paragraph.bulletText)
    frags = []
    for frag in paragraph.frags:
        if (frag.rise or frag.us_lines or frag.link or frag.greek or hasattr(frag, 'cbDefn') or hasattr(frag, 'backColor')
                or hasattr(frag, 'nobr') or frag.fontSize != style.fontSize or frag.textColor != style.textColor
                or frag.fontName not in FONTS):
            other = True
        frags.append([frag.fontName, frag.text, bool(getattr(frag, 'lineBreak', False))])
    if other:
        entry['other'] = True
    else:
        entry['frags'] = frags
    return entry


def go_string(text):
    out = []
    for char in text:
        code = ord(char)
        if char in '"\\':
            out.append('\\' + char)
        elif 32 <= code < 127:
            out.append(char)
        elif code > 0xFFFF:
            out.append('\\U%08x' % code)
        else:
            out.append('\\u%04x' % code)
    return '"' + ''.join(out) + '"'


def entities():
    lines = ['// Code generated from reportlab 4.2.5 (paraparser.known_entities); DO NOT EDIT.', '', 'package pdfexport', '',
             '// paraEntities are the entity names ParaParser replaces.', 'var paraEntities = map[string]string{']
    for name in sorted(known_entities):
        lines.append('\t%s: %s,' % (go_string(name), go_string(known_entities[name])))
    gofmt = shutil.which('gofmt')
    if not gofmt:
        raise SystemExit('gofmt is needed to write entities_gen.go')
    return subprocess.run([gofmt], input='\n'.join(lines + ['}', '']), capture_output=True, text=True, check=True).stdout


def corpus():
    rng = random.Random(20261110)
    texts = list(TEXTS)
    for _ in range(2600):
        texts.append(''.join(rng.choice(TOKENS) for _ in range(rng.randint(1, 7))))
    cases = [outcome(text, FONTS[i % 2] if i % 7 else FONTS[i % 4]) for i, text in enumerate(texts)]
    if not cases or not any('frags' in case for case in cases) or not any('error' in case for case in cases):
        raise SystemExit('the corpus is empty or one-sided')
    return json.dumps({'cases': cases}, ensure_ascii=True, separators=(',', ':')) + '\n'


def main():
    outputs = {PACKAGE / 'entities_gen.go': entities(), PACKAGE / 'testdata/paragraphs.json': corpus()}
    if '--check' in sys.argv[1:]:
        stale = [str(path) for path, text in outputs.items() if not path.is_file() or path.read_text() != text]
        if stale:
            raise SystemExit('stale: ' + ', '.join(stale))
        print('up to date')
        return
    for path, text in outputs.items():
        path.write_text(text)
    print(len(known_entities), 'entities,', text.count('"text"'), 'paragraphs')


if __name__ == '__main__':
    main()
