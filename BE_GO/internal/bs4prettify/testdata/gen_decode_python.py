"""str(BeautifulSoup(markup, 'html.parser')) golden for soup.go's
Parse/Decode in BE_GO/internal/bs4prettify.

The inputs are the documents testdata/editor.json and testdata/full.json
already use (the stored/editor documents and the random markup corpus, which
covers comments, declarations, doctypes, processing instructions, CDATA,
script/style/textarea and entity edge cases), plus a few that name one
NavigableString subclass each. The expected value is ``str(soup)`` - i.e.
``Tag.decode()`` with no indentation - or the exception bs4 raises on that
markup.

Run with the production Python (bs4 4.13.3, CPython 3.13.16):

    python -I BE_GO/internal/bs4prettify/testdata/gen_decode_python.py
"""
import json
from pathlib import Path
import warnings

import bs4
from bs4 import BeautifulSoup

HERE = Path(__file__).resolve().parent
DECODE = HERE / 'decode_python.json'

EXTRA = [
    '',
    'plain text',
    '<!-- c -->',
    '<!DOCTYPE html>',
    '<?pi?>',
    '<![CDATA[x]]>',
    '<!x>',
    '<script>if (a < b) { x = "&amp;"; }</script>',
    '<style>p::after { content: "<&>"; }</style>',
    '<textarea>  a &amp; b  </textarea>',
    '<template><p>x</p></template>',
    '<rt>a</rt><rp>b</rp>',
    '<pre>  x  </pre>',
    '<p>&amp; &foo; &amp &#150; &#0; &#xD800;</p>',
    '<a title=\'x"y\'>t</a>',
    '<a title="x\'y">t</a>',
    '<a title="both \' and \"">t</a>',
    '<meta charset="latin-1">',
    '<meta http-equiv="content-type" content="text/html; charset=iso-8859-1">',
    '<br><br/><img src="x"><input>',
    '<P CLASS="A b">X</P>',
    '<p a b="1" c>x</p>',
    'a &lt; b &gt; c',
    'x\u0000y\u000b',
]


def decode_value(value):
    """A golden input/output value back to a Python str."""
    if isinstance(value, dict):
        return bytes.fromhex(value['hex']).decode('utf-8', 'surrogatepass')
    return value


def _has_surrogate(value):
    return any(0xd800 <= ord(c) <= 0xdfff for c in value)


def enc(value):
    """JSON form of one string: {"hex": ...} when it holds a lone surrogate."""
    if value is None:
        return None
    if isinstance(value, str):
        return {'hex': value.encode('utf-8', 'surrogatepass').hex()} if _has_surrogate(value) else value
    raise TypeError(value)


def corpus_inputs():
    """Every input of the committed corpora, then EXTRA, without duplicates."""
    seen = set()
    inputs = []
    for name in ('editor.json', 'full.json'):
        for case in json.loads((HERE / name).read_text(encoding='utf-8')):
            value = decode_value(case['input'])
            if value in seen:
                continue
            seen.add(value)
            inputs.append(value)
    for value in EXTRA:
        if value in seen:
            continue
        seen.add(value)
        inputs.append(value)
    return inputs


def decode(markup):
    """(str(soup), None) or (None, exception class name)."""
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        try:
            return str(BeautifulSoup(markup, features='html.parser')), None
        except Exception as exc:
            return None, type(exc).__name__


def main():
    cases = []
    for markup in corpus_inputs():
        output, error = decode(markup)
        cases.append({'input': enc(markup), 'output': enc(output), 'error': error})
    document = {'bs4': bs4.__version__, 'python': '3.13.16', 'cases': cases}
    DECODE.write_text(json.dumps(document, ensure_ascii=False) + '\n', encoding='utf-8')
    written = sum(1 for c in cases if c['output'] is not None)
    print(f'{len(cases)} cases ({written} rendered, {len(cases) - written} raise) -> {DECODE}')


if __name__ == '__main__':
    main()
