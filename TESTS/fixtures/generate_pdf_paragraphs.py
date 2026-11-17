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
REFUSED = ('bullet', 'seq', 'seqchain', 'seqdefault', 'seqformat', 'seqreset')
FONTS = ('Helvetica', 'Helvetica-Bold', 'Helvetica-Oblique', 'Helvetica-BoldOblique',
         'Courier', 'Courier-Bold', 'Courier-Oblique', 'Courier-BoldOblique',
         'Times-Roman', 'Times-Bold', 'Times-Italic', 'Times-BoldItalic')

def image_data(width=4, height=3, colour=(200, 30, 30)):
    import base64
    import io
    from PIL import Image
    out = io.BytesIO()
    Image.new('RGB', (width, height), colour).save(out, 'JPEG')
    return 'data:image/jpeg;base64,' + base64.b64encode(out.getvalue()).decode()


IMAGE = image_data()


TEXTS = [
    '', 'ASD Aurora', 'Sport & Fun', 'Sport &amp; Fun', 'A < B', 'A <b>B</b>', 'A > B', '&', '<', '&amp;', 'a&b', 'a &b c', 'a&b;c',
    'R&D', 'AT&T Italia', 'Yoga &amp; Pilates &lt;adulti&gt;', '&lt;b&gt;', 'a<br/>b', 'a<br>b', 'a<br></br>b', 'a</br>b', '<br/>',
    '<b>grassetto</b>', '<i>corsivo</i> e <b><i>entrambi</i></b>', '<strong>a</strong><em>b</em>', '<b>a', 'a</b>', '<b><i>a</b></i>',
    '<para size="12">a b</para>', '<para fontsize="14" leading="20">a</para>', '<para font="Helvetica-Bold">a</para>',
    '<para face="Symbol">abc</para>', '<para color="red">a</para>', '<para fg="#00ff00">a</para>', '<para backcolor="yellow">a</para>',
    '<para align="center">a</para>', '<para align="right">a</para>', '<para align="justify">a</para>', '<para align="bogus">a</para>',
    '<para spaceb="5" spacea="6">a</para>', '<para leading="+2">a</para>', '<para size="-2">a</para>', '<para lindent="10">a</para>',
    '<para lindent="0">a</para>', '<para underlinewidth="2" underlinecolor="red">a<u>b</u></para>', '<para strikeoffset="2">a<strike>b</strike></para>',
    '<para splitlongwords="0">a</para>', '<para allowwidows="0">a</para>', '<para alloworphans="1">a</para>', '<para spaceshrinkage="0.1">a</para>',
    '<para texttransform="uppercase">a</para>', '<para foo="1">a</para>', '<para size>a</para>', '<para size="x">a</para>',
    '<para color="nope">a</para>', '<para wordwrap="LTR">a</para>', '<para wordwrap="bogus">a</para>', '<para borderwidth="1">a</para>',
    '<para autoleading="max">a</para>', '<para autoleading="off">a</para>', '<para justifybreaks="1">a</para>', '<para bfont="Foo">a</para>',
    '<para font="Arial">a</para>', '<para font="Courier">a</para>', '<para size="12" color="blue">a<b>b</b> c</para>',
    '<para align="center" size="8">a <u>b</u> <font size="12">c</font></para>', '<para align="right" bgcolor="#eeeeee">a b c</para>',
    '<para underlinecolor="nope">a</para>', '<para underlinewidth="x">a</para>', '<para alignment="center" spaceBefore="3">a</para>',
    '<para textcolor="green" backColor="red" leftindent="0">a</para>', '<para color="red" color="blue">a</para>',
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
    'a <img src="%s" width="20" height="10"/> b' % IMAGE, '<img src="%s"/>' % IMAGE, 'x<img src="%s" valign="middle"/>y' % IMAGE,
    '<img src="%s" width="50%%"/>' % IMAGE, '<img src="%s" height="200%%" valign="50%%"/>' % IMAGE, '<img/>', '<img src=""/>',
    '<img src="/nonexistent.jpg"/>', '<img src="%s">x</img>' % IMAGE, '<img src="%s" width="x"/>' % IMAGE, '<img src="%s" foo="1"/>' % IMAGE,
    '<img src="data:image/png;base64,iVBORw0KGgo="/>', 'a<img src="%s" width="0"/>b' % IMAGE, '<b><img src="%s"/></b>' % IMAGE,
    '<u><img src="%s"/>a</u>' % IMAGE, '<img src="%s" valign="top" width="2mm" height="1in"/>' % IMAGE, '<img src="%s" valign="bogus"/>' % IMAGE,
    '<img src="%s" valign="+3"/>' % IMAGE, '<img src="%s" valign="Sub"/>' % IMAGE, '<img src="%s"/><img src="%s"/>' % (IMAGE, IMAGE),
    '<img src="%s" width="10"/> texto <img src="%s" width="10"/>' % (IMAGE, IMAGE), '<img src="%s"/>' % IMAGE + '<br/>' + 'x',
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
    # The Symbol font of <greek>, the character of <unichar>, the Courier and Times faces, and the callbacks <index>
    # and <onDraw> that these documents cannot draw.
    '<greek>abg</greek>', '<greek>città</greek>', 'a<greek>ab</greek>b', '<b><greek>a</greek></b>', '<greek>a&amp;b</greek>',
    '<greek></greek>', '<greek>a</greek><greek>b</greek>', '<greek>a<br/>b</greek>', '<i><greek>de</greek></i>', '<greek>日</greek>',
    '<greek>ab日</greek>', '<greek>a</greek> ê <greek>日本</greek>',
    '<greek>ab</greek> &amp; <greek>gd</greek>', '<greek>a</greek>', '<greek>xyz</greek>', '<greek>ab cd</greek>', '<greek x="1">a</greek>',
    '<greek>a</greek></greek>', '<unichar code="65"/>', '<unichar code="0x263A"/>', '<unichar code="0b101"/>', '<unichar code="010"/>',
    '<unichar code="1114112"/>', '<unichar code="x"/>', '<unichar/>x', '<unichar code="65">x</unichar>', '<unichar code="+65"/>',
    '<unichar code=" 65 "/>', '<unichar code="0x1F600"/>', '<unichar code="233"/>', '<unichar code="0o101"/>', '<unichar code="-1"/>',
    '<unichar code="1_0"/>', '<unichar code="0x_41"/>', '<unichar code=""/>', '<unichar code>', '<unichar foo="1"/>',
    '<unichar name="LATIN SMALL LETTER A"/>', '<unichar name="zzz"/>', '<unichar name="LATIN SMALL LETTER A" code="65"/>',
    '<unichar name="latin small letter a"/>', '<unichar name="LATIN CAPITAL LETTER A WITH MACRON AND GRAVE"/>',
    '<unichar name="BYTE ORDER MARK"/>', '<unichar name="HANGUL SYLLABLE GAG"/>', '<unichar name="CJK UNIFIED IDEOGRAPH-4E00"/>',
    '<unichar name="LATIN SMALL LETTER A" foo="1"/>', '<unichar name=""/>',
    '<unichar name="EURO SIGN"/> e <unichar name="BLACK HEART SUIT"/>',
    'a<unichar code="65"/>b', '<b><unichar code="65"/></b>', '<greek><unichar code="65"/></greek>',
    '<font name="Courier">a</font>', '<font face="Times-Italic">a b</font>', '<b><font name="Times">x</font></b>',
    '<para font="Times-Bold">a</para>', '<font name="Courier-BoldOblique">a</font>', '<font name="Times-BoldItalic">a</font>',
    '<link href="x" name="Times-Bold">a</link>', '<font name="Times-Roman">a</font>', '<font name="Courier">αβγ 日本</font>',
    '<font name="Times-Italic">città è €</font>', '<para face="Courier">a <b>b</b></para>', '<font name="Courier">a</font><font name="Times-Roman">b</font>',
    '<font name="Times-Bold">a</font> b <font name="Times-Italic">c</font>', '<u><font name="Courier">a</font></u>',
    '<index item="a"/>x', '<onDraw name="x"/>y', '<index item="a" format="I"/>x', '<index/>', '<index item="a" format="Z"/>',
    '<index item="a" offset="x"/>', '<onDraw/>', '<onDraw name="x" label="l"/>', '<onDraw foo="1"/>', '<index item="a" foo="1"/>',
    # Numbers written with the decimal digits of other scripts (Python's int() and float() read them).
    '<font size="１２">a</font>', '<font size="١٢">a</font>', '<para size="١٢">a</para>', '<para lindent="1٢">a</para>',
    '<font size="१२">a</font>', '<font size="١٢٣">a</font>', '<para spaceb="٢" spacea="٣">a</para>',
    '<unichar code="٦٥"/>', '<unichar code="٠x٤١"/>', '<font size="１２.５">a</font>', '<u width="١.٥">a</u>',
    '<sup rise="١">a</sup>', '<img src="%s" width="١٠"/>' % IMAGE,
    '<para alloworphans="π">a</para>', '<font color="città">a</font>', '<font color="π">a</font>', '<u color="rosso">a</u>',
    '<img src="%s" valign="١"/>' % IMAGE,
    # Anchors (<a name="..."/>) and internal links (#name, document:name): the fragments they make and the targets the
    # paragraph accepts, printable or not.
    'A <a name="top"/> B', '<a name="top"/>', '<a name=" top "/>text', 'before<a name="x"/>after',
    'before<a name="x"/>after<a name="y"/>end', '<b><a name="x"/>bold</b>', '<i><a name="uno"/><a name="due"/>due</i>',
    '<a name=""/>', '<a name="   "/>', '<a name/>', '<a name="x" href="y"/>', '<a name="x">text</a>', '<a name="x"></a>',
    '<a name="x"> </a>', '<a name="città"/>ok', '<a name="日本語"/>ok', '<a name="a b"/>ok', '<a name="a\\b"/>ok',
    '<a name="a(b)c"/>ok', '<a name="x"/><a name="x"/>', 'a <a name="x"/> b', '<a name="x"/><b><i>y</i></b>',
    '<a href="#top">a</a>', '<a href="#top">a</a> <a name="top"/>', '<a name="top"/> <a href="#top">a</a>',
    '<a href="document:top">a</a>', '<a name="top"/> <a href="document:top">a</a>', '<link href="#top">a</link>',
    '<link dest="#top">a</link>', '<link target="#top">a</link>', '<link destination="document:top">a</link>',
    '<a href="#">a</a>', '<a href="document:">a</a>', '<a href="#a b">a</a>', '<a href="#città">a</a>',
    '<a href="#a(b)c">a</a>', '<a href="#a\\b">a</a>', '<a href="#a&#9;b">a</a>', '<a href="#a&#1;b">a</a>',
    '<a href="#a&#127;b">a</a>', '<a href="#a&#128;b">a</a>', '<a href="#日本">a</a>', '<a href="#top" color="red">a</a>',
    '<b><a href="#top">a</a></b>', '<a href="#top"><b>a</b> b</a>', '<a href="x y">a</a>', "<a href='a\"b'>a</a>",
    '<a href="città/日本">a</a>', '<a href="pdf:città.pdf">a</a>', '<a href="pdf:a&#1;b.pdf">a</a>',
    # <seq template> padding: reportlab's line breaking splits on whitespace, so a run of spaces
    # between words lays out as one space; these pairs record the text of a width of 5 and one of
    # 50 at the start, the end and next to other fragments.
    '<seq template="A%(w)5sB"/>', '<seq template="A%(w)50sB"/>',
    'A<seq template="%(w)5s"/>B', 'A<seq template="%(w)50s"/>B',
    '<b>A</b><seq template="%(w)5s"/>', '<b>A</b><seq template="%(w)50s"/>',
    'A<seq template="%(w)-5s"/>Z', 'A<seq template="%(w)-50s"/>Z',
    '<b>A</b><seq template="%(w)5.0s"/><i>B</i>', '<b>A</b><seq template="%(w)50.0s"/><i>B</i>',
]


# The numbers of every attribute that takes one, written the ways Python's int() and float() read them: the infinities
# and NaNs, a literal beyond the float range, exact ints beyond 2**53 and int64, a hex literal, the spaces and decimal
# digits of other scripts, underscores, a fraction, an invalid suffix and the 4300-digit limit of int().
NUMBER_VALUES = ['inf', '-inf', 'nan', 'Infinity', '+NaN', '1e400', '-1e400', '1e308', '99999999999999999999', '+99999999999999999999',
                 '9007199254740993', '+9007199254740993', '0x1F', ' 12', '1 ', '12 mm', '١٢', '1_000', '1__0', '12x', '½', '1' * 400,
                 '1' * 4400]
NUMBER_ATTRIBUTES = [
    ('font size', '<font size="%s">x</font>'),
    ('font size relative', '<font size="+%s">x</font>'),
    ('link size', '<link href="y" size="%s">x</link>'),
    ('sup rise', '<sup rise="%s">x</sup>'),
    ('sup size', '<sup size="%s">x</sup>'),
    ('sub rise', '<sub rise="%s">x</sub>'),
    ('img width', '<img src="%s" width="%%s"/>' % IMAGE),
    ('img height', '<img src="%s" height="%%s"/>' % IMAGE),
    ('img valign', '<img src="%s" valign="%%s"/>' % IMAGE),
    ('u width', '<u width="%s">x</u>'),
    ('u offset', '<u offset="%s">x</u>'),
    ('u gap', '<u gap="%s">x</u>'),
    ('strike width', '<strike width="%s">x</strike>'),
    ('para size', '<para size="%s">x</para>'),
    ('para leading', '<para leading="%s">x</para>'),
    ('para spaceb', '<para spaceb="%s">x</para>'),
    ('para spacea', '<para spacea="%s">x</para>'),
    ('para spaceshrinkage', '<para spaceshrinkage="%s">x</para>'),
]
for _name, _template in NUMBER_ATTRIBUTES:
    for _value in NUMBER_VALUES:
        TEXTS.append(_template % _value)

TOKENS = [
    'a', 'b c', ' ', 'Città', '&', '&amp;', '&lt;', '&gt;', '&nbsp;', '&#65;', '&#x41;', '&#X41;', '&#;', '&#12a', '&foo;', '&alpha;',
    '&amp', '<', '>', '<b>', '</b>', '<i>', '</i>', '<br/>', '<br>', '</br>', '<strong>', '</strong>', '<em>', '</em>', '<x>', '</x>',
    '<x y="1">', "<x y='>'>", '<x y="', '<!-- c -->', '<!--', '<?pi?>', '<!DOCTYPE x>', '<![CDATA[x]]>', '<para>', '</para>',
    '<title>', '</title>', '<script>', '</script>', '<b/>', '</>', '< b>', '<b >', '<B>', '</B >', '<a href="x">', '</a>', '<u>',
    '</u>', '<strike>', '</strike>', '<u color="red">', '<strike kind="double">', '<font size="12">', '</font>', '<font color="red">',
    '<img src="%s" width="12"/>' % IMAGE, '<a href="x">', '</a>', '<link href="y">', '</link>', '<a href="https://example.test/p?q=1&amp;r=2">', '<sup>', '</sup>', '<sub>', '</sub>', '<span>', '</span>', '<nobr>', '</nobr>', '<font name="Symbol">', '<font size="+2">',
    '<font bgcolor="yellow">', '\xad', 'é', '"', "'", '=', '/', '<x/>', '<x a=b c>', '<3', '&&', '&;', '&#', '&#x;', '-', '.', ';',
    '<plaintext>', '<textarea>', '</textarea>', '<b', '</b', '<x "a">', "<x 'a>", '\n', '\t', '\xa0', '<br />', '<BR/>', '&AMP;',
    '&Alpha;', '&#0;', '&#55296;', '&#1114112;', '&#955;', 'x= "y', '<x y= "z', '<x/y>', '<x//>', '<!>', '<!->', '-->', ']]>',
    '<greek>', '</greek>', '<unichar code="65"/>', '<font name="Courier">', '</font>', '<font name="Times-Roman">',
]


PARA_REFUSED = ('indent', 'lindent', 'rindent', 'findent', 'justify', 'border', 'texttransform', 'enddots', 'wordwrap', 'hyphenation', 'uriwastereduce',
                'embeddedhyphenation', 'autoleading')


def refusable(text):
    lowered = text.lower()
    if '<img' in lowered and not ('src="%s"' % IMAGE) in text:
        return True
    return (any('<' + tag in lowered or '</' + tag in lowered for tag in REFUSED) or ('<para' in lowered and any(word in lowered for word in PARA_REFUSED))
            or 'blacker(' in lowered
            or 'whiter(' in lowered
            or ('<' in text and not text.isascii()))


def rgb(colour):
    """[r, g, b] of a plain RGB colour, or None for no colour; anything else makes the paragraph ``other``."""
    if colour is None or colour == '':
        return None
    if type(colour) is not Color or colour.alpha != 1:
        raise TypeError('colour')
    return [colour.red, colour.green, colour.blue]


def number(value):
    """A parsed attribute number as the corpus writes it: an int or float, or a marker string for a float that is not
    finite (``inf``, ``-inf``, ``nan``), which JSON cannot hold."""
    if isinstance(value, float) and not math.isfinite(value):
        if math.isnan(value):
            return 'nan'
        return 'inf' if value > 0 else '-inf'
    return value


def us_value(value):
    return number(value) if isinstance(value, (str, int, float)) else repr(value)


def dimension(value):
    if hasattr(value, 'normalizedValue'):
        return ['pct', number(value._value)]
    return number(value)


def describe(frag):
    """What the Go writer compares: font, text, line break, size, colour, rise, lines, links, background and nobr."""
    lines = []
    for n, kind, colour, width, offset, rise, repeats, gap in frag.us_lines:
        lines.append([n, kind, rgb(colour), us_value(width), us_value(offset), number(rise), repeats, us_value(gap)])
    return [frag.fontName, frag.text, bool(getattr(frag, 'lineBreak', False)), number(frag.fontSize), rgb(frag.textColor), number(frag.rise),
            lines, [list(link) for link in frag.link], rgb(getattr(frag, 'backColor', None)), bool(getattr(frag, 'nobr', False)),
            describe_image(frag)]


def describe_image(frag):
    defn = getattr(frag, 'cbDefn', None)
    if defn is None:
        return None
    if defn.kind == 'anchor':
        return ['anchor', defn.name]
    if defn.kind != 'img':
        raise TypeError('callback')
    return ['img', defn.src, dimension(defn.width), dimension(defn.height), dimension(defn.valign)]


def style_info(style):
    lines = [[us_value(getattr(style, name + 'Width')), us_value(getattr(style, name + 'Offset')), us_value(getattr(style, name + 'Gap')),
              rgb(getattr(style, name + 'Color'))] for name in ('underline', 'strike')]
    return [style.fontName, number(style.fontSize), number(style.leading), style.alignment, number(style.spaceBefore), number(style.spaceAfter),
            rgb(style.textColor), rgb(style.backColor), lines, number(style.spaceShrinkage), bool(style.splitLongWords), bool(style.allowWidows),
            bool(style.allowOrphans)]


def style_unported(style):
    return (style.leftIndent or style.rightIndent or style.firstLineIndent or style.alignment not in (0, 1, 2) or style.borderWidth
            or style.borderPadding or style.borderRadius or style.textTransform or style.endDots or style.wordWrap
            or getattr(style, 'autoLeading', '') not in ('', 'off') or style.hyphenationLang or style.uriWasteReduce
            or style.embeddedHyphenation or style.borderColor)


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
    other = bool(paragraph.bulletText) or bool(style_unported(paragraph.style))
    frags = []
    for frag in paragraph.frags:
        if frag.fontName not in FONTS + ('Symbol', 'ZapfDingbats'):
            other = True
            continue
        defn = getattr(frag, 'cbDefn', None)
        if defn is not None and defn.kind in ('index', 'onDraw'):
            # The paragraph parses; building the document calls a canvas callback these documents do not have.
            entry['build_error'] = True
            return entry
        try:
            frags.append(describe(frag))
        except TypeError:
            other = True
    if other:
        entry['other'] = True
    else:
        try:
            entry['style'] = style_info(paragraph.style)
        except TypeError:
            entry['other'] = True
            return entry
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


def standard_fonts():
    """The width vectors of the standard faces the markup can name, Courier and Times; Helvetica, Symbol and
    ZapfDingbats are in fonts_gen.go. Also paraparser._greekConvert's byte-to-character table."""
    from reportlab.pdfbase.pdfmetrics import getFont
    names = [('courier', 'Courier'), ('courierBold', 'Courier-Bold'), ('courierOblique', 'Courier-Oblique'),
             ('courierBoldOblique', 'Courier-BoldOblique'), ('timesRoman', 'Times-Roman'), ('timesBold', 'Times-Bold'),
             ('timesItalic', 'Times-Italic'), ('timesBoldItalic', 'Times-BoldItalic')]
    lines = ['// Code generated from reportlab 4.2.5 (pdfmetrics width vectors of the standard faces and the symbol',
             '// codec); DO NOT EDIT.', '', 'package pdfexport', '']
    for variable, face in names:
        widths = getFont(face).widths
        lines.append('// %sWidths is %s in WinAnsiEncoding.' % (variable, face))
        lines.append('var %sWidths = [256]int{' % variable)
        for start in range(0, 256, 16):
            lines.append('\t' + ' '.join('%d,' % widths[start + i] for i in range(16)))
        lines.append('}')
        lines.append('')
    from reportlab.pdfbase.rl_codecs import RL_Codecs
    decode = dict(RL_Codecs._RL_Codecs__rl_codecs_data['symbol'][0])
    table = []
    for byte in range(256):
        if byte < 32:
            table.append(0)
            continue
        value = decode.get(byte, byte)
        table.append(value if value else 0)
    lines.append('// symbolGreek is paraparser._greekConvert: the character each Symbol byte stands for (0..31 give NUL).')
    lines.append('var symbolGreek = [256]rune{')
    for start in range(0, 256, 8):
        lines.append('\t' + ' '.join('0x%04X,' % value for value in table[start:start + 8]))
    lines.append('}')
    lines.append('')
    gofmt = shutil.which('gofmt')
    if not gofmt:
        raise SystemExit('gofmt is needed to write fonts_std_gen.go')
    return subprocess.run([gofmt], input='\n'.join(lines), capture_output=True, text=True, check=True).stdout


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
    outputs = {PACKAGE / 'entities_gen.go': entities(), PACKAGE / 'colors_gen.go': colours(), PACKAGE / 'html5_gen.go': html5_entities(),
               PACKAGE / 'fonts_std_gen.go': standard_fonts(), PACKAGE / 'testdata/paragraphs.json': corpus()}
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
