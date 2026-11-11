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
import math
import random
import shutil
import subprocess
import sys
from pathlib import Path

from reportlab.lib.colors import Color
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph
from reportlab.platypus.paraparser import known_entities

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / 'BE_GO/internal/pdfexport'
REFUSED = ('bullet', 'greek', 'img', 'index', 'ondraw', 'seq', 'seqchain', 'seqdefault', 'seqformat', 'seqreset', 'unichar')
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
    '<font size="12">a</font>', '<font size="+2">a</font>', '<font size="-2">a</font>', '<font size="2mm">a</font>', '<font size=" 12 ">a</font>',
    '<font color="red">a</font>', '<font color="#00ff00">a b</font>', '<font fg="blue">a</font>', '<font textcolor=red>a</font>',
    '<font fontsize=12>a</font>', '<font name="Helvetica-Bold">a</font>', '<font face="helvetica-oblique">a</font>',
    '<font name="Symbol">abc</font>', '<font name="ZapfDingbats">abc</font>', '<font name="Courier">a</font>', '<font name="Times-Roman">a</font>',
    '<font name="Arial">a</font>', '<font name>a</font>', '<font size>a</font>', '<font size="x">a</font>', '<font size="">a</font>',
    '<font color="nope">a</font>', '<font backcolor="yellow">a</font>', '<font bgcolor="#ffff00">a b c</font>', '<font style="x">a</font>',
    '<font foo="1">a</font>', '<font size="12" color="red" name="Helvetica-BoldOblique">a b</font> c', '<b><font name="Helvetica">a</font></b>',
    '<i><font name="Helvetica-Bold">a</font></i>', '<span>a</span>', '<span size="11" color="red">a</span>', '<span style="x">a</span>',
    '<nobr>a b</nobr>', '<nobr x="1">a</nobr>', '<nobr>a</nobr> <nobr>b</nobr>', '<sup>2</sup>', 'm<sup>2</sup>', 'x<sub>i</sub>',
    '<sup rise="3">a</sup>', '<sup size="5">a</sup>', '<sup rise="50%">a</sup>', '<sup size="50%">a</sup>', '<sub rise="+1" size="-1">a</sub>',
    '<sup rise="x">a</sup>', '<sup rise>a</sup>', '<sup foo="1">a</sup>', '<super>a</super>', '<sup>a</super>', '<sup><sub>a</sub></sup>',
    '<sup rise="2"><sub>a</sub></sup>', '<sup rise="+10%">a</sup>', '<sup size="2in">a</sup>', '<sup rise="1e2">a</sup>',
    '<font size="0">a</font>', '<font size="1&#50;">a</font>', '<font size="inf">a</font>', '<font color="red" color="blue">a</font>',
    '<font size="12">a</font><font size="12">b</font>', '<font size="12">a <b>b</b></font>', '<u><font size="14">a</font></u>',
    '<sup><u>a</u></sup>', '<u><sup>a</sup></u>', 'a<sup>b</sup>c<sub>d</sub>e', '<font size="9">a</font>',
    '<a href="https://example.test/">a</a>', '<a href="x">a</a>', '<link href="mailto:a@b.it">m</link>', '<a href="">a</a>', '<a>x</a>',
    '<a href=" https://example.test/x ">a</a>', '<a href="https://example.test/?a=1&amp;b=2&c=3">a</a>', '<a href="#top">a</a>',
    '<a href="document:x">a</a>', '<a href="pdf:file.pdf">a</a>', '<a href="PDF:file.pdf">a</a>', '<a href="www.x.it">a</a>', '<a href="a:b">a</a>',
    '<a href="x:y:z">a</a>', '<a href="http://a/(b)\\c">a</a>', '<a name="x"/>', '<a name="">a</a>', '<a name="x" href="y">a</a>',
    '<a href="y" color="red" underline="1">a</a>', '<a href="y" underline="yes" ucolor="blue" uwidth="2" ukind="double">a</a>',
    '<a href="y" underline="maybe">a</a>', '<link href="y" name="Helvetica">a</link>', '<link name="Helvetica-Bold" href="y">a</link>',
    '<link href="y" face="Symbol">a</link>', '<link href>a</link>', '<link foo="1">a</link>', '<a href="y">a<br/>b</a>',
    '<a href="y"><b>a</b> b <i>c</i></a>', '<a href="y">a</a> <a href="z">b</a>', '<a href="y"><a href="z">a</a></a>', '<a href="y">',
    '<a href="\u00e9">a</a>', '<a href="\t">a</a>', '<link>a</link>', '<link href="y" dest="z">a</link>', '<a href="y" size="12" bgcolor="yellow">a</a>',
    '<a href="y" fg="#ff0000" fontsize="10">a b</a>', '<a href="y" backcolor="red" textcolor="blue" underlinekind="triple" underline="true">a</a>',
    '<u>a</u>', '<u>a b</u> c', '<u color="red">a</u>', '<u width="2">a</u>', '<u offset="-1">x</u>', '<u kind="double">a</u>',
    '<u kind="triple" gap="2">a</u>', '<u width="0.5*f">a</u>', '<u width="2*P">a</u>', '<u width=" 1.5 ">a</u>', '<u width="2mm">a</u>',
    '<u offset="0.1*F">a</u>', '<u offset="">a</u>', '<u gap="3">a</u>', '<strike>a</strike>', '<strike color="#ff0000" width="1">a</strike>',
    '<strike color="rgb(0,128,0)">a</strike>', '<strike color="0x0000ff" kind="double">a</strike>', '<u><strike>a</strike></u>',
    '<u>a<b>b</b></u>', '<u>a</u><u>b</u>', '<u color="blue">a<u>b</u></u>', '<u foo="1">a</u>', '<u color>a</u>',
    '<u color="notacolor">a</u>', '<u kind="quad">a</u>', '<u width="x">a</u>', '<u width="">a</u>', '<u width>a</u>', '<u>a</strike>',
    '<u>a', 'a</u>', '<br/><u>a</u>', '<u>a<br/>b</u>', '<u/>', '<u></u>', '<strike/>a', '<U>a</U>', '<u color="RED">a</u>',
    '<u color="red" color="blue">a</u>', '<u kind=double>a</u>', "<u kind='single'>a</u>", '<u color="hsl(120,100%,50%)">a</u>',
    '<u color="#f00">a</u>', '<u color="#ff00ff00">a</u>', '<u color="transparent">a</u>', '<u color="cmyk(1,0,0,0)">a</u>',
    '<u color="rgba(255,0,0,0.5)">a</u>', '<u color="rgb(10%,20%,30%)">a</u>', '<u color="Blacker(red,0.5)">a</u>',
    '<u width="1&amp;2">a</u>', '<u kind="&#100;ouble">a</u>', '<a href="x">a</a>', '<font size="12">a</font>', '<font color="red">a</font>', '<sup>2</sup>', '<sub>2</sub>',
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
    '</u>', '<strike>', '</strike>', '<u color="red">', '<strike kind="double">', '<font size="12">', '</font>', '<font color="red">',
    '<a href="x">', '</a>', '<link href="y">', '</link>', '<a href="https://example.test/p?q=1&amp;r=2">', '<sup>', '</sup>', '<sub>', '</sub>', '<span>', '</span>', '<nobr>', '</nobr>', '<font name="Symbol">', '<font size="+2">',
    '<font bgcolor="yellow">', '\xad', 'é', '"', "'", '=', '/', '<x/>', '<x a=b c>', '<3', '&&', '&;', '&#', '&#x;', '-', '.', ';',
    '<plaintext>', '<textarea>', '</textarea>', '<b', '</b', '<x "a">', "<x 'a>", '\n', '\t', '\xa0', '<br />', '<BR/>', '&AMP;',
    '&Alpha;', '&#0;', '&#55296;', '&#1114112;', '&#955;', 'x= "y', '<x y= "z', '<x/y>', '<x//>', '<!>', '<!->', '-->', ']]>',
]


def refusable(text):
    lowered = text.lower()
    return (any('<' + tag in lowered or '</' + tag in lowered for tag in REFUSED) or '<para' in lowered or 'blacker(' in lowered
            or 'whiter(' in lowered or (('<a' in lowered or '<link' in lowered) and ('#' in text or 'document:' in lowered or 'name' in lowered))
            or ('<' in text and (not text.isascii() or 'inf' in lowered or 'nan' in lowered
                                                         or 'courier' in lowered or 'times' in lowered)))


def rgb(colour):
    """[r, g, b] of a plain RGB colour, or None for no colour; anything else makes the paragraph ``other``."""
    if colour is None or colour == '':
        return None
    if type(colour) is not Color or colour.alpha != 1:
        raise TypeError('colour')
    return [colour.red, colour.green, colour.blue]


def finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise TypeError('not finite')
    return value


def us_value(value):
    return finite(value) if isinstance(value, (str, int, float)) else repr(value)


def describe(frag):
    """What the Go writer compares: font, text, line break, size, colour, rise, lines, links, background and nobr."""
    lines = []
    for n, kind, colour, width, offset, rise, repeats, gap in frag.us_lines:
        lines.append([n, kind, rgb(colour), us_value(width), us_value(offset), finite(rise), repeats, us_value(gap)])
    return [frag.fontName, frag.text, bool(getattr(frag, 'lineBreak', False)), finite(frag.fontSize), rgb(frag.textColor), finite(frag.rise), lines,
            [list(link) for link in frag.link], rgb(getattr(frag, 'backColor', None)), bool(getattr(frag, 'nobr', False))]


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
        if frag.greek or hasattr(frag, 'cbDefn') or frag.fontName not in FONTS + ('Symbol', 'ZapfDingbats'):
            other = True
            continue
        try:
            frags.append(describe(frag))
        except TypeError:
            other = True
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


def colours():
    from reportlab.lib.colors import CMYKColor, getAllNamedColors
    lines = ['// Code generated from reportlab 4.2.5 (colors.getAllNamedColors); DO NOT EDIT.', '', 'package pdfexport', '',
             '// paraNamedColors are the plain RGB colour names toColor knows (lower case).',
             'var paraNamedColors = map[string]color{']
    other = []
    for name, value in sorted(getAllNamedColors().items()):
        if isinstance(value, CMYKColor) or value.alpha != 1 or name != name.lower():
            other.append(name.lower())
        else:
            lines.append('\t%s: {r: %r, g: %r, b: %r},' % (go_string(name), value.red, value.green, value.blue))
    lines += ['}', '', '// paraUnportedColors are the names of colours that are CMYK or transparent.',
              'var paraUnportedColors = map[string]bool{']
    lines += ['\t%s: true,' % go_string(name) for name in sorted(set(other))]
    gofmt = shutil.which('gofmt')
    return subprocess.run([gofmt], input='\n'.join(lines + ['}', '']), capture_output=True, text=True, check=True).stdout


def html5_entities():
    from html.entities import html5
    lines = ['// Code generated from html.entities.html5 of the pinned interpreter; DO NOT EDIT.', '', 'package pdfexport', '',
             '// html5Entities are the character references of HTML 5 (with and without the closing semicolon).',
             'var html5Entities = map[string]string{']
    lines += ['\t%s: %s,' % (go_string(name), go_string(html5[name])) for name in sorted(html5)]
    gofmt = shutil.which('gofmt')
    return subprocess.run([gofmt], input='\n'.join(lines + ['}', '']), capture_output=True, text=True, check=True).stdout


def corpus():
    rng = random.Random(20261110)
    texts = list(TEXTS)
    for _ in range(2600):
        texts.append(''.join(rng.choice(TOKENS) for _ in range(rng.randint(1, 7))))
    cases = [outcome(text, FONTS[i % 2] if i % 7 else FONTS[i % 4]) for i, text in enumerate(texts)]
    if not cases or not any('frags' in case for case in cases) or not any('error' in case for case in cases):
        raise SystemExit('the corpus is empty or one-sided')
    return json.dumps({'cases': cases}, ensure_ascii=True, separators=(',', ':'), allow_nan=False) + '\n'


def main():
    outputs = {PACKAGE / 'entities_gen.go': entities(), PACKAGE / 'colors_gen.go': colours(), PACKAGE / 'html5_gen.go': html5_entities(), PACKAGE / 'testdata/paragraphs.json': corpus()}
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
