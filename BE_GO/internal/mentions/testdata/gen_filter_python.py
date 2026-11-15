"""filter_mentions golden for BE_GO/internal/mentions.

Runs the real filter_mentions logic of BE/application/models/utils.py:151-216
(the four helpers below are copied from that file; nothing imports Django or
the application settings) on markup that used to be refused by the Go port,
and records str(soup) plus the FilterBlank answer for each case.

The context is plain dicts, which get_nested_attr reads with .get, exactly
like it reads a model with getattr.

Run with the production Python (bs4 4.13.3, CPython 3.13.16):

    python -I BE_GO/internal/mentions/testdata/gen_filter_python.py
"""
import json
import logging
from pathlib import Path
import re

import bs4
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
GOLDEN = HERE / 'filter_python.json'

LEGACY_MENTION_ALIASES = {
    'nome': 'associate.first_name',
    'cognome': 'associate.last_name',
    'codicefiscale': 'associate.tax_code',
    'cf': 'associate.tax_code',
    'datadinascita': 'associate.born_date',
    'datanascita': 'associate.born_date',
    'cittadinascita': 'associate.born_city',
    'indirizzo': 'associate.address',
    'citta': 'associate.address_city',
    'cap': 'associate.address_cap',
    'nazionalita': 'associate.nationality',
    'email': 'associate.email',
    'telefono': 'associate.phone',
    'note': 'associate.notes',
    'nometutore': 'main_tutor.first_name',
    'cognometutore': 'main_tutor.last_name',
    'codicefiscaletutore': 'main_tutor.tax_code',
    'emailtutore': 'main_tutor.email',
    'telefonotutore': 'main_tutor.phone',
    'numeroricevuta': 'invoice.number',
    'dataricevuta': 'invoice.creation_date',
    'importoattivita': 'invoice.activity_fee',
    'importoiscrizione': 'invoice.membership_fee',
    'importototale': 'invoice.total_amount',
    'dataodierna': 'other.today',
    'listacorsi': 'other.courses_list',
}

CONTEXT = {
    'associate': {
        'first_name': 'Mario & <Luigi>',
        'last_name': 'Rossi',
        'tax_code': 'RSSMRA80A01H501U',
        'born_date': '1980-01-02',
        'born_city': 'Roma',
        'address': 'Via Roma 1',
        'address_city': 'Roma',
        'address_cap': '00100',
        'nationality': 'Italiana',
        'email': 'mario.rossi@example.test',
        'phone': '+39 06 1234567',
        'notes': None,
    },
    'main_tutor': {
        'first_name': 'Anna',
        'last_name': 'Bianchi',
        'tax_code': 'BNCNNA75B02F205X',
        'email': 'anna.bianchi@example.test',
        'phone': '333 1234567',
    },
    'invoice': {
        'number': '2026/17',
        'creation_date': '2026-03-04',
        'activity_fee': ' 100,00 ',
        'membership_fee': '20,00',
        'total_amount': '120,00',
    },
    'other': {
        'today': '15/11/2026',
        'courses_list': 'corso A, corso B',
    },
}


def get_nested_attr(obj, attr_path):
    if not obj or not attr_path:
        return ""

    attrs = attr_path.split('.')
    current_obj = obj

    for attr in attrs:
        if current_obj is None:
            return ""
        if isinstance(current_obj, dict):
            current_obj = current_obj.get(attr)
        else:
            current_obj = getattr(current_obj, attr, None)

    return str(current_obj) if current_obj is not None else ""


def _format_mention_value(value):
    value = '' if value is None else str(value).strip()
    if len(value) == 10 and value[4] == '-' and value[7] == '-':
        return f'{value[8:10]}/{value[5:7]}/{value[0:4]}'
    return value


def _resolve_mention(key, context_objects):
    obj_name, separator, attr_name = key.partition('.')
    if not separator or obj_name not in context_objects:
        return ''
    return _format_mention_value(get_nested_attr(context_objects[obj_name], attr_name))


def filter_mentions(html_content, context_objects=None):
    if not html_content:
        return ""

    if context_objects is None:
        context_objects = {}

    soup = BeautifulSoup(html_content, 'html.parser')

    mention_spans = soup.find_all('span', {
        'class': 'mention',
        'data-type': 'mention'
    })

    logger.info(f"Found {len(mention_spans)} mention spans in the HTML content")

    for span in mention_spans:
        key = span.get('key', '')
        logger.info(f"Found mention span with key {key}")
        if not key:
            continue

        span.replace_with(_resolve_mention(key, context_objects))

    alias_pattern = re.compile(
        r'(?<![\w@])@(' + '|'.join(
            sorted(map(re.escape, LEGACY_MENTION_ALIASES), key=len, reverse=True)
        ) + r')\b',
        flags=re.IGNORECASE,
    )
    for text_node in list(soup.find_all(string=True)):
        parent = text_node.parent
        if parent is None or parent.name in {'script', 'style'}:
            continue
        if parent.name == 'span' and 'mention' in (parent.get('class') or []):
            continue

        replaced = alias_pattern.sub(
            lambda match: _resolve_mention(
                LEGACY_MENTION_ALIASES[match.group(1).lower()],
                context_objects,
            ),
            str(text_node),
        )
        if replaced != str(text_node):
            text_node.replace_with(replaced)

    return str(soup)


MENTION = 'class="mention" data-type="mention" key="associate.first_name"'

CASES = [
    ('comment', '<p>a<!-- @nome -->b</p>'),
    ('comment-only-mention-span',
     '<p><!-- <span class="mention" data-type="mention" key="associate.last_name">x</span> --></p>'),
    ('doctype', '<!DOCTYPE html><p>@nome</p>'),
    ('processing-instruction', '<?pi value?><p>x</p>'),
    ('cdata', '<![CDATA[@nome]]>'),
    ('cdata-kept', '<p><![CDATA[a < b & c]]></p>'),
    ('script', '<script>var a = "@nome"; if (a < b) { x = "&amp;"; }</script>'),
    ('style', '<style>p::after { content: "@nome"; }</style>'),
    ('textarea', '<textarea>@nome & <b></textarea>'),
    ('mismatched-end-tags', '<p>a<b>c</p>d</b>e'),
    ('extra-end-tag', '<p>x</p></p>'),
    ('unknown-entity', '<p>&foo; &amp x &nbsp y</p>'),
    ('entity-without-semicolon', '<p>a &amp b &lt c</p>'),
    ('ampersand-at-end', '<p>x &amp'),
    ('br-after-br', '<p>a<br>b<br/>c<br />d</p>'),
    ('odd-attribute-syntax', '<p a = "b"c=\'d\' e f=>x</p>'),
    ('duplicate-attribute', '<p class="mention" class="other">x</p>'),
    ('upper-case-tags', '<P>@nome</P><BR><SPAN CLASS="mention" DATA-TYPE="mention" KEY="associate.last_name">x</SPAN>'),
    ('nested-mention-spans',
     '<span ' + MENTION + '><span class="mention" data-type="mention" key="associate.last_name">x</span>y</span>'),
    ('mention-span-class-list', '<span class="other mention" data-type="mention" key="associate.first_name">x</span>'),
    ('mention-span-two-classes-joined',
     '<p><span class="mention other" data-type="mention" key="associate.first_name">x</span></p>'),
    ('mention-span-no-data-type', '<span class="mention" key="associate.first_name">x</span>'),
    ('mention-span-no-key', '<span class="mention" data-type="mention">x</span>'),
    ('mention-span-empty-key', '<span class="mention" data-type="mention" key="">x</span>'),
    ('mention-span-unresolved-key', '<span class="mention" data-type="mention" key="associate.unknown">x</span>'),
    ('mention-span-none-value', '<span class="mention" data-type="mention" key="associate.notes">x</span>'),
    ('mention-span-date', '<span class="mention" data-type="mention" key="associate.born_date">x</span>'),
    ('mention-span-entity-value', '<p>a<span ' + MENTION + '>x</span>b</p>'),
    ('alias-uppercase-first', '<p>@Nome</p>'),
    ('alias-uppercase-all', '<p>@NOME</p>'),
    ('alias-mixed-case-date', '<p>@DataDiNascita</p>'),
    ('alias-masculine-ordinal', '<p>@n\u00bame</p>'),
    ('alias-long-s', '<p>@\u017fomething</p>'),
    ('alias-preceded-by-letter', '<p>x@nome</p>'),
    ('alias-followed-by-letter', '<p>@nomeX</p>'),
    ('alias-followed-by-underscore', '<p>@nome_</p>'),
    ('alias-after-punctuation', '<p>e-mail @email.</p>'),
    ('alias-after-at-sign', '<p>@@nome</p>'),
    ('alias-longest-wins', '<p>@codicefiscaletutore @codicefiscale @cf</p>'),
    ('alias-in-attribute-not-text', '<p title="@nome">x</p>'),
    ('alias-kelvin-sign', '<p>@\u212anome \u212a @listacorsi\u212a</p>'),
    ('alias-after-non-word', '<p>\u20ac@nome</p>'),
    ('alias-after-accented-letter', '<p>\u00e8@nome</p>'),
    ('alias-after-cjk-ext-i',
     '<p>\U0002ebf0@nome \U0002ee5e@cognome</p>'),
    ('alias-after-dotted-i', '<p>\u0130@nome</p>'),
    ('alias-superscript', '<p>@nom&amp;e @nome</p>'),
    ('only-whitespace', '<p>  \n\t </p>'),
    ('only-whitespace-and-comment', '<p> <!-- @cf --> </p>'),
    ('only-whitespace-and-style', '<p> <style>a</style> </p>'),
    ('blank-with-mention', '<p><span ' + MENTION + '></span></p>'),
    ('blank-after-empty-value', '<p><span class="mention" data-type="mention" key="associate.notes">x</span></p>'),
    ('nbsp-only', '<p>&nbsp;</p>'),
    ('keyerror-long-s', '<p>@cittadina\u017fcita @codicefi\u017fscale</p>'),
    ('keyerror-dotless-i', '<p>@cittad\u0131nascita</p>'),
    ('keyerror-dotted-i', '<p>@cittad\u0130nascita @c\u0130tta</p>'),
]


def _has_surrogate(value):
    return any(0xd800 <= ord(c) <= 0xdfff for c in value)


def enc(value):
    if isinstance(value, str):
        return {'hex': value.encode('utf-8', 'surrogatepass').hex()} if _has_surrogate(value) else value
    return value


def run(markup):
    """(filter_mentions output, FilterBlank answer, error class name)."""
    try:
        result = filter_mentions(markup, CONTEXT)
    except Exception as exc:
        return None, None, type(exc).__name__
    blank = BeautifulSoup(result, 'html.parser').get_text().strip() == ''
    return result, blank, None


def main():
    cases = []
    for name, markup in CASES:
        output, blank, error = run(markup)
        cases.append({'name': name, 'html': enc(markup), 'output': enc(output), 'blank': blank, 'error': error})
    document = {'bs4': bs4.__version__, 'python': '3.13.16', 'context': CONTEXT, 'cases': cases}
    GOLDEN.write_text(json.dumps(document, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    raised = sum(1 for c in cases if c['error'] is not None)
    print(f'{len(cases)} cases ({raised} raise) -> {GOLDEN}')


if __name__ == '__main__':
    main()
