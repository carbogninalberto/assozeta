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
    # The attributes of <para> that place, justify, bullet, frame and case the paragraph, the <bullet> tag, and the
    # values reportlab raises on.
    '<para lindent="12">a b c</para>', '<para rindent="12">a</para>', '<para findent="12">a b</para>',
    '<para lindent="12" rindent="8" findent="-4">a</para>', '<para lindent="+3">a</para>', '<para lindent="-3">a</para>',
    '<para lindent="1cm">a</para>', '<para lindent="x">a</para>', '<para lindent>a</para>', '<para justifylastline="3">a</para>',
    '<para justifylastline="x">a</para>', '<para justifybreaks="yes">a</para>', '<para justifybreaks="maybe">a</para>',
    '<para align="justify">a b c</para>', '<para align="JUSTIFY">a</para>', '<para align="centre">a</para>',
    '<bullet>b</bullet>a', '<bullet>1.</bullet>a b', '<para bindent="12">a<bullet>x</bullet></para>',
    '<bullet><b>b</b> c</bullet>a', '<bullet>b</bullet>a<bullet>c</bullet>', '<bullet/>a', '<bullet></bullet>a',
    '<bullet>b</bullet>', '<bullet><br/>b</bullet>a', '<bullet font="Times-Bold">b</bullet>a', '<bullet> </bullet>a',
    '<bullet color="red" size="8" anchor="end">b</bullet>a', '<bullet foo="1">b</bullet>a', '<bullet anchor="bogus">b</bullet>a',
    '<bullet anchor="NUMERIC">1.</bullet>a', '<para banchor="middle" bfontsize="8" bindent="3" bcolor="blue" boffsety="1" '
    'bfont="Times-Italic"><bullet>b</bullet>a</para>', '<para bfont="Foo"><bullet>b</bullet>a</para>',
    '<para bfont="Times"><bullet>b</bullet>a</para>', '<para bcolor="nope"><bullet>b</bullet>a</para>',
    '<para borderwidth="1">a</para>', '<para bordercolor="red">a</para>', '<para borderwidth="1" bordercolor="red">a</para>',
    '<para borderwidth="1" borderpadding="4" borderradius="3" bordercolor="blue">a</para>', '<para borderradius="x">a</para>',
    '<para borderpadding="+2">a</para>', '<para autoleading="min">a</para>', '<para autoleading="MAX">a</para>',
    '<para autoleading="x">a</para>', '<para enddots=".">a</para>', '<para enddots="">a</para>',
    '<para texttransform="uppercase">a ß ﬁ İ</para>', '<para texttransform="lowercase">A Σ İ</para>',
    '<para texttransform="capitalize">hello world ΣΟΣΟΣ</para>', '<para texttransform="none">a</para>',
    '<para texttransform="UPPERCASE ">a</para>', '<para texttransform="x">a</para>',
    '<para texttransform="uppercase">a <b>b</b> <i>c</i></para>', '<para texttransform="capitalize">a<b>b c</b>d e</para>',
    '<para texttransform="uppercase">a<br/>b</para>', '<para texttransform="capitalize">a <b>b</b> c</para>',
    '<para texttransform="lowercase">ΑΣ ΣΟΣΟΣ İ</para>', '<para texttransform="capitalize">ΑΣ ΣΟΣΟΣ</para>',
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
    # wordWrap, hyphenation and URI splitting: the values reportlab accepts (case and
    # space variants included), the blank ones and the ones it raises on.
    '<para wordwrap="CJK">a b</para>', '<para wordwrap="cjk">a</para>', '<para wordwrap=" CJK ">a</para>',
    '<para wordWrap="RTL">a b</para>', '<para wordwrap="rtl">a</para>', '<para wordwrap="Rtl ">a</para>',
    '<para wordwrap="LTR">a b</para>', '<para wordwrap="ltr">a</para>', '<para wordwrap="">a</para>',
    '<para wordwrap="  ">a</para>', '<para wordwrap="bogus">a</para>', '<para wordwrap>b</para>',
    '<para wordwrap="CJK" align="justify">あ い</para>', '<para wordwrap="RTL" align="justify">a b</para>',
    '<para hyphenationlang="en_GB">a b</para>', '<para hyphenationlang="it">a</para>', '<para hyphenationlang="">a</para>',
    '<para hyphenationlang="  ">a</para>', '<para hyphenationlanguage="en">a</para>',
    '<para hyphenationLang="en">a</para>', '<para hyphenationlang>b</para>',
    '<para embeddedhyphenation="1">a b</para>', '<para embeddedhyphenation="0">a</para>',
    '<para embeddedhyphenation="true">a</para>', '<para embeddedhyphenation="yes">a</para>',
    '<para embeddedhyphenation="no">a</para>', '<para embeddedhyphenation="false">a</para>',
    '<para embeddedhyphenation="2">a</para>', '<para embeddedhyphenation="x">a</para>',
    '<para embeddedHyphenation="1">a</para>', '<para embeddedhyphenation>b</para>',
    '<para uriwastereduce="0.3">https://example.test/a</para>', '<para uriwastereduce="0">a</para>',
    '<para uriwastereduce="1">a</para>', '<para uriwastereduce="+0.2">a</para>', '<para uriwastereduce="-0.1">a</para>',
    '<para uriwastereduce="nan">a</para>', '<para uriwastereduce="inf">a</para>', '<para uriwastereduce="x">a</para>',
    '<para uriwastereduce=" 0.5 ">a</para>', '<para uriWasteReduce="0.3">a</para>', '<para uriwastereduce>b</para>',
    '<para hyphenationoverflow="1">a</para>', '<para hyphenationoverflow="0">a</para>',
    '<para hyphenationoverflow="yes">a</para>', '<para hyphenationoverflow="x">a</para>',
    '<para hyphenationOverflow="1">a</para>',
    '<para hyphenationminwordlength="3">a</para>', '<para hyphenationminwordlength="1_0">a</para>',
    '<para hyphenationminwordlength="-1">a</para>', '<para hyphenationminwordlength="0">a</para>',
    '<para hyphenationminwordlength="x">a</para>', '<para hyphenationminwordlength="１２">a</para>',
    '<para hyphenationMinWordLength="4">a</para>', '<para hyphenationminwordlength>b</para>',
    '<para hyphenationminwordlength="05">a</para>', '<para hyphenationminwordlength=" 4 ">a</para>',
    '<para hyphenationminwordlength="٤">a</para>',
    '<para uriwastereduce="+0.5">a</para>', '<para uriwastereduce="--1">a</para>', '<para uriwastereduce="1_0">a</para>',
    '<para wordwrap="ｃｊｋ">a</para>', '<para wordwrap="Cjk">a</para>',
    # A hyphenation, URI and embedded-hyphen set together, and the attributes without a
    # value: reportlab reads the ones without a converter as None.
    '<para uriwastereduce="0.3" embeddedhyphenation="1" hyphenationlang="en_GB" hyphenationminwordlength="4">a-b</para>',
    '<para enddots>a</para>', '<para enddots=" ">a</para>', '<para font>b</para>', '<para face>b</para>',
    '<para bfont>b</para>', '<para bfont>b<bullet>*</bullet></para>',
]
# Colour attributes: every css colour form (valid, invalid, percentages, alpha and
# wrong arity), the CMYK and transparent named colours, Blacker/Whiter, hex and
# decimal values, the literals, the strings and bytes, complex numbers, the colour
# functions with good and bad arguments, colour names inside expressions, junk,
# keywords and unbalanced brackets.
COLOUR_VALUES = [
    # rgb / rgba
    'rgb(0,0,0)', 'rgb(255,255,255)', 'rgb(128,0,0)', 'rgb(50%,0%,0%)', 'rgb(100%,100%,100%)', 'rgb(0%,0%,0%)',
    'rgb( 255 , 0 , 0 )', 'rgb (1,0,0)', ' rgb(1,0,0)', 'rgb(1,0,0) ', 'rgb(1,0,0)\n', 'rgb(1,0,0', 'rgb(1,0,0)x',
    'rgb(1,0)', 'rgb(1,0,0,0)', 'rgb()', 'rgb(,,)', 'rgb(256,0,0)', 'rgb(-1,0,0)', 'rgb(1.5,0,0)', 'rgb(0.5,0.5,0.5)',
    'rgb(1e2,0,0)', 'rgb(50%,0,0)', 'rgb(50 %,0%,0%)', 'rgb(1_0,0,0)', 'rgb(١٢,0,0)', 'rgb(1٢,0,0)', 'rgb(+1,0,0)',
    'rgb(inf,0,0)', 'rgb(0x10,0,0)', 'RGB(1,0,0)', 'rgb(1,(0),0)', 'rgba(255,0,0,0.5)', 'rgba(255,0,0,0)',
    'rgba(255,0,0,1)', 'rgba(255,0,0,1.5)', 'rgba(255,0,0,-1)', 'rgba(255,0,0)', 'rgba(255,0,0,0.5,1)',
    'rgba(1,0,0,1e-3)', 'rgba(255,0,0, 0.5 )', 'rgba(50%,0%,0%,0.5)', 'rgba(255,0,0,x)',
    # hsl / hsla
    'hsl(0,100%,50%)', 'hsl(120,100%,50%)', 'hsl(-120,100%,50%)', 'hsl(360,100%,50%)', 'hsl(0,0%,0%)',
    'hsl(0,100,50)', 'hsl(0,100%,50%,0.5)', 'hsla(0,100%,50%,0.5)', 'hsl(0,101%,50%)', 'hsl(1e3,50%,50%)',
    'hsl(0,50 %,50 %)', 'hsl(0,-10%,50%)', 'hsl(0,100%)', 'hsl(0,100%,50%,1.5)', 'hsl(720,100%,25%)',
    # cmyk / cmyka / pcmyk / pcmyka
    'cmyk(1,0,0,0)', 'cmyk(0,0,0,1)', 'cmyk(0.5,0.5,0,0)', 'cmyk(1,0,0,0,0.5)', 'cmyka(1,0,0,0,0.5)',
    'cmyk(2,-1,0,0)', 'cmyk(1,0,0)', 'cmyka(1,0,0,0)', 'cmyk(0,0,0,0)', 'cmyka(0,0,0,1,1)',
    'pcmyk(100,0,0,0)', 'pcmyk(0,65,100,30)', 'pcmyka(100,0,0,0,50)', 'pcmyka(100,0,0,0,1.5)',
    'pcmyk(101,-5,0,0)', 'pcmyk(100,0,0)', 'pcmyka(100,0,0,0)', 'pcmyk(0,0,0,0)', 'pcmyk( 100 , 0 , 0 , 0 )',
    # named colours, including the CMYK and transparent ones
    'transparent', 'Transparent', '_CMYK_black', '_cmyk_black', '_CMYK_white', '_PCMYK_white', '_PCMYK_black',
    'ReportLabBlue', 'reportlabblue', 'ReportLabBlueOLD', 'ReportLabBluePCMYK', 'ReportLabFidRed', 'ReportLabGreen',
    'red', 'RED', 'Rosso', 'black', 'white', 'grey', 'gray', 'fidblue', 'aqua', 'Cornflower',
    # Blacker / Whiter
    'Blacker(red,0.5)', 'Blacker(red, 0.5)', 'Whiter(blue,0.25)', 'Blacker(red,0.5)junk', 'Whiter(red,.5)',
    'Blacker(red,1.)', 'Blacker(red,1.2.3)', 'Blacker(red,)', 'Blacker(red)', 'Blacker(bogus,0.5)',
    'Blacker(rosso,0.5)', 'Whiter(_CMYK_white,0.5)', 'Blacker(_CMYK_black,0)', 'Whiter(transparent,0.5)',
    'Blacker(red ,0.5)', 'Blacker(red, 0.5 )', 'blacker(red,0.5)', 'Blacker(é,0.5)', 'Blacker(red,0.5))',
    'Whiter(red,0)', 'Whiter(red,1)', 'Blacker(red,0)', 'Blacker(red,1)',
    # hex and decimal values
    '#ff0000', '#FF0000', '#f00', '#abcdef', '#ABCDEF', '#000000', '#ffffff', '#ff000000', '0xffffff', '0XFFFFFF',
    '0xf00', '16777215', '0', '1', '-1', '+1', ' 12 ', '1_0', '0b101', '0o17', '0xFF', '#', '#zz', '16777216',
    '999999999999999',
    # literals
    '(1,0,0)', '(1,0,0,1)', '(0.5,0.5,0.5)', '(0,0,0)', '(1,1,1,1)', '(1,0,0,0.5)', '[1,0,0]', '[0,1,0,1]',
    '(1,2)', '()', '(1,0)', '(1,0,0,0,0)', '(2,0,0)', '(-1,0,0)', '(1,"a",0)', '(1,None,0)', '((1),0,0)',
    '((1,0,0),0,0)', '((1,0,0),)', '(1,0,(0,0))', '(True,0,0)', '(False,0,0)', '(1+0j,0,0)', '(0,0,1.0)',
    '(1,0,0),', '[1,0,0,]', '(((1,0,0)))', '(1, 0, 0)', '[1, 1, 1, 1]',
    # dicts, sets and strings
    '{}', '{1:2}', '{1,2}', 'set()', '{"a":1}', '{[1]:2}', '"red"', "'red'", '"rosso"', "'rgb(1,0,0)'",
    "b'red'", 'b"blue"', "b'\\x41'", '""', "''", "'red' 'blue'", '"Blacker(red,0.5)"', "'#ff0000'",
    "r'red'", "'rgb(0,0,0)'", "'transparent'",
    # numbers and complex values
    '1+2j', '1j+2j', '2j', '1.5', '-1.5', '1.', '.5', '1e2', '-(-1)', '--1', '0x10', 'True', 'False', 'None',
    '...', '1+2', '1 + -2', '7', '16777215.0', '--0', '-(-0)', '--1.5',
    # the colour functions
    'Color(1,0,0)', 'Color(1,0,0,0.5)', 'Color()', 'Color(red=1,green=0,blue=0)', 'Color(1,2,3)', 'Color(1,0)',
    'Color(foo=1)', 'Color(0,1,0, alpha=0.25)', 'ColorType(0,1,0)',
    'CMYKColor(1,0,0,0)', 'CMYKColor(0,0,0,1)', 'CMYKColor(0.5,0.5,0,0,alpha=0.5)',
    'CMYKColor(1,0,0,0,spotName="X")', 'CMYKColor(0,0,0,0,density=0.5)', 'PCMYKColor(100,0,0,0)',
    'PCMYKColor(100,65,0,30)', 'PCMYKColor(100)', 'CMYKColorSep(1,0,0,0,spotName="S")',
    'PCMYKColorSep(100,0,0,0,spotName="S")', 'CMYKColorSep(1,0,0,0)', 'CMYKColorSep()',
    'HexColor("#abcdef")', 'HexColor(11259375)', 'HexColor("rosso")', 'HexColor()', 'HexColor("#ffffff", True)',
    'HexColor("#ffffff", htmlOnly=True)', 'HexColor(1.5)', 'HexColor(b"#abcdef")',
    'Blacker((1,0,0),0.5)', 'Blacker(CMYKColor(1,0,0,0),0.5)', 'Blacker("red",0.5)',
    'Blacker(transparent,0.5)', 'Whiter(CMYKColorSep(0,0,0,0,spotName="A"),0.5)',
    'Blacker(CMYKColorSep(1,0,0,0,spotName="A"),0.5)', 'Blacker(PCMYKColorSep(100,0,0,0,spotName="A"),0.5)',
    'fade(CMYKColor(1,0,0,0),[50])', 'fade(CMYKColor(1,0,0,0),[])', 'fade("x",[50])',
    'linearlyInterpolatedColor(Color(0,0,0),Color(1,1,1),0,1,0.5)',
    'linearlyInterpolatedColor(Color(0,0,0),CMYKColor(0,0,0,0),0,1,0.5)',
    'linearlyInterpolatedColor((1,0,0),(0,0,0),0,1,0)',
    'hsl2rgb(0.5,1,1)', 'hue2rgb(0,1,0)', 'cmyk2rgb((1,0,0,0))', 'cmyk2rgb((1,0,0))', 'rgb2cmyk(1,0,0)',
    'color2bw(Color(1,0,0))', 'colorDistance(Color(1,0,0),Color(0,0,0))',
    'cmykDistance(CMYKColor(1,0,0,0),CMYKColor(1,0,0,0))', 'obj_R_G_B(Color(1,0,0))', 'obj_R_G_B((1,0,0))',
    'obj_R_G_B((1,0,0,1))', 'obj_R_G_B((1,2))', 'opaqueColor(Color(1,0,0))', 'opaqueColor(transparent)',
    'opaqueColor(CMYKColor(0,0,0,0))', 'cssParse("red")', 'cssParse("rgb(1,0,0)")', 'cssParse("nope")',
    'fp_str(1)', 'fp_str(1,0,0)', 'fp_str((1,0,0))', 'asNative("red")', 'asNative("rosso")', 'asNative(b"red")',
    'toColor("red")', 'toColor("rosso")', 'toColorOrNone(None)', 'toColorOrNone("rosso",Color(1,0,0))',
    'setColors()', '_enforceCMYK(red)', '_enforceCMYK(black)', '_enforceCMYK(Color(0,0,0))',
    '_enforceRGB(CMYKColor(0,0,0,1))', '_enforceRGB(CMYKColor(1,0,0,0))',
    '_enforceSEP(CMYKColorSep(1,0,0,0,spotName="A"))', '_enforceSEP(Color(1,0,0))',
    '_enforceSEP_BLACK(black)', '_enforceSEP_CMYK(grey)', '_enforceCMYK(grey)',
    '_chooseEnforceColorSpace(None)', '_enforceError("x",1,2)',
    'describe(Color(1,0,0),1)', 'describe(Color(1,0,0),2)', 'describe(CMYKColor(1,0,0,0),1)',
    # colour names inside expressions
    '(red,0,0)', '[red,0,0]', '(red, green, blue)', 'toColor(red)',
    # junk, keywords and unbalanced brackets
    'rosso', 'π', 'città', 'π₂', 'NoneType', 'Truexx', 'and', 'or', 'not', 'lambda', 'class', 'def', 'if',
    'else', 'for', 'while', 'import', 'return', '=', '==', '+', '-', '*', '(', ')', '[', ']', '{', '}', '((((1',
    '1+(', 'f(', 'x)', 'a b', 'red blue', '\tred', 'red\t', ' red ', 'red # comment', '#0f0 # green',
    '(((((((((1,0,0)))))))))', '[[[[1]]]]', '(((1,0,0),(0,0,0)))',
    # \N{NAME}: names, aliases and the named sequences CPython refuses.
    r"'\N{LATIN SMALL LETTER R}ED'", r"'\N{latin small letter b}lue'", r"'\N{BULLET}'", r"'\N{NUL}x'",
    r"'\N{VULGAR FRACTION ONE HALF}'", r"'\N{KEYCAP NUMBER SIGN}'", r"'\N{NO SUCH NAME}'", r"'\N{BULLET'",
    r"b'\N{BULLET}'", r"'\N{LATIN SMALL LETTER A WITH ACUTE}bba'",
    # Components of every number kind: bools, exact ints beyond 2**53, ints
    # beyond int64, infinities, and a spotName of another type.
    'Color(100000000000000000000, 0, 0)', 'Color(1180591620717411303424, 0, 0)', 'Color(True, 0, 0)', 'Color(False, 0, 0)',
    'Color(0x10,0,0)', 'Color(1e400,0,0)', 'Color(-1e400,0,0)', 'Color(100000000000000000001, 0, 0)',
    'CMYKColor(1180591620717411303424,0,0,0)', 'CMYKColor(100000000000000000000,0,0,0)', 'CMYKColor(True,0,0,0)',
    'CMYKColor(1,0,0,0,spotName=0)', 'CMYKColor(1,0,0,0,spotName=7)', 'CMYKColor(1,0,0,0,spotName=())',
    'CMYKColor(1,0,0,0,spotName=b"x")', 'PCMYKColor(0,65,100,30,spotName=False)',
    'CMYKColorSep(1,0,0,0,spotName=0)', 'PCMYKColorSep(100,0,0,0,spotName=False)',
    'Color(red,0,0)', 'Color(Color(1,0,0),0,0)', 'Color("a",0,0)', 'Color(None,0,0)',
    # Non-ASCII identifiers (NFKC, with the raw run checked by str.isidentifier), the tokenizer's 200-bracket
    # limit and the 4300-digit limit of a decimal int literal. 'İvory' lowers to 'i' and a combining dot;
    # 'Ｎｏｎｅ' normalises to a Name 'None', which ast.parse refuses (ValueError).
    'İvory', 'IVORY', 'ｒｅｄ', 'ＲＥＤ', 'ʳed', 'Ｎｏｎｅ', 'Color(ｒｅｄ,0,0)', '(ｒｅｄ)', 'a²', '²', 'ｉｆ',
    'Blacker(ｒｅｄ,0.5)', 'Color(1,0,0,ａｌｐｈａ=0.5)',
    'Color(' + '(' * 198 + '1' + ')' * 198 + ',0,0)', 'Color(' + '(' * 199 + '1' + ')' * 199 + ',0,0)',
    'Color(' + '(' * 200 + '1' + ')' * 200 + ',0,0)', '[' * 200 + ']' * 200,
    '1' * 4300, '1' * 4301, '-' + '1' * 4301, '1_' * 2150 + '1', '0x' + '1' * 5000,
    'Color(0,0,0,' + '1' * 4301 + ')',
]
# The tags the colour attributes belong to: <font color/fg/backcolor/bgcolor>, <u color>,
# <strike color>, <para color/fg/bgcolor/underlinecolor/strikecolor> and <span color>.
COLOUR_SHAPES = ['<font color=@>a</font>', '<font fg=@>a</font>', '<font backcolor=@>a b</font>',
                 '<font bgcolor=@>a b</font>', '<u color=@>a</u>', '<strike color=@>a</strike>',
                 '<para color=@>a b</para>', '<para fg=@ bgcolor=@>a b</para>',
                 '<para underlinecolor=@ strikecolor=@>a <u>b</u> <strike>c</strike></para>',
                 '<span color=@>a</span>',
                 '<a href="https://example.test/x" ucolor=@>a</a>']
for _index, _value in enumerate(COLOUR_VALUES):
    _quote = "'" if '"' in _value else '"'
    _attr = _quote + _value + _quote
    for _shape in (COLOUR_SHAPES[_index % len(COLOUR_SHAPES)], COLOUR_SHAPES[(_index * 5 + 3) % len(COLOUR_SHAPES)]):
        TEXTS.append(_shape.replace('@', _attr))



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
    ('para lindent', '<para lindent="%s">x</para>'),
    ('para rindent', '<para rindent="%s">x</para>'),
    ('para findent', '<para findent="%s">x</para>'),
    ('para borderwidth', '<para borderwidth="%s">x</para>'),
    ('para borderpadding', '<para borderpadding="%s">x</para>'),
    ('para borderradius', '<para borderradius="%s">x</para>'),
    ('para bfontsize', '<para bfontsize="%s"><bullet>*</bullet>x</para>'),
    ('para bindent', '<para bindent="%s"><bullet>*</bullet>x</para>'),
    ('para boffsety', '<para boffsety="%s"><bullet>*</bullet>x</para>'),
    ('bullet size', '<bullet size="%s">*</bullet>x'),
    ('bullet indent', '<bullet indent="%s">*</bullet>x'),
    ('bullet offsety', '<bullet offsety="%s">*</bullet>x'),
    ('para justifylastline', '<para justifylastline="%s">x</para>'),
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
    '<bullet>b</bullet>', '</bullet>', '<para lindent="5">', '<para align="justify">', '<para borderwidth="1">',
    '<para texttransform="uppercase">', '<para autoleading="max">', '<para enddots=".">', '<para bindent="2">',
]


def refusable(text):
    lowered = text.lower()
    if '<img' in lowered and not ('src="%s"' % IMAGE) in text:
        return True
    return (any('<' + tag in lowered or '</' + tag in lowered for tag in REFUSED) or ('<' in text and not text.isascii()))


def float_repr(value):
    """repr() of float(value): how the port prints a colour component."""
    return repr(float(value))


def colour_info(value):
    """The tagged JSON form of a colour attribute value, or None for no colour.

    A value toColor answers is a Color or CMYKColor instance; rl_extended_literal_eval
    returns every other value it reads as it is (a tuple or list of components, a
    string, a number, None, a dict...), and those are tagged by type. A number is its
    repr() (the digits of an int, the shortest form of a float), a colour component
    the repr() of its float, which is what fp_str draws. Anything this port could not
    compare makes the paragraph ``other``."""
    from reportlab.lib.colors import CMYKColor, CMYKColorSep, PCMYKColor, PCMYKColorSep
    if value is None:
        return None
    if isinstance(value, CMYKColor):
        if not all(isinstance(v, (int, float)) for v in
                   (value.cyan, value.magenta, value.yellow, value.black, value.density)):
            raise TypeError('colour')
        if not isinstance(value.alpha, (int, float)):
            return {'t': 'opaque'}
        classes = {CMYKColor: 'CMYKColor', PCMYKColor: 'PCMYKColor', CMYKColorSep: 'CMYKColorSep', PCMYKColorSep: 'PCMYKColorSep'}
        return {'t': 'cmyk', 'v': [float_repr(value.red), float_repr(value.green), float_repr(value.blue)],
                'c': [float_repr(value.cyan), float_repr(value.magenta), float_repr(value.yellow), float_repr(value.black)],
                'd': float_repr(value.density), 'a': float_repr(value.alpha), 's': colour_info(value.spotName),
                'cl': classes[type(value)]}
    if isinstance(value, Color):
        if not all(isinstance(v, (int, float)) for v in (value.red, value.green, value.blue, value.alpha)):
            return {'t': 'opaque'}
        return {'t': 'rgb', 'v': [float_repr(value.red), float_repr(value.green), float_repr(value.blue)],
                'a': float_repr(value.alpha)}
    if isinstance(value, tuple):
        return {'t': 'tuple', 'v': [colour_info(item) for item in value]}
    if isinstance(value, list):
        return {'t': 'list', 'v': [colour_info(item) for item in value]}
    if isinstance(value, str):
        return {'t': 'str', 'v': value}
    if isinstance(value, bytes):
        return {'t': 'bytes', 'v': value.decode('latin1')}
    if isinstance(value, bool):
        return {'t': 'bool', 'v': value}
    if isinstance(value, (int, float)):
        return {'t': 'num', 'v': float_repr(value)}
    if isinstance(value, complex):
        return {'t': 'complex', 'v': [repr(float(value.real)), repr(float(value.imag))]}
    if isinstance(value, dict):
        return {'t': 'dict', 'v': [[colour_info(key), colour_info(item)] for key, item in value.items()]}
    if isinstance(value, set):
        return {'t': 'set', 'v': [colour_info(item) for item in value]}
    if value is Ellipsis:
        return {'t': 'ellipsis'}
    raise TypeError('colour')


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
        lines.append([n, kind, colour_info(colour), us_value(width), us_value(offset), number(rise), repeats, us_value(gap)])
    return [frag.fontName, frag.text, bool(getattr(frag, 'lineBreak', False)), number(frag.fontSize), colour_info(frag.textColor), number(frag.rise),
            lines, [list(link) for link in frag.link], colour_info(getattr(frag, 'backColor', None)), bool(getattr(frag, 'nobr', False)),
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
              colour_info(getattr(style, name + 'Color'))] for name in ('underline', 'strike')]
    return [style.fontName, number(style.fontSize), number(style.leading), style.alignment, number(style.spaceBefore), number(style.spaceAfter),
            colour_info(style.textColor), colour_info(style.backColor), lines, number(style.spaceShrinkage),
            bool(style.splitLongWords), bool(style.allowWidows), bool(style.allowOrphans), number(style.leftIndent),
            number(style.rightIndent), number(style.firstLineIndent), getattr(style, 'autoLeading', ''), style.textTransform or '',
            style.endDots or '', bool(style.justifyBreaks), number(style.justifyLastLine), number(style.borderWidth),
            number(style.borderPadding), colour_info(style.borderColor),
            number(style.borderRadius) if style.borderRadius is not None else 0, style.bulletFontName or '', number(style.bulletFontSize),
            number(style.bulletIndent), style.bulletAnchor, number(getattr(style, 'bulletOffsetY', 0)),
            colour_info(getattr(style, 'bulletColor', None)),
            style.wordWrap or '', style.hyphenationLang or '', number(style.uriWasteReduce), bool(style.embeddedHyphenation),
            bool(getattr(style, 'hyphenationOverflow', 0)), number(getattr(style, 'hyphenationMinWordLength', 5))]


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
    other = False
    frags = []
    bullet = None
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
    if paragraph.bulletText is not None:
        try:
            if not isinstance(paragraph.bulletText, list):
                raise TypeError('bullet')
            bullet = [describe(frag) for frag in paragraph.bulletText]
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
        entry['bullet'] = bullet
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


def go_color(value):
    """The Go literal of a reportlab colour value, with the fields the port keeps."""
    from reportlab.lib.colors import CMYKColor, CMYKColorSep, PCMYKColor, PCMYKColorSep
    classes = {CMYKColor: 'cmykPlain', PCMYKColor: 'cmykP', CMYKColorSep: 'cmykSep', PCMYKColorSep: 'cmykPSep'}
    if isinstance(value, CMYKColor):
        fields = ['kind: colorCMYK', 'r: %r' % value.red, 'g: %r' % value.green, 'b: %r' % value.blue,
                  'c: %r' % value.cyan, 'm: %r' % value.magenta, 'y: %r' % value.yellow, 'k: %r' % value.black,
                  'dens: %r' % value.density, 'alpha: %r' % value.alpha, 'hasAlpha: true', 'class: ' + classes[type(value)]]
        if value.spotName is not None:
            fields += ['spot: &color{kind: colorStr, text: %s}' % go_string(value.spotName)]
        return '{' + ', '.join(fields) + '}'
    fields = ['r: %r' % value.red, 'g: %r' % value.green, 'b: %r' % value.blue]
    if value.alpha != 1:
        fields += ['alpha: %r' % value.alpha, 'hasAlpha: true']
    return '{' + ', '.join(fields) + '}'


def colours():
    from reportlab.lib.colors import getAllNamedColors
    named = getAllNamedColors()
    lines = ['// Code generated from reportlab 4.2.5 (colors.getAllNamedColors); DO NOT EDIT.', '', 'package pdfexport', '',
             '// paraNamedColors are the colour names toColor looks up by lower case: the names',
             '// getAllNamedColors keeps as they are.',
             'var paraNamedColors = map[string]color{']
    for name, value in sorted(named.items()):
        if name == name.lower():
            lines.append('\t%s: %s,' % (go_string(name), go_color(value)))
    lines += ['}', '', '// paraColorNames are every name getAllNamedColors returns, as the safe names of',
              '// rl_extended_literal_eval match them.',
              'var paraColorNames = map[string]color{']
    for name, value in sorted(named.items()):
        lines.append('\t%s: %s,' % (go_string(name), go_color(value)))
    lines += ['}', '', '// paraNamedColorOrder is the order getAllNamedColors returns the names in, which',
              '// describe walks.',
              'var paraNamedColorOrder = []string{']
    for name in named:
        lines.append('\t%s,' % go_string(name))
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


def case_tables():
    """The code points whose str.upper/lower/title mapping is more than one character: the one-character mappings are
    the ones Go's unicode tables give, which the fixture of case_mappings() checks for every code point."""
    lines = ['// Code generated from CPython 3.13 (str.upper, str.lower and str.title of every code point); DO NOT EDIT.',
             '', 'package pdfexport', '']
    for op in ('upper', 'lower', 'title'):
        name = 'case' + op.capitalize() + 'Extra'
        lines.append('// %s is str.%s() of the code points whose mapping is more than one character.' % (name, op))
        lines.append('var %s = map[rune]string{' % name)
        for code in range(0x110000):
            mapped = getattr(chr(code), op)()
            if len(mapped) > 1:
                lines.append('\t0x%X: %s,' % (code, go_string(mapped)))
        lines += ['}', '']
    gofmt = shutil.which('gofmt')
    if not gofmt:
        raise SystemExit('gofmt is needed to write textcase_gen.go')
    return subprocess.run([gofmt], input='\n'.join(lines), capture_output=True, text=True, check=True).stdout


def ident_tables():
    """The non-ASCII code points str.isidentifier accepts as the first character of a name and as a later one: the
    XID_Start and XID_Continue properties of the Unicode 15.1 tables of CPython 3.13 (Go's tables are 15.0), as
    compact inclusive ranges."""
    start = [code for code in range(0x80, 0x110000) if chr(code).isidentifier()]
    cont = [code for code in range(0x80, 0x110000) if ('a' + chr(code)).isidentifier()]

    def ranges(codes):
        out = []
        for code in codes:
            if out and out[-1][1] == code - 1:
                out[-1][1] = code
            else:
                out.append([code, code])
        return out

    lines = ['// Code generated from CPython 3.13 (str.isidentifier of every code point, Unicode 15.1); DO NOT EDIT.',
             '', 'package pdfexport', '']
    for name, codes, comment in (
            ('identStart', start, 'str.isidentifier() of the non-ASCII code points a name may start with'),
            ('identContinue', cont, "('a' + chr(c)).isidentifier() of the non-ASCII code points a name may go on with")):
        lines.append('// %s is %s.' % (name, comment))
        lines.append('var %s = [][2]rune{' % name)
        for low, high in ranges(codes):
            lines.append('\t{0x%X, 0x%X},' % (low, high))
        lines += ['}', '']
    gofmt = shutil.which('gofmt')
    if not gofmt:
        raise SystemExit('gofmt is needed to write pyident_gen.go')
    return subprocess.run([gofmt], input='\n'.join(lines), capture_output=True, text=True, check=True).stdout


def case_mappings():
    """Every code point whose str.upper, str.lower or str.title differs from the character, with the three mappings,
    as the proof of the case tables of the package; and strings that mix the scripts whose casing is contextual."""
    mappings = {}
    for code in range(0x110000):
        char = chr(code)
        mapped = [char.upper(), char.lower(), char.title()]
        if mapped != [char, char, char]:
            mappings[str(code)] = '|'.join(mapped)
    specials = ['Σ', 'σ', 'ς', 'İ', 'A', 'a', ' ', '.', "'", 'ß', 'Ǆ', 'ﬁ']
    strings = list(specials)
    strings += [first + second for first in specials for second in specials]
    strings += ['ΣΟΣΟΣ', 'ΑΣ.', 'ΑΣ ΑΣ', 'ΣΣΣ', 'aΣ', 'Σ\u0301a', "ΑΣ'Α", "Σ'Σ", 'ΑΣ \u00ad ΑΣ', 'ΣΣ.', 'Σ A', 'AΣB',
                'İstanbul', 'İİ', 'Aİ', 'ßß', 'ẞ', 'fi', 'ﬁﬁ', 'ﬂ', 'ﬅ', 'ﬆ', 'ﬀ', 'ﬃ', 'ﬄ', 'Ǆǅǆx', 'xǄ', 'a1b',
                "don't", 'hello world', 'a-b c', '  a', 'ÉCOLE', 'école', 'ǰ', 'ΐ', 'ŉ', 'ᾈ', 'ᾀ', 'Ὰ', 'ΐΐ',
                'Straße İstanbul ΣΟΦΟΣ ǅ ﬁx', 'Ωmega ΑΣ', 'ﬁ ﬀ ß Ǆ', 'ÀÉÎÕÜ àéîõü', 'Σ sigma Σ', 'İIıi', 'ǅungla',
                'ﬁne Straße', 'aΣ İß Ǆﬁ', 'MiXeD CaSe', 'ÀÉ ÀÉ', 'াΑΣ', 'ΑΣা']
    corpus = [[text, text.upper(), text.lower(), text.title()] for text in strings]
    return json.dumps({'mappings': mappings, 'corpus': corpus}, ensure_ascii=False, separators=(',', ':')) + '\n'


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
               PACKAGE / 'fonts_std_gen.go': standard_fonts(), PACKAGE / 'textcase_gen.go': case_tables(),
               PACKAGE / 'pyident_gen.go': ident_tables(),
               PACKAGE / 'testdata/paragraphs.json': corpus(), PACKAGE / 'testdata/casemapping.json': case_mappings()}
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
