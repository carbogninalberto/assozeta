"""Write the golden documents of BE_GO/internal/pdfexport (pdf-exports.md).

Every case is a '$pdf' spec of TESTS/contracts/pdf.py built with reportlab
4.2.5 exactly as the contract builds its expectation; the golden file keeps
the decoded form the opt-in PDF standard compares (TESTS.contracts.pdf
normalize). A case reportlab raises on records the exception class instead.
Run from the repository root with the migration interpreter:
``python -I TESTS/fixtures/generate_pdf_golden.py``.
"""
import base64
import contextlib
import http.server
import io
import json
import os
import shutil
import socketserver
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from TESTS.contracts import pdf  # noqa: E402

OUTPUT = ROOT / 'BE_GO/internal/pdfexport/testdata/golden.json'
SEPARATOR = b'\n--part--\n'


def table(name, columns, rows, title='Titolo', **extra):
    return {'name': name, 'spec': {'engine': 'data_table', 'columns': columns, 'rows': rows, 'title': title, **extra}}


def jpeg(width, height, colour, mode='RGB'):
    from PIL import Image
    out = io.BytesIO()
    Image.new(mode, (width, height), colour).save(out, 'JPEG')
    return base64.b64encode(out.getvalue()).decode()


PEOPLE = [['Rossi', 'Mario', 'mario@example.test', '3331234567', '-', '-', '01/02/1990', {'int': '36'}, '10/12/2026', {'int': '30'}],
          ['Bianchi', 'Anna Maria', '-', '-', '3339876543', 'Madre', '11/03/2015', {'int': '11'}, '01/12/2026', {'int': '21'}]]
PEOPLE_COLUMNS = ['Nome', 'Cognome', 'Email', 'Telefono', 'Tel. Tutore', 'Contatto', 'Data di nascita', 'età',
                  'Scadenza certificato medico', 'Giorni rimanenti']

CASES = [
    table('dt-people', PEOPLE_COLUMNS, PEOPLE, 'Certificati medici in scadenza (entro 30 giorni dal 10/11/2026)'),
    table('dt-empty', PEOPLE_COLUMNS, [], 'Senza righe'),
    table('dt-types', ['S', 'N', 'I', 'F', 'D', 'B', 'O'],
          [['testo', None, {'int': '-12'}, {'float': '3.5'}, {'decimal': '10.50'}, {'bool': True}, {'dict': [['a', {'int': '1'}]]}],
           ['', None, {'int': '0'}, {'float': '0.0'}, {'decimal': '0.00'}, {'bool': False}, {'dict': []}]], 'Tipi'),
    table('dt-list', ['A', 'B'], [['x', {'list': ['a', {'int': '1'}]}]], 'Lista'),
    table('dt-list-empty', ['A', 'B'], [['x', {'list': []}], ['y', {'list': [{'list': []}]}], ['z', 'w']], 'Lista vuota'),
    table('dt-escapes', ['Testo'], [['città è "x" (y) \\ € – “q” … ½'], ['αβγ Ω → ✔ ❤ 日本 \t tab'], ['riga uno\nriga due\nriga tre'],
                                    ['\n'], ['a\r\nb'], [' nbsp­'], ['😀 emoji'], ['x' * 120]], 'Caratteri (α) ✔'),
    table('dt-pages', ['N', 'Nome', 'Nota'], [[{'int': str(i)}, f'Persona {i}', 'riga\ndoppia' if i % 7 == 0 else 'x'] for i in range(95)],
          'Più pagine'),
    table('dt-exact-fit', ['N'], [[{'int': str(i)}] for i in range(28)], 'Pagina piena'),
    table('dt-exact-fit-plus', ['N'], [[{'int': str(i)}] for i in range(29)], 'Pagina piena più una'),
    table('dt-a3', ['Colonna numero %d' % i for i in range(12)], [['valore abbastanza lungo %d' % i for i in range(12)]] * 3, 'A3'),
    table('dt-a2', ['Colonna numero %d' % i for i in range(22)], [['valore abbastanza lungo %d' % i for i in range(22)]] * 6, 'A2'),
    table('dt-too-wide', ['Colonna numero %d' % i for i in range(40)], [['valore abbastanza lungo %d' % i for i in range(40)]] * 2, 'Oltre A2'),
    table('dt-tall-row', ['A', 'B'], [['x', '\n'.join(str(i) for i in range(120))]], 'Riga più alta della pagina'),
    table('dt-tall-row-later', ['A', 'B'], [['x', 'y']] * 5 + [['x', '\n'.join(str(i) for i in range(120))]], 'Riga alta dopo altre'),
    table('dt-align', ['Descrizione', 'Istituzionale', 'Commerciale', 'Totale'], [['A) Entrate', '1,00', '', '2,00'], ['Totale', '', '', '3,00']],
          'Allineamento', align=['LEFT', 'RIGHT', 'CENTER', 'RIGHT'], landscape=False, is_a3=True),
    table('dt-align-bad', ['A', 'B'], [['x', 'y']], 'Allineamento errato', align=['LEFT']),
    table('dt-ragged', ['A', 'B'], [['x']], 'Riga corta'),
    table('dt-portrait-pages', ['N', 'Voce'], [[{'int': str(i)}, 'voce %d' % i] for i in range(150)], 'Verticale', landscape=False),
    table('dt-title-symbols', ['A'], [['x']], 'Ω titolo ✔ (1/2) \\'),
]

BALANCE_COLUMNS = ['Descrizione', 'Istituzionale (€)', 'Commerciale (€)', 'Totale (€)']
BALANCE_ROWS = [['A) ENTRATE', '', '', ''], ['Entrate Generali', '', '', ''], ['Quote associative', '1200,00', '0,00', '1200,00'],
                ['Totale entrate', '1200,00', '0,00', '1200,00'], ['', '', '', ''], ['D) LIQUIDITÀ', '', '', ''],
                ['Banca', '', '', '-15,50'], ['Totale', '', '', '1184,50']]


def header(logo, name, city='Roma', cap='00100', address='Via Roma 1'):
    """The custom_header of export_balance_sheet."""
    return f"""
        <div style="text-align: center;display:flex;justify-content: center;width: 200px;margin:auto; margin-bottom: 20px;">
            <div style="text-align: center;">
                <img src="{logo}" style="height: 1000px;"/>
            </div>
            <div style="text-align: center;">
                <h1 style="font-size: 20px;">{name}</h1>
                <h2 style="font-size: 16px;">{city or ''}, {cap or ''}, {address or ''}</h2>
                <h3 style="font-size: 16px;">Approvato il ____/____/______</h3>
            </div>
        </div>
        """


def balance(name, head, rows=BALANCE_ROWS, **extra):
    return table(name, BALANCE_COLUMNS, rows, 'Bilancio 2025 al 31/12/2025', align=['LEFT', 'RIGHT', 'RIGHT', 'RIGHT'], header=head,
                 is_a3=True, landscape=False, additional=[['balance_at_date', 'I saldi sono riportati al 31/12/2025.']], **extra)


LONG = 'Associazione Sportiva Dilettantistica Polisportiva Città di Castello e dintorni sezione nuoto pallanuoto tuffi e salvamento'
CASES += [
    balance('bs-no-logo', header(None, 'ASD Aurora')),
    balance('bs-logo-url', header('https://example.test/logo.png', 'ASD Aurora')),
    balance('bs-logo-png-data', header('data:image/png;base64,iVBORw0KGgo=', 'ASD Città "Aurora" (1990) – è')),
    balance('bs-logo-jpeg', header('data:image/jpeg;base64,' + jpeg(40, 20, (200, 30, 30)), 'ASD Aurora')),
    balance('bs-logo-jpeg-grey-tall', header('data:image/jpeg;base64,' + jpeg(10, 35, 90, 'L'), 'ASD Aurora')),
    balance('bs-logo-jpeg-pages', header('data:image/jpeg;base64,' + jpeg(64, 64, (10, 120, 200)), 'ASD Aurora'),
            rows=BALANCE_ROWS * 9),
    balance('bs-long-title', header(None, LONG, city=LONG, address=LONG)),
    balance('bs-unbroken-title', header(None, 'Polisportiva' * 9)),
    balance('bs-empty-address', header(None, 'ASD Aurora', city=None, cap=None, address=None)),
    balance('bs-spaces', header(None, '  ASD   Aurora\tclub\u00a0unito  ', city=' ', address='via\u00a0x   y')),
    balance('bs-empty-name', header(None, '')),
    balance('bs-name-newline', header(None, 'ASD\nAurora')),
    balance('bs-symbols', header(None, 'Ω Club ✔ αβγ', city='→')),
    balance('bs-closing-on-next-page', header(None, 'ASD Aurora'), rows=[['voce %d' % i, '1,00', '2,00', '3,00'] for i in range(53)]),
    balance('bs-rows-fill-page', header(None, 'ASD Aurora'), rows=[['voce %d' % i, '1,00', '2,00', '3,00'] for i in range(52)]),
    balance('bs-pages', header(None, 'ASD Aurora'), rows=[['voce %d' % i, '1,00', '2,00', '3,00'] for i in range(130)]),
]


def report(name, headers, rows, title='Tags', **extra):
    return {'name': name, 'spec': {'engine': 'report_table', 'headers': headers, 'rows': rows, 'title': title, **extra}}


def attendance(name, *courses):
    return {'name': name, 'spec': {'engine': 'attendance', 'courses': [
        {'course': title, 'dates': dates, 'rows': [{'name': person, 'attendance': marks, 'present_count': marks.count('P')}
                                                   for person, marks in rows]} for title, dates, rows in courses]}}


def days(count, start=1):
    return ['%02d/%02d/2026' % ((start + i) % 28 + 1, (start + i) // 28 % 12 + 1) for i in range(count)]


def people(count, dates, long=False):
    return [('Cognome%d Nome%d' % (i, i) + (' di Sant\'Angelo dei Lombardi e Montefiascone' if long and i % 5 == 0 else ''),
             ['P' if (i + j) % 3 else '' for j in range(len(dates))]) for i in range(count)]


WORDS = 'il corso di nuoto agonistico per adulti si tiene nella piscina comunale ogni settimana'
CASES += [
    report('rt-small', ['tag_name', 'creation_date'], [['Nuoto', '15/09/2026 08:30'], ['nuoto agonistico', '01/10/2026 00:00']]),
    report('rt-one-cell', ['Nome'], [['x']], 'Uno'),
    report('rt-empty-cells', ['A', 'B', 'C'], [['', 'x', ''], ['', '', '']], 'Vuote'),
    report('rt-empty-header', ['', 'B'], [['a', 'b']], ''),
    report('rt-texts', ['Nome', 'Tipo', 'Importo'], [['Quote', 'Istituzionale', '80.0'], ['Affitto', 'Commerciale', '45.5']], 'Categorie',
           footer_text='ASD Aurora', text_before='Elenco delle categorie al 10/11/2026.', text_after='Fine elenco.'),
    report('rt-text-before-long', ['Nome'], [['x']], 'Lungo', text_before=' '.join([WORDS] * 12), text_after=' '.join([WORDS] * 9)),
    report('rt-wrap', ['Descrizione lunga della colonna', 'N', 'Nota'],
           [[' '.join([WORDS] * 4), str(i), WORDS if i % 2 else 'x'] for i in range(6)], 'A capo'),
    report('rt-pages', ['N', 'Nome', 'Nota'], [[str(i), 'Persona %d' % i, WORDS if i % 9 == 0 else 'x'] for i in range(120)], 'Pagine',
           footer_text='piede'),
    report('rt-pages-wrap', ['N', 'Testo'], [[str(i), ' '.join([WORDS] * (1 + i % 7))] for i in range(60)], 'Pagine a capo',
           text_before='Prima.', text_after='Dopo.'),
    report('rt-a3', ['Colonna %d' % i for i in range(8)], [['valore %d %d' % (i, j) for i in range(8)] for j in range(5)], 'A3'),
    report('rt-a2', ['Colonna %d' % i for i in range(14)], [['valore %d %d' % (i, j) for i in range(14)] for j in range(5)], 'A2'),
    report('rt-squeezed', ['Colonna numero %d' % i for i in range(5)],
           [['Supercalifragilistichespiralidoso%d' % i * 3 for i in range(5)] for _ in range(3)], 'Stretta'),
    report('rt-squeezed-wide', ['Intestazione molto lunga numero %d' % i for i in range(60)],
           [['valore abbastanza lungo %d' % i for i in range(60)] for _ in range(3)], 'Sessanta colonne'),
    report('rt-mixed-min', ['A', 'Colonna larga con parole', 'C'], [['1', 'unaparolamoltolungasenzaspazi e altre', ''],
                                                                     ['22', 'x', 'yy']], 'Minimi'),
    report('rt-long-word', ['Codice'], [['X' * 400], ['y']], 'Parola lunga'),
    report('rt-symbols', ['Nome'], [['città è "x" (y) \\ € – “q” … ½'], ['αβγ Ω → ✔ ❤ 日本'], ['a\nb\tc  d']], 'Caratteri'),
    report('rt-text-after-split', ['N', 'Nome'], [[str(i), 'Persona %d' % i] for i in range(24)], 'Testo diviso',
           text_before=' '.join([WORDS] * 30), text_after=' '.join([WORDS] * 40)),
    report('rt-text-before-pages', ['Nome'], [['x']], 'Testo su più pagine', text_before=' '.join([WORDS] * 420)),
    report('rt-text-after-widow', ['N', 'Nome'], [[str(i), 'Persona %d' % i] for i in range(27)], 'Ultima riga sola',
           text_after=' '.join([WORDS] * 3)),
    report('rt-tall-row', ['A', 'B'], [['x', ' '.join([WORDS] * 400)]], 'Riga più alta della pagina'),
    attendance('at-single', ('Nuoto', days(4), people(3, days(4)))),
    attendance('at-no-dates-rows', ('Vuoto', days(1), [])),
    attendance('at-long-names', ('Nuoto adulti', days(12), people(12, days(12), long=True))),
    attendance('at-pages', ('Nuoto', days(10), people(70, days(10)))),
    attendance('at-a3', ('Nuoto', days(13), people(4, days(13)))),
    attendance('at-a3-full', ('Nuoto', days(22), people(4, days(22)))),
    attendance('at-a2', ('Nuoto', days(23), people(45, days(23)))),
    attendance('at-wider-than-page', ('Nuoto', days(70), people(3, days(70)))),
    attendance('at-multi', ('Calcio', days(3), people(2, days(3))), ('Nuoto', days(5, 40), people(4, days(5))),
               ('Yoga della risata per principianti assoluti', days(2, 90), people(1, days(2)))),
    attendance('at-multi-pages', ('Calcio', days(6), people(60, days(6))), ('Nuoto', days(14, 40), people(3, days(14)))),
    attendance('at-symbols', ('Ω Club ✔', ['01/01/2026', 'lunedì', ''], [('Città è', ['P', '', 'P'])])),
]


# Paragraph markup, entities and soft hyphens (reportlab's parser and its several-fragment layout), and paragraphs cut
# between two pages inside a long word.
SHY = 'Associa\xadzione Sporti\xadva Dilet\xadtan\xadti\xadsti\xadca Poli\xadsportiva'
MARKUP = 'il <b>corso</b> di nuoto &amp; pallanuoto <i>agonistico</i> per adulti R&D si tiene <b><i>ogni</i></b> settimana'
MARKUP_CELLS = [
    'Yoga & Pilates', 'Sport &amp; Fun', 'A < B', '1<2 e 3>2', '<b>grassetto</b>', '<i>corsivo</i> e <b><i>entrambi</i></b>',
    'a<br/>b', 'a<br/><br/>b', '<br/>inizio', 'fine<br/>', MARKUP, 'poli\xadsportiva', SHY, SHY + ' &amp; ' + SHY,
    'Supercalifragilistiche&amp;spiralidoso' * 3, 'Super\xadcali\xadfragi\xadlisti\xadche\xadspirali\xaddoso' * 2,
    '<b>Supercalifragilistiche</b>spiralidoso<i>precipitevolissimevolmente</i>', '&lt;b&gt;', '&nbsp;x&nbsp;', 'a &amp;&amp; b',
    '&alpha;&beta;&gamma; &Omega; &rarr; &euro;', '&#65;&#x42;&#955;', '&foo; &bar', '<h1>Titolo</h1> <div>x</div>', '<!-- nota -->visibile',
    'a - b -- c', 'tratti---&amp;---uniti-' * 6, 'fine con trattino\xad', '\xadinizio', 'a\xad\xadb ' * 12, '&#32;spazio&#32;', '<b> </b>',
    'parola&#32;&#32;doppia', 'x <b>y</b>z <i>w</i>', '<b>a </b>b', 'a<b> b</b>', 'è &egrave; € &#8364;', 'Ω &amp; ✔ <b>αβγ</b>', '',
]
CASES += [
    balance('bs-ampersand', header(None, 'Sport & Fun', city='Roma & dintorni', address='Via R&D 1')),
    balance('bs-entities', header(None, 'Sport &amp; Fun &lt;1990&gt;', city='Citt&agrave;', address='&#86;ia &foo; 1')),
    balance('bs-less-than', header(None, 'A < B', address='via 1<2 e 3>2')),
    balance('bs-markup', header(None, 'ASD <b>Aurora</b> <i>club</i>', city='Roma<br/>Lazio', address='<strong>Via</strong> Roma 1')),
    balance('bs-markup-long', header(None, ' '.join([MARKUP] * 4), city=MARKUP, address=LONG + ' &amp; ' + LONG)),
    balance('bs-markup-unbroken', header(None, 'Poli&amp;sportiva' * 12, city='<b>Poli</b>sportiva' * 14)),
    balance('bs-soft-hyphen', header(None, 'Poli\xadsportiva Città', city='Ro\xadma')),
    balance('bs-soft-hyphen-long', header(None, ' '.join([SHY] * 5), city=''.join([SHY.replace(' ', '\xad')] * 4))),
    balance('bs-soft-hyphen-frags', header(None, ' &amp; '.join([SHY] * 5), city='<b>' + SHY.replace(' ', '\xad') * 4 + '</b> x')),
    balance('bs-markup-unclosed', header(None, 'ASD <b>Aurora')),
    balance('bs-markup-mismatch', header(None, 'ASD Aurora', address='via </b> Roma')),
    balance('bs-entity-number', header(None, 'ASD &#1114112; Aurora')),
    balance('bs-logo-and-markup', header('data:image/jpeg;base64,' + jpeg(30, 30, (20, 160, 60)), 'Sport & <b>Fun</b>')),
]
CASES[-13]['spec']['additional'] = [['balance_at_date', 'I saldi <b>sono</b> riportati al 31/12/2025 &amp; oltre.']]
CASES[-12]['spec']['additional'] = [['balance_at_date', ' '.join(['I saldi sono riportati al 31/12/2025.'] * 9)],
                                    ['altro', ' '.join(['I saldi <b>sono</b> riportati &amp; al 31/12/2025.'] * 9)]]
JPEG_RED = jpeg(40, 20, (200, 30, 30))
CASES += [
    # A logo in base64 Python accepts and Go's strict decoder does not, four-component JPEG data, and data the view
    # answers with the '[LOGO]' paragraph.
    balance('bs-logo-jpeg-cmyk', header('data:image/jpeg;base64,' + jpeg(24, 12, (10, 200, 30, 5), 'CMYK'), 'ASD Aurora')),
    balance('bs-logo-base64-lines', header('data:image/jpeg;base64,' + '\n'.join(JPEG_RED[i:i + 60] for i in range(0, len(JPEG_RED), 60)),
                                           'ASD Aurora')),
    balance('bs-logo-base64-noise', header('data:image/jpeg;base64, ' + JPEG_RED.rstrip('=').replace('A', 'A!', 3) + '====', 'ASD Aurora')),
    balance('bs-logo-base64-padding', header('data:image/jpeg;base64,' + JPEG_RED.rstrip('=')[:-1], 'ASD Aurora')),
    balance('bs-logo-base64-one-over', header('data:image/jpeg;base64,QUJDR', 'ASD Aurora')),
    balance('bs-logo-base64-not-ascii', header('data:image/jpeg;base64,' + JPEG_RED[:40] + 'è' + JPEG_RED[40:], 'ASD Aurora')),
    balance('bs-logo-no-data', header('data:image/jpeg;base64,!!!', 'ASD Aurora')),
    balance('bs-logo-padding-only', header('data:image/jpeg;base64,=', 'ASD Aurora')),
    # A substitution font first used on a later page is numbered after the pages before it.
    table('late-symbols', ['Nome', 'Nota'], [['Persona %d' % i, 'x'] for i in range(70)] + [['Ω', '✔']] + [['fine', 'y']] * 60),
]
CASES += [
    report('rt-markup-cells', ['Nome & Cognome', 'A < B', '<b>Note</b>'],
           [[MARKUP_CELLS[i], MARKUP_CELLS[(i + 13) % len(MARKUP_CELLS)], MARKUP_CELLS[(i + 27) % len(MARKUP_CELLS)]]
            for i in range(len(MARKUP_CELLS))], 'Marcatura'),
    report('rt-markup-squeezed', ['<i>Colonna</i> &amp; %d' % i for i in range(12)],
           [[MARKUP_CELLS[(i * 5 + j) % len(MARKUP_CELLS)] for j in range(12)] for i in range(len(MARKUP_CELLS))], 'Marcatura stretta'),
    report('rt-markup-pages', ['N', 'Testo &amp; <b>note</b> ' + SHY], [[str(i), ' '.join([MARKUP_CELLS[i % len(MARKUP_CELLS)]] * (1 + i % 5))]
                                                                   for i in range(140)], 'Marcatura su pagine'),
    report('rt-markup-title', ['A'], [['x']], 'Titolo & <b>non</b> letto', footer_text='piede &amp; <i>x</i>'),
    report('rt-markup-text-cut', ['Nome'], [['x']], 'Tagliato', text_before=' '.join([MARKUP] * 130),
           text_after=' '.join([MARKUP + '<br/>'] * 70)),
    report('rt-markup-text-cut-pages', ['Nome'], [['x']], 'Tagliato', text_before=' '.join([MARKUP, SHY] * 400)),
    report('rt-long-word-cut', ['Nome'], [['x']], 'Parola', text_before='X' * 40000),
    report('rt-long-word-cut-words', ['Nome'], [['x']], 'Parola', text_before='prima ' * 40 + 'X' * 9000 + ' dopo' * 30,
           text_after='W' * 11000 + ' fine'),
    report('rt-long-word-cut-frags', ['Nome'], [['x']], 'Parola', text_before='prima &amp; ' * 40 + 'X' * 9000 + '<b>Y</b>' * 300 + ' dopo'),
    report('rt-soft-hyphen-cut', ['Nome'], [['x']], 'Sillabe', text_before=' '.join([SHY] * 500),
           text_after=''.join([SHY.replace(' ', '\xad')] * 300)),
    report('rt-soft-hyphen-cut-frags', ['Nome'], [['x']], 'Sillabe', text_before=' &amp; '.join([SHY] * 500),
           text_after='<i>' + ''.join([SHY.replace(' ', '\xad')] * 300) + '</i> &amp; fine'),
    report('rt-break-cut', ['Nome'], [['x']], 'A capo', text_before='<br/>'.join(['riga %d' % i for i in range(120)])),
    report('rt-markup-cell-unclosed', ['A'], [['<b>x']], 'Errore'),
    report('rt-markup-header-error', ['a</i>'], [['x']], 'Errore'),
    report('rt-markup-header-tag-open', ['A<B', 'x'], [['x', 'y']], 'Errore'),
    report('rt-markup-text-error', ['A'], [['x']], 'Errore', text_after='a <br> b'),
    attendance('at-markup', ('Yoga & Pilates <b>adulti</b>', ['01/01/2026', '<b>x</b>'],
                             [('Rossi & Bianchi', ['P', 'A &amp; B']), ('<i>Verdi</i> Anna', ['presente con nota lunga', 'P<br/>A']),
                              ('Poli\xadsportiva ' * 6, ['', 'assente & giustificato'])]),
               ('Nuoto &lt;agonisti&gt;', ['02/01/2026'], [('Neri', ['P'])])),
    attendance('at-markup-error', ('Yoga <b>', ['01/01/2026'], [('Rossi', ['P'])]), ('Nuoto', ['02/01/2026'], [('Neri', ['P'])])),
]


# The numbers of paragraph markup in a built document: the same values as generate_pdf_paragraphs.py reads, one per
# case, in the text before the table. A value reportlab raises on is recorded as its exception class.
GOLDEN_NUMBERS = ['inf', '-inf', 'nan', 'Infinity', '+NaN', '1e400', '-1e400', '1e308', '99999999999999999999', '+99999999999999999999',
                  '9007199254740993', '+9007199254740993', '0x1F', ' 12', '1 ', '12 mm', '١٢', '1_000', '1__0', '12x', '½', '1' * 400,
                  '1' * 4400]
IMAGE_URL = 'data:image/jpeg;base64,' + jpeg(4, 3, (200, 30, 30))
GOLDEN_NUMBER_ATTRIBUTES = [
    ('font-size', '<font size="%s">x</font>'),
    ('font-size-relative', '<font size="+%s">x</font>'),
    ('link-size', '<link href="https://example.test/n" size="%s">x</link>'),
    ('sup-rise', '<sup rise="%s">x</sup>'),
    ('sup-size', '<sup size="%s">x</sup>'),
    ('sub-rise', '<sub rise="%s">x</sub>'),
    ('img-width', '<img src="' + IMAGE_URL + '" width="%s"/>'),
    ('img-height', '<img src="' + IMAGE_URL + '" height="%s"/>'),
    ('img-valign', '<img src="' + IMAGE_URL + '" valign="%s"/>'),
    ('u-width', '<u width="%s">x</u>'),
    ('u-offset', '<u offset="%s">x</u>'),
    ('u-gap', '<u gap="%s">x</u>'),
    ('strike-width', '<strike width="%s">x</strike>'),
    ('para-size', '<para size="%s">x</para>'),
    ('para-leading', '<para leading="%s">x</para>'),
    ('para-spaceb', '<para spaceb="%s">x</para>'),
    ('para-spacea', '<para spacea="%s">x</para>'),
    ('para-spaceshrinkage', '<para spaceshrinkage="%s">x</para>'),
]


def number_slug(value):
    """A case name for a number: its digits and the markers of a long or special value."""
    if len(value) > 18:
        return 'n%d' % len(value) + (value[:4].replace('+', 'p').replace('-', 'm'))
    return ''.join(char if char.isascii() and (char.isalnum() or char in '._-') else '_' for char in value)


CASES += [
    report('num-%s-%s' % (name, number_slug(value)), ['N'], [['x']], 'Numeri', text_before=template % value)
    for name, template in GOLDEN_NUMBER_ATTRIBUTES
    for value in GOLDEN_NUMBERS
]

# The same exact ints combined with the layout of several fragments and lines: the font size and the rise of a run
# reach the text operators, the position of a line and the drawing of an underline, a link and a background.
BIG = '9007199254740993'
CASES += [
    report('num-combo-underline', ['N'], [['x']], 'Numeri', text_before='<para size="%s"><u>a</u></para>' % BIG),
    report('num-combo-underline-lines', ['N'], [['x']], 'Numeri', text_before='<font size="%s">a<br/>b<br/>c</font>' % BIG),
    report('num-combo-strike', ['N'], [['x']], 'Numeri', text_before='<font size="%s"><strike>a</strike></font>' % BIG),
    report('num-combo-link', ['N'], [['x']], 'Numeri', text_before='<a href="https://example.test/x" size="%s">a</a>' % BIG),
    report('num-combo-sup-underline', ['N'], [['x']], 'Numeri', text_before='<sup rise="%s"><u>a</u></sup>' % BIG),
    report('num-combo-sub-underline', ['N'], [['x']], 'Numeri', text_before='<sub rise="%s">a<u>b</u></sub>' % BIG),
    report('num-combo-background', ['N'], [['x']], 'Numeri', text_before='<font size="%s" backcolor="red">a</font>' % BIG),
    report('num-combo-underline-width', ['N'], [['x']], 'Numeri', text_before='<u width="%s">a</u>' % BIG),
    report('num-combo-u-p', ['N'], [['x']], 'Numeri', text_before='<para size="%s"><u width="2*P">a</u></para>' % BIG),
    report('num-combo-link-lines', ['N'], [['x']], 'Numeri', text_before='<a href="https://example.test/x" size="%s">a b c</a>' % BIG),
    report('num-combo-background-lines', ['N'], [['x']], 'Numeri', text_before='<font size="%s" backcolor="red">a b c</font>' % BIG),
    report('num-combo-sup-multi', ['N'], [['x']], 'Numeri', text_before='a <sup rise="%s">b</sup> c' % BIG),
]


def with_additional(case, items):
    case['spec']['additional'] = items
    return case


# Paragraph tags that draw lines (u, strike): single and several fragments, centred, over several lines, cut between pages.
RICH = 'il <u>corso</u> di <strike>nuoto</strike> e <u color="red" width="2">pallanuoto</u> <u kind="double">agonistico</u> si tiene <b><u>ogni</u></b> settimana'
RICH_CELLS = [
    '<u>sottolineato</u>', '<strike>barrato</strike>', 'a <u>b</u> c', '<u>a b c d e f g h i j k l m n o p</u>', RICH,
    '<u color="blue" kind="triple" gap="2">x</u> y', '<u width="0.5*f" offset="-0.2*F">z</u>', '<u><strike>entrambi</strike></u>',
    '<u>a<br/>b</u>', '<strike color="#00aa00">verde</strike> <u color="rgb(255,0,255)">viola</u>', '<u width="1mm">mm</u>',
    '<b><u>grassetto</u></b> <i><strike>corsivo</strike></i>', '<u>poli\xadsportiva</u> ' + SHY, '<u>' + 'lunga ' * 12 + '</u>',
]
CASES += [
    balance('bs-underline', header(None, 'ASD <u>Aurora</u> club', city='<u>Roma</u>', address='<strike>Via</strike> Roma 1')),
    balance('bs-underline-long', header(None, ' '.join([RICH] * 3), city=RICH, address='<u>' + LONG + '</u>')),
    balance('bs-underline-style', header(None, '<u color="red" width="3" offset="-4" kind="triple" gap="2">ASD</u> Aurora',
                                         city='<strike color="blue" width="2" kind="double">Roma</strike>')),
    balance('bs-underline-error', header(None, 'ASD <u color="notacolor">Aurora</u>')),
    balance('bs-underline-kind-error', header(None, 'ASD <u kind="quad">Aurora</u>')),
    balance('bs-underline-width-error', header(None, 'ASD <u width="x">Aurora</u>')),
    balance('bs-underline-unclosed', header(None, 'ASD <u>Aurora')),
    with_additional(balance('bs-underline-additional', header(None, 'ASD Aurora')),
                    [['balance_at_date', 'I saldi <u>sono</u> riportati al <strike>31/12/2025</strike>.'],
                     ['altro', ' '.join([RICH] * 4)], ['solo', '<u>tutto sottolineato</u>']]),
    report('rt-underline-cells', ['<u>Nome</u>', 'Testo <strike>barrato</strike>', 'Note'],
           [[RICH_CELLS[i], RICH_CELLS[(i + 5) % len(RICH_CELLS)], RICH_CELLS[(i + 9) % len(RICH_CELLS)]] for i in range(len(RICH_CELLS))],
           'Sottolineature'),
    report('rt-underline-squeezed', ['<u>Colonna</u> %d' % i for i in range(12)],
           [[RICH_CELLS[(i * 5 + j) % len(RICH_CELLS)] for j in range(12)] for i in range(len(RICH_CELLS))], 'Sottolineature strette'),
    report('rt-underline-pages', ['N', 'Testo'], [[str(i), ' '.join([RICH_CELLS[i % len(RICH_CELLS)]] * (1 + i % 4))] for i in range(120)],
           'Sottolineature su pagine'),
    report('rt-underline-text-cut', ['Nome'], [['x']], 'Tagliato', text_before=' '.join([RICH] * 130),
           text_after=' '.join([RICH + '<br/>'] * 70)),
    report('rt-underline-text-whole', ['Nome'], [['x']], 'Tutto', text_before='<u>' + 'sottolineato ' * 400 + '</u>',
           text_after='<strike>' + 'barrato ' * 300 + '</strike>'),
    report('rt-underline-cell-error', ['A'], [['<u color="nope">x</u>']], 'Errore'),
    attendance('at-underline', ('Yoga <u>adulti</u>', ['01/01/2026', '<u>x</u>'], [('<strike>Rossi</strike> Anna', ['P', '<u>A</u>'])]),
               ('Nuoto', ['02/01/2026'], [('Neri', ['P'])])),
]


# Fonts, sizes, colours, backgrounds, rises (font, span, nobr, sup, sub): lines of mixed sizes and colours.
FONTED = ('il <font color="red">corso</font> di <font size="14">nuoto</font> e <font size="6" color="#0000ff">pallanuoto</font> '
          '<font name="Helvetica-BoldOblique">agonistico</font> si tiene <font bgcolor="yellow">ogni</font> settimana m<sup>2</sup> H<sub>2</sub>O')
FONTED_CELLS = [
    '<font color="red">rosso</font>', '<font size="12">grande</font>', '<font size="5">piccolo</font>', 'a <font size="+3">b</font> c',
    '<font size="-2" color="green">d</font> e', FONTED, '<font name="Symbol">abc</font> x', '<font name="ZapfDingbats">abc</font>',
    '<span color="#aa00aa" size="9">span</span>', '<nobr>non spezzare questo testo</nobr> poi altro', 'm<sup>2</sup> e m<sup rise="4" size="5">3</sup>',
    'H<sub>2</sub>O', '<sup>a</sup><sub>b</sub><sup>c</sup>', '<font bgcolor="#ffee00" color="#220000">sfondo</font> senza', '<b><font color="blue">b</font></b>',
    '<font size="11">' + 'lungo testo che va a capo ' * 6 + '</font>', '<font color="red">poli\xadsportiva</font> ' + SHY,
    '<font size="30">G</font>randi <font size="4">p</font>iccoli', '<sup rise="50%" size="50%">pct</sup>', '<font size="2mm">mm</font>',
]
CASES += [
    balance('bs-font', header(None, 'ASD <font color="red" size="30">Aurora</font> club', city='<font size="10">Roma</font>',
                              address='<font color="blue">Via</font> Roma 1')),
    balance('bs-font-long', header(None, ' '.join([FONTED] * 3), city=FONTED, address='<font size="9" color="#444444">' + LONG + '</font>')),
    balance('bs-font-background', header(None, 'ASD <font bgcolor="yellow">Aurora</font> club', city='<font bgcolor="red" color="white">Roma</font>')),
    balance('bs-sup-sub', header(None, 'ASD m<sup>2</sup> H<sub>2</sub>O', city='<sup>alto</sup>', address='<sub rise="3" size="6">basso</sub> x')),
    balance('bs-font-error', header(None, 'ASD <font color="nope">Aurora</font>')),
    balance('bs-font-name-error', header(None, 'ASD <font name="Arial">Aurora</font>')),
    balance('bs-font-size-error', header(None, 'ASD <font size="x">Aurora</font>')),
    balance('bs-sup-error', header(None, 'ASD <sup rise="x">Aurora</sup>')),
    balance('bs-span-style-error', header(None, 'ASD <span style="x">Aurora</span>')),
    with_additional(balance('bs-font-additional', header(None, 'ASD Aurora')),
                    [['balance_at_date', 'I saldi <font color="red">sono</font> riportati al <font size="14">31/12/2025</font>.'],
                     ['altro', ' '.join([FONTED] * 4)], ['solo', '<font size="16" color="green">tutto grande</font>']]),
    report('rt-font-cells', ['<font color="red">Nome</font>', 'Testo <font size="12">grande</font>', 'Note'],
           [[FONTED_CELLS[i], FONTED_CELLS[(i + 5) % len(FONTED_CELLS)], FONTED_CELLS[(i + 9) % len(FONTED_CELLS)]]
            for i in range(len(FONTED_CELLS))], 'Caratteri'),
    report('rt-font-squeezed', ['<font size="6">Colonna</font> %d' % i for i in range(12)],
           [[FONTED_CELLS[(i * 5 + j) % len(FONTED_CELLS)] for j in range(12)] for i in range(len(FONTED_CELLS))], 'Caratteri stretti'),
    report('rt-font-pages', ['N', 'Testo'], [[str(i), ' '.join([FONTED_CELLS[i % len(FONTED_CELLS)]] * (1 + i % 4))] for i in range(120)],
           'Caratteri su pagine'),
    report('rt-font-text-cut', ['Nome'], [['x']], 'Tagliato', text_before=' '.join([FONTED] * 130), text_after=' '.join([FONTED + '<br/>'] * 70)),
    report('rt-font-text-whole', ['Nome'], [['x']], 'Tutto', text_before='<font size="13" color="#334455">' + 'testo grande ' * 300 + '</font>',
           text_after='<font size="5">' + 'testo piccolo ' * 600 + '</font>'),
    report('rt-font-soft-hyphen-cut', ['Nome'], [['x']], 'Sillabe', text_before=' '.join(['<font size="8">' + SHY + '</font>'] * 400)),
    report('rt-font-break', ['Nome'], [['x']], 'A capo', text_before='<br/>'.join(['<font size="%d">riga %d</font>' % (6 + i % 9, i) for i in range(120)])),
    report('rt-font-cell-error', ['A'], [['<font size="">x</font>']], 'Errore'),
    attendance('at-font', ('Yoga <font color="red">adulti</font>', ['01/01/2026', '<sup>x</sup>'],
                           [('<font size="9">Rossi</font> Anna', ['P', '<font bgcolor="yellow">A</font>'])]), ('Nuoto', ['02/01/2026'], [('Neri', ['P'])])),
]


# Links (a, link): annotations on the page, one per run of a link on a line.
LINKED = ('il <a href="https://example.test/corso?a=1&amp;b=2">corso</a> di <link href="mailto:info@example.test" color="blue">nuoto</link> e '
          '<a href="https://example.test/p" color="red" underline="1">pallanuoto</a> <a href="pdf:altro.pdf">agonistico</a> si tiene '
          '<a href="https://example.test/o"><b>ogni</b> <i>settimana</i></a> <font size="12"><a href="www.example.test">qui</a></font>')
LINKED_CELLS = [
    '<a href="https://example.test/a">link</a>', 'a <a href="b">b</a> c', '<link href="mailto:x@example.test">posta</link>',
    '<a href="https://example.test/lungo">' + 'testo del collegamento che va a capo ' * 4 + '</a>', LINKED, '<a href="">vuoto</a>', '<a>senza</a>',
    '<a href="https://example.test/u" underline="yes" ucolor="green" uwidth="1.5" ukind="double">sotto</a>', '<a href="a(b)c">parentesi</a>',
    '<a href="https://example.test/br">uno<br/>due</a>', '<a href="x:y">schema</a> <a href="PDF:f.pdf">pdf</a> <a href=" https://example.test/s ">spazi</a>',
    '<sup><a href="https://example.test/n">1</a></sup> nota', '<a href="https://example.test/f" size="14" bgcolor="yellow">grande</a>',
    '<a href="https://example.test/s">poli\xadsportiva</a> ' + SHY,
]
CASES += [
    balance('bs-link', header(None, 'ASD <a href="https://example.test/">Aurora</a> club', city='<a href="mailto:a@example.test">Roma</a>',
                              address='<link href="https://example.test/via">Via</link> Roma 1')),
    balance('bs-link-long', header(None, ' '.join([LINKED] * 3), city=LINKED, address='<a href="https://example.test/l">' + LONG + '</a>')),
    balance('bs-link-error', header(None, 'ASD <a href="y" underline="maybe">Aurora</a>')),
    balance('bs-link-font-error', header(None, 'ASD <link href="y" name="Arial">Aurora</link>')),
    balance('bs-link-unclosed', header(None, 'ASD <a href="y">Aurora')),
    with_additional(balance('bs-link-additional', header(None, 'ASD Aurora')),
                    [['balance_at_date', 'I saldi <a href="https://example.test/s">sono</a> riportati.'], ['altro', ' '.join([LINKED] * 4)],
                     ['solo', '<a href="https://example.test/t">tutto un link</a>']]),
    report('rt-link-cells', ['<a href="https://example.test/h">Nome</a>', 'Testo', 'Note'],
           [[LINKED_CELLS[i], LINKED_CELLS[(i + 5) % len(LINKED_CELLS)], LINKED_CELLS[(i + 9) % len(LINKED_CELLS)]] for i in range(len(LINKED_CELLS))],
           'Collegamenti'),
    report('rt-link-squeezed', ['<a href="https://example.test/c%d">Colonna</a> %d' % (i, i) for i in range(12)],
           [[LINKED_CELLS[(i * 5 + j) % len(LINKED_CELLS)] for j in range(12)] for i in range(len(LINKED_CELLS))], 'Collegamenti stretti'),
    report('rt-link-pages', ['N', 'Testo'], [[str(i), ' '.join([LINKED_CELLS[i % len(LINKED_CELLS)]] * (1 + i % 4))] for i in range(120)],
           'Collegamenti su pagine'),
    report('rt-link-text-cut', ['Nome'], [['x']], 'Tagliato', text_before=' '.join([LINKED] * 130), text_after=' '.join([LINKED + '<br/>'] * 70)),
    report('rt-link-text-whole', ['Nome'], [['x']], 'Tutto', text_before='<a href="https://example.test/w">' + 'collegamento lungo ' * 300 + '</a>',
           text_after='<link href="mailto:z@example.test">' + 'posta ' * 600 + '</link>'),
    report('rt-link-cell-error', ['A'], [['<a href="y" underline="maybe">x</a>']], 'Errore'),
    attendance('at-link', ('Yoga <a href="https://example.test/y">adulti</a>', ['01/01/2026', '<a href="x">x</a>'],
                           [('<a href="https://example.test/r">Rossi</a> Anna', ['P', '<a href="a">A</a>'])]), ('Nuoto', ['02/01/2026'], [('Neri', ['P'])])),
]


# Anchors (a with a name) and internal links (#name, document:name): the destinations a document binds when it is saved.
CASES += [
    report('rt-anchor-link', ['Nome'], [['x']], 'Ancora', text_before='Apri <a name="inizio"/>qui.', text_after='Torna <a href="#inizio">su</a>.'),
    report('rt-anchor-link-first', ['Nome'], [['x']], 'Ancora', text_before='Vai <a href="#fine">giù</a>.', text_after='<a name="fine"/>Fine.'),
    report('rt-anchor-pages', ['Nome'], [['x']], 'Pagine', text_before='<a name="testata"/>' + ' '.join([WORDS] * 140),
           text_after='<a href="#testata">testata</a> e <a href="document:testata">di nuovo</a>.'),
    report('rt-anchor-document', ['Nome'], [['x']], 'Documento', text_before='<a name="dest"/>Testo.', text_after='Vai <a href="document:dest">qui</a>.'),
    report('rt-anchor-undefined', ['Nome'], [['x']], 'Manca', text_before='Vai <a href="#manca">qui</a>.'),
    report('rt-anchor-rebound', ['Nome'], [['x']], 'Rilegata', text_before='<a name="punto"/>Prima.', text_after='<a name="punto"/>Dopo e <a href="#punto">vai</a>.'),
    report('rt-anchor-cells', ['Da', 'A'], [['<a name="riga1"/>uno', '<a href="#riga1">vai</a>'],
                                            ['<a name="riga2"/>due', '<a href="document:riga2">vai</a>']], 'Celle'),
    report('rt-anchor-cell-only', ['<a name="solo"/>'], [['y']], 'Solo'),
    report('rt-anchor-row-only', ['A'], [['<a name="solo"/>']], 'Solo'),
    report('rt-anchor-header', ['<a name="testata"/>Nome', 'Nota'], [['x', 'y']] * 60, 'Testata',
           text_after='<a href="#testata">testata</a>'),
    report('rt-anchor-targets', ['A'], [['<a href="città/日本">x</a>'], ['<a href="#a&#1;b">y</a>'], ['<a href="pdf:città.pdf">z</a>'],
                                         ['<a href="pdf:a&#9;b.pdf">w</a>']], 'URL', text_before='<a name="a&#1;b"/>'),
    report('rt-anchor-urls', ['A'], [['<a href="https://example.test/é?q=1&amp;r=2">x</a>'], ["<a href='a\"b(c)\\\\d'>y</a>"],
                                     ['<a href="x y">z</a>'], ['<a href="😀/日本">w</a>']], 'URL'),
]

# Inline images (img with a JPEG data URL): in the line, sized and aligned, wrapped and cut between pages.
PIX = 'data:image/jpeg;base64,' + jpeg(30, 20, (200, 30, 30))
TALL = 'data:image/jpeg;base64,' + jpeg(8, 40, (20, 160, 60))
GREY = 'data:image/jpeg;base64,' + jpeg(12, 12, 90, 'L')
CMYK = 'data:image/jpeg;base64,' + jpeg(16, 8, (10, 200, 30, 5), 'CMYK')
PICTURED_CELLS = [
    '<img src="%s"/>' % PIX, 'a <img src="%s" width="12" height="8"/> b' % PIX, 'testo <img src="%s"/> altro testo ancora' % TALL,
    '<img src="%s" valign="middle"/> <img src="%s" valign="top"/> <img src="%s" valign="baseline"/>' % (PIX, PIX, PIX),
    '<img src="%s" valign="text-top"/>x<img src="%s" valign="sub"/>y<img src="%s" valign="super"/>z' % (GREY, GREY, GREY),
    '<img src="%s" width="50%%"/>' % PIX, '<img src="%s" height="200%%" valign="30%%"/>' % PIX, 'x<img src="%s" width="0"/>y' % PIX,
    ' '.join(['parola <img src="%s" width="9" height="6"/>' % PIX] * 14), '<b>grassetto <img src="%s"/></b> <u>sotto <img src="%s"/></u>' % (GREY, GREY),
    '<a href="https://example.test/i"><img src="%s"/></a> link' % GREY, '<img src="%s"/><br/><img src="%s"/>' % (PIX, TALL),
    '<img src="%s" valign="3" width="2mm" height="0.1in"/>' % CMYK, 'poli\xadsportiva <img src="%s"/> ' % PIX + SHY,
    '<font size="16">grande <img src="%s" valign="middle"/></font> <sub><img src="%s"/></sub>' % (PIX, GREY),
]
CASES += [
    balance('bs-image', header(None, 'ASD <img src="%s" width="14" height="10"/> Aurora' % PIX, city='<img src="%s"/>' % GREY,
                               address='Via <img src="%s" valign="middle"/> Roma 1' % TALL)),
    balance('bs-image-logo', header('data:image/jpeg;base64,' + jpeg(30, 30, (20, 160, 60)), 'ASD <img src="%s" width="20" height="10"/> Aurora' % PIX)),
    balance('bs-image-long', header(None, ' '.join(['ASD <img src="%s" width="14" height="10"/> Aurora' % PIX] * 6), city=PICTURED_CELLS[8])),
    balance('bs-image-error', header(None, 'ASD <img src="%s" width="x"/> Aurora' % PIX)),
    balance('bs-image-no-src', header(None, 'ASD <img/> Aurora')),
    balance('bs-image-content-error', header(None, 'ASD <img src="%s">x</img> Aurora' % PIX)),
    balance('bs-image-bad-base64', header(None, 'ASD <img src="data:image/jpeg;base64,QUJDR"/> Aurora')),
    with_additional(balance('bs-image-additional', header(None, 'ASD Aurora')),
                    [['balance_at_date', 'I saldi <img src="%s" width="10" height="7"/> riportati.' % PIX],
                     ['altro', ' '.join(['x <img src="%s" width="10" height="7"/>' % GREY] * 40)], ['solo', '<img src="%s"/>' % PIX]]),
    report('rt-image-cells', ['<img src="%s" width="10" height="7"/> Nome' % PIX, 'Testo', 'Note'],
           [[PICTURED_CELLS[i], PICTURED_CELLS[(i + 5) % len(PICTURED_CELLS)], PICTURED_CELLS[(i + 9) % len(PICTURED_CELLS)]]
            for i in range(len(PICTURED_CELLS))], 'Immagini'),
    report('rt-image-pages', ['N', 'Testo'], [[str(i), ' '.join([PICTURED_CELLS[i % len(PICTURED_CELLS)]] * (1 + i % 4))] for i in range(100)],
           'Immagini su pagine'),
    report('rt-image-text-cut', ['Nome'], [['x']], 'Tagliato', text_before=' '.join(['parola <img src="%s" width="9" height="6"/>' % PIX] * 700),
           text_after=' '.join(['riga <img src="%s"/><br/>' % GREY] * 70)),
    report('rt-image-text-pct', ['Nome'], [['x']], 'Percentuale', text_before=' '.join(['x <img src="%s" width="20%%"/> y' % PIX] * 80)),
    report('rt-image-cell-pct', ['A', 'B'], [['<img src="%s" width="50%%"/>' % PIX, 'y']], 'Percentuale in cella'),
    report('rt-image-cell-error', ['A'], [['<img src="%s" valign="bogus"/>' % PIX]], 'Errore'),
    attendance('at-image', ('Yoga <img src="%s" width="8" height="6"/>' % PIX, ['01/01/2026', '<img src="%s"/>' % GREY],
                            [('<img src="%s" width="6" height="4"/> Rossi' % PIX, ['P', 'A'])]), ('Nuoto', ['02/01/2026'], [('Neri', ['P'])])),
]


# <para> with attributes: the style of the paragraph (size, font, colour, leading, spacing, alignment, background, line defaults).
PARAS = [
    '<para size="12" color="red">grande rosso</para>', '<para leading="20" align="center">centrato con interlinea ampia</para>',
    '<para align="right">a destra</para>', '<para align="right" size="8" bgcolor="#dddddd">a destra con sfondo</para>',
    '<para backcolor="yellow" spaceb="5" spacea="7">sfondo</para>', '<para font="Helvetica-Bold" size="9">grassetto <u>sotto</u> <i>x</i></para>',
    '<para font="Helvetica-Oblique" fg="#005500">' + 'testo lungo che va a capo ' * 8 + '</para>', '<para face="Symbol">abc</para>',
    '<para underlinewidth="2" underlinecolor="blue" underlineoffset="-3" underlinegap="2">a <u kind="double">b</u></para>',
    '<para strikeoffset="1" strikewidth="1.5">a <strike>b</strike></para>', '<para size="+3" leading="+4">relativo</para>',
    '<para splitlongwords="0">' + 'x' * 120 + '</para>', '<para spaceshrinkage="0.3">' + 'parola ' * 40 + '</para>',
    '<para align="center" size="7">a <font size="11" color="green">b</font> <sup>c</sup> <a href="https://example.test/p">d</a></para>',
    '<para lindent="0" rindent="0" findent="0" borderwidth="0">zero</para>', '<para allowwidows="0">' + 'riga uno due tre quattro cinque sei ' * 30 + '</para>',
    '<para alloworphans="1">' + 'riga uno due tre quattro cinque sei ' * 30 + '</para>', '<para size="0.5in">pollici</para>',
]
CASES += [
    balance('bs-para', header(None, '<para color="red" size="22">ASD Aurora</para>', city='<para size="10" align="center">Roma</para>',
                              address='<para backcolor="#eeeeee">Via Roma 1</para>')),
    with_additional(balance('bs-para-additional', header(None, 'ASD Aurora')),
                    [['balance_at_date', '<para align="right" backcolor="#eeeeee" spaceb="6" spacea="3">I saldi sono riportati.</para>'],
                     ['altro', '<para size="14" color="blue" align="left">' + 'testo lungo ' * 30 + '</para>']]),
    balance('bs-para-attribute-error', header(None, '<para size="x">ASD</para>')),
    balance('bs-para-unknown-error', header(None, '<para foo="1">ASD</para>')),
    balance('bs-para-font-error', header(None, '<para font="Arial">ASD</para>')),
    report('rt-para-cells', ['<para color="red">Nome</para>', 'Testo', 'Note'],
           [[PARAS[i], PARAS[(i + 5) % len(PARAS)], PARAS[(i + 9) % len(PARAS)]] for i in range(len(PARAS))], 'Attributi'),
    report('rt-para-pages', ['N', 'Testo'], [[str(i), PARAS[i % len(PARAS)]] for i in range(120)], 'Attributi su pagine'),
    report('rt-para-text-cut', ['Nome'], [['x']], 'Tagliato', text_before='<para backcolor="#eeeeee" size="10" leading="13">' + 'testo con sfondo ' * 700 + '</para>',
           text_after='<para allowwidows="0" align="right">' + 'riga destra ' * 500 + '</para>'),
    report('rt-para-text-orphans', ['Nome'], [['x']], 'Orfane', text_before='<para alloworphans="1" allowwidows="0">' + 'riga ' * 900 + '</para>'),
    attendance('at-para', ('<para color="red">Yoga</para>', ['01/01/2026', '<para align="right">x</para>'],
                           [('<para size="8">Rossi</para>', ['P', 'A'])]), ('Nuoto', ['02/01/2026'], [('Neri', ['P'])])),
]

# Placed, justified, bulleted, framed, end-dotted and case-transformed paragraphs: the attributes of <para> and the
# <bullet> tag, in the header, in cells, cut over pages and in additional rows.
PLACED = [
    '<para lindent="18">' + 'testo lungo che va a capo ' * 8 + '</para>',
    '<para rindent="24">' + 'testo lungo che va a capo ' * 8 + '</para>',
    '<para findent="18">' + 'testo lungo che va a capo ' * 8 + '</para>',
    '<para lindent="18" rindent="12" findent="-9">' + 'testo lungo che va a capo ' * 8 + '</para>',
    '<para lindent="1cm" findent="0.5cm">a b c</para>', '<para lindent="+6" rindent="+3">relativi</para>',
    '<para align="justify">' + 'giustificato con più parole ' * 6 + '</para>',
    '<para align="justify">giu <b>sti</b> fi <i>ca</i> to</para>',
    '<para align="justify" justifylastline="2">' + 'ultima riga giustificata ' * 4 + '</para>',
    '<para align="justify">a<br/>b<br/>c</para>', '<para align="justify" justifybreaks="1">a<br/>b<br/>c</para>',
    '<para align="justify">a&nbsp;b c&nbsp;d e&nbsp;f g</para>',
    '<para align="justify"><u>sotto</u> <a href="https://example.test/j">link</a> e <img src="%s"/> immagine</para>' % PIX,
    '<para align="justify"><img src="%s" width="10" height="7"/> giustificato con immagine</para>' % PIX,
    '<bullet>*</bullet>' + 'voce lunga che va a capo ' * 6,
    '<para lindent="18" findent="-9"><bullet>*</bullet>' + 'voce lunga che va a capo ' * 6 + '</para>',
    '<para lindent="18" findent="-9"><bullet><b>1.</b></bullet>' + 'prima voce della lista ' * 4 + '</para>',
    '<para lindent="18" findent="-9"><bullet anchor="end">*</bullet>pallino a fine</para>',
    '<para lindent="18" findent="-9"><bullet anchor="middle">*</bullet>pallino al centro</para>',
    '<para lindent="18" findent="-9"><bullet anchor="numeric">9.9</bullet>numerico</para>',
    '<para lindent="18" findent="-9" bindent="4"><bullet>*</bullet>rientrato</para>',
    '<para lindent="18" findent="-9" bcolor="red" bfont="Times-Bold" bfontsize="9"><bullet>x</bullet>rosso</para>',
    '<para lindent="6" findent="0" boffsety="3"><bullet>**</bullet>pallino più largo del rientro</para>',
    '<para lindent="4"><bullet>***</bullet>largo</para>',
    '<para borderwidth="1">bordo</para>', '<para borderwidth="2" bordercolor="blue">bordo blu</para>',
    '<para borderwidth="1" borderpadding="4" bordercolor="#008000">padding</para>',
    '<para borderwidth="1" borderpadding="3" borderradius="5" bordercolor="red">angoli</para>',
    '<para borderwidth="1" borderpadding="6" bordercolor="black" backcolor="#eeeeee">' + 'bordo e sfondo ' * 6 + '</para>',
    '<para lindent="12" rindent="6" borderwidth="1" borderpadding="2" bordercolor="blue">' + 'rientrato e bordato ' * 5 + '</para>',
    '<para autoleading="min">piccolo <font size="18">grande</font> e <font size="6">minuto</font> testo</para>',
    '<para autoleading="max">' + 'solo una font e testo lungo ' * 8 + '</para>',
    '<para autoleading="min">' + 'solo una font e testo lungo ' * 8 + '</para>',
    '<para autoleading="max">piccolo <font size="18">grande</font> e <img src="%s"/> testo</para>' % PIX,
    '<para autoleading="min">' + 'righe con immagini <img src="%s" width="10" height="8"/> e testo ' % PIX * 6 + '</para>',
    '<para autoleading="max" lindent="12">' + 'righe allargate e rientrate ' * 8 + '</para>',
    '<para enddots=".">' + 'punti finali a sinistra ' * 4 + '</para>',
    '<para enddots="." align="center">' + 'punti finali al centro ' * 4 + '</para>',
    '<para enddots="." align="right">' + 'punti finali a destra ' * 4 + '</para>',
    '<para enddots="." align="justify">' + 'punti finali giustificati ' * 4 + '</para>',
    '<para enddots=" ." backcolor="#ffffcc">' + 'sfondo e punti ' * 4 + '</para>',
    '<para enddots="…">puntini ' + 'di sospensione ' * 5 + '</para>',
    '<para texttransform="uppercase">Straße città <b>ﬁne</b> İstanbul</para>',
    '<para texttransform="lowercase">STRASSE CITTÀ <b>FINE</b> İSTANBUL</para>',
    '<para texttransform="capitalize">hello world of <b>titles and</b> ΣΟΣΟΣ words</para>',
    '<para texttransform="none">Mixed <b>Case</b></para>',
    '<para texttransform="uppercase" align="center">a <i>b</i> c</para>',
]
# The colour model in the attributes of this port: CMYK, PCMYK and alpha colours and Blacker/Whiter as the paragraph
# background, the border, the bullet and the end dots (reportlab draws each through setFillColor/setStrokeColor, the
# separations and the ExtGState alpha included).
COLOURED = [
    '<para bordercolor="cmyk(0,1,0,0)" borderwidth="1" backcolor="rgba(255,0,0,0.5)"><bullet color="cmyk(0,0,1,0)">*</bullet>' +
    'bordo ciano e sfondo rosso trasparente ' * 5 + '</para>',
    '<para backcolor="pcmyk(20,0,0,0)">' + 'sfondo pcmyk ' * 6 + '</para>',
    '<para bordercolor="cmyk(0,0,1,0)" borderwidth="2" borderpadding="3">bordo giallo</para>',
    '<para bordercolor="rgba(0,0,255,0.25)" borderwidth="1">bordo blu trasparente</para>',
    '<para backcolor="Blacker(blue,0.3)">sfondo scuro</para>',
    '<para backcolor="Whiter(green,0.5)">sfondo chiaro</para>',
    '<para backcolor="cmyk(0,0,0,0.2)" borderwidth="1" bordercolor="rgba(0,128,0,0.5)" borderradius="4">' +
    'bordo e sfondo ' * 5 + '</para>',
    '<para bcolor="Blacker(red,0.5)" lindent="18" findent="-9"><bullet>*</bullet>pallino scuro</para>',
    '<para bcolor="rgba(0,0,255,0.5)" bindent="4"><bullet>*</bullet>pallino trasparente</para>',
    '<para banchor="end" bcolor="cmyk(0,1,0,0)" bindent="6"><bullet>1.</bullet>numeric</para>',
    '<para enddots="." color="cmyk(0,1,0,0)">' + 'punti ciano ' * 6 + '</para>',
    '<para enddots="." color="rgba(0,0,0,0.4)" align="right">' + 'punti trasparenti ' * 6 + '</para>',
    '<para color="pcmyk(100,0,0,0)" enddots="." align="center">' + 'punti pcmyk ' * 5 + '</para>',
    '<para enddots=" ." color="cmyk(0,0,0,0.3)" backcolor="Whiter(yellow,0.6)">' + 'sfondo e punti ' * 5 + '</para>',
]
# The numbers of the paragraph attributes this port added, written the ways Python's int() and float() read them: the
# Unicode digits and spaces, an underscore group, relative values, an exact int beyond 2**53 — and the values that fail
# the build (a hex literal int() refuses, and the non-finite numbers fp_str cannot format).
NUMBERED = [
    '<para lindent="١٢">' + 'cifre unicode rientrate ' * 6 + '</para>',
    '<para findent="۱۵" lindent="١٢">a capo rientrato con cifre</para>',
    '<para borderwidth="٢" borderpadding="٣" bordercolor="red">' + 'bordo con cifre unicode ' * 5 + '</para>',
    '<para lindent="1_0" findent="-4"><bullet indent="2">*</bullet>cifre raggruppate</para>',
    '<para lindent="9007199254740993" borderwidth="9007199254740993">oltre 2**53</para>',
    '<para lindent="+1_0" rindent="+٢">relativi</para>',
    '<para borderradius="9007199254740993" borderwidth="1">raggio grande</para>',
    '<para justifylastline="1_0" align="justify">' + 'ultima riga giustificata ' * 12 + '</para>',
    '<para justifylastline="9007199254740993" align="justify">' + 'tutte le righe giustificate ' * 12 + '</para>',
    '<para><bullet size="١٢" indent="٣" offsety="١">*</bullet>pallino con cifre unicode</para>',
    '<para lindent="١٢" findent="-٦"><bullet size="9007199254740993">*</bullet>pallino enorme</para>',
]
CASES += [
    balance('bs-placed', header(None, '<para lindent="12" align="justify">ASD Aurora club con rientro e giustificato</para>',
                                city='Roma', address='Via Roma 1')),
    balance('bs-bullet', header(None, '<para lindent="14" findent="-8"><bullet>*</bullet>ASD Aurora club e società</para>',
                                city='', address='')),
    balance('bs-border', header(None, '<para borderwidth="1" borderpadding="2" bordercolor="red">ASD Aurora club</para>')),
    balance('bs-auto-leading', header(None, '<para autoleading="max">ASD <font size="24">Aurora</font> club con testo lungo</para>')),
    balance('bs-end-dots', header(None, '<para enddots="." align="center">ASD Aurora club e società sportiva</para>')),
    balance('bs-text-transform', header(None, '<para texttransform="uppercase">ASD <b>aurora</b> straße</para>')),
    with_additional(balance('bs-placed-additional', header(None, 'ASD Aurora')),
                    [['balance_at_date', '<para lindent="12" findent="-6"><bullet>*</bullet>' + 'I saldi sono riportati al 31/12/2025. ' * 9 + '</para>'],
                     ['altro', '<para align="justify" enddots=".">' + 'I saldi sono riportati &amp; al 31/12/2025. ' * 9 + '</para>'],
                     ['solo', '<para borderwidth="1" borderpadding="3" borderradius="4" bordercolor="blue">' + 'bordo ' * 40 + '</para>'],
                     ['punti', '<para endDots="." texttransform="uppercase" autoleading="max">' + 'maiuscolo ' * 40 + '</para>']]),
    report('rt-placed-cells', ['<para lindent="6">Nome</para>', 'Testo', 'Note'],
           [[PLACED[i], PLACED[(i + 5) % len(PLACED)], PLACED[(i + 9) % len(PLACED)]] for i in range(len(PLACED))], 'Posizioni'),
    report('rt-placed-squeezed', ['<para lindent="3">Colonna %d</para>' % i for i in range(12)],
           [[PLACED[(i * 5 + j) % len(PLACED)] for j in range(12)] for i in range(len(PLACED))], 'Posizioni strette'),
    report('rt-placed-pages', ['N', 'Testo'], [[str(i), PLACED[i % len(PLACED)]] for i in range(120)], 'Posizioni su pagine'),
    report('rt-placed-text-cut', ['Nome'], [['x']], 'Tagliato',
           text_before='<para lindent="18" rindent="12" findent="-9">' + 'testo rientrato che va a capo e si divide ' * 200 + '</para>',
           text_after='<para align="justify">' + 'riga giustificata<br/>' * 200 + '</para>'),
    report('rt-bullet-split', ['Nome'], [['x']], 'Pallino',
           text_before='<para lindent="18" findent="-9"><bullet>*</bullet>' + 'voce della lista che va a capo ' * 120 + '</para>'),
    report('rt-justify-split', ['Nome'], [['x']], 'Giustificato',
           text_before='<para align="justify">' + 'giustificato su più righe e pagine ' * 150 + '</para>',
           text_after='<para align="justify" enddots=".">' + 'altra giustificazione ' * 60 + '</para>'),
    report('rt-justify-break-cut', ['Nome'], [['x']], 'Giustificato a capo',
           text_before='<para align="justify">' + 'linea giustificata<br/>' * 400 + '</para>',
           text_after='<para align="justify" justifybreaks="1">' + 'rotto<br/>' * 120 + '</para>'),
    report('rt-justify-frags-split', ['Nome'], [['x']], 'Giustificato con frammenti',
           text_before='<para align="justify">' + 'giu <b>sti</b> fi <i>ca</i> to ' * 400 + '</para>',
           text_after='<para align="justify"><u>sotto</u> e <a href="https://example.test/k">link</a> ' * 120 + '</para>'),
    report('rt-enddots-break-split', ['Nome'], [['x']], 'Punti a capo',
           text_before='<para align="right" enddots=".">' + 'riga con punti<br/>' * 200 + '</para>'),
    report('rt-bullet-text-whole', ['Nome'], [['x']], 'Pallino intero',
           text_before='<para lindent="18" findent="-9" autoleading="max"><bullet>1.</bullet>' +
                       'voce <font size="14">alta</font> della lista ' * 300 + '</para>'),
    report('rt-auto-leading-split', ['Nome'], [['x']], 'Interlinea',
           text_before='<para autoleading="max">' + 'grande <font size="20">testo</font> e piccolo ' * 80 + '</para>'),
    report('rt-auto-leading-simple-split', ['Nome'], [['x']], 'Interlinea semplice',
           text_before='<para autoleading="min">' + 'testo con interlinea minima ' * 200 + '</para>',
           text_after='<para autoleading="max">' + 'testo con interlinea massima ' * 200 + '</para>'),
    report('rt-border-split', ['Nome'], [['x']], 'Bordo',
           text_before='<para borderwidth="1" borderpadding="3" bordercolor="blue">' + 'bordato su più pagine ' * 150 + '</para>'),
    report('rt-placed-errors', ['A'], [['x']], 'Errori', text_after='<para lindent="x">a</para>'),
    # The numbers of the style now that every attribute is a Python number: Unicode digits, an underscore group, a hex
    # literal int() refuses, exact ints beyond 2**53, and the non-finite values fp_str cannot format.
    report('rt-numbered-cells', ['<para lindent="١٢">Nome</para>', 'Testo', 'Note'],
           [[NUMBERED[i], NUMBERED[(i + 5) % len(NUMBERED)], NUMBERED[(i + 9) % len(NUMBERED)]] for i in range(len(NUMBERED))],
           'Numeri'),
    report('rt-numbered-text', ['Nome'], [['x']], 'Numeri',
           text_before='<para lindent="١٢" rindent="٨">' + 'testo rientrato con cifre unicode ' * 40 + '</para>',
           text_after='<para findent="1_0" lindent="١٢">' + 'cifre raggruppate ' * 40 + '</para>'),
    report('rt-numbered-justify', ['Nome'], [['x']], 'Numeri',
           text_before='<para align="justify" justifylastline="9007199254740993">' + 'ultima riga giustificata ' * 60 + '</para>',
           text_after='<para align="justify" justifylastline="1_0">' + 'altra ultima riga ' * 60 + '</para>'),
    report('rt-numbered-bullet', ['Nome'], [['x']], 'Numeri',
           text_before='<para lindent="١٢" findent="-٦"><bullet size="١٢" indent="٣" offsety="١">*</bullet>' +
                       'pallino con cifre unicode ' * 40 + '</para>'),
    report('rt-numbered-nan', ['Nome'], [['x']], 'Numeri', text_before='<para lindent="nan">non finito</para>'),
    report('rt-numbered-inf', ['Nome'], [['x']], 'Numeri', text_before='<para borderwidth="inf" bordercolor="blue">x</para>'),
    report('rt-numbered-overflow', ['Nome'], [['x']], 'Numeri', text_before='<para lindent="1e400">oltre il range</para>'),
    report('rt-numbered-hex', ['Nome'], [['x']], 'Numeri', text_before='<para lindent="0x10">esadecimale</para>'),
    report('rt-numbered-padding-nan', ['Nome'], [['x']], 'Numeri',
           text_before='<para borderwidth="1" borderpadding="nan" bordercolor="red">x</para>'),
    report('rt-numbered-negative-overflow', ['Nome'], [['x']], 'Numeri', text_before='<para lindent="-1e400">oltre il range</para>'),
    # A relative border radius is added to the style's None (reportlab's TypeError).
    report('rt-numbered-radius-relative', ['Nome'], [['x']], 'Numeri',
           text_before='<para borderradius="+3" borderwidth="1">raggio relativo</para>'),
    report('rt-numbered-bullet-big', ['Nome'], [['x']], 'Numeri',
           text_before='<para><bullet size="9007199254740993">*</bullet>pallino enorme</para>'),
    report('rt-numbered-bullet-nan', ['Nome'], [['x']], 'Numeri', text_before='<para><bullet size="nan">*</bullet>pallino nan</para>'),
    balance('bs-numbered', header(None, '<para lindent="١٢" borderwidth="٢"><bullet size="١٢" indent="٣">*</bullet>ASD Aurora</para>')),
    balance('bs-numbered-big', header(None, '<para lindent="9007199254740993">ASD Aurora oltre 2**53</para>')),
    balance('bs-numbered-nan', header(None, '<para lindent="nan">ASD Aurora</para>')),
    report('rt-colour-marks-cells', ['<para bordercolor="cmyk(0,1,0,0)" borderwidth="1">Colore</para>', 'Testo', 'Note'],
           [[COLOURED[i], COLOURED[(i + 5) % len(COLOURED)], COLOURED[(i + 9) % len(COLOURED)]] for i in range(len(COLOURED))],
           'Colori'),
    report('rt-colour-marks-text', ['Nome'], [['x']], 'Colori',
           text_before='<para bordercolor="cmyk(0,1,0,0)" borderwidth="1" backcolor="rgba(255,0,0,0.5)">' +
                       '<bullet color="Blacker(red,0.5)">*</bullet>' + 'bordo ciano e sfondo trasparente ' * 40 + '</para>',
           text_after='<para bcolor="cmyk(0,0,1,0)" lindent="18" findent="-9"><bullet>*</bullet>' +
                      'pallino giallo su più righe ' * 40 + '</para>'),
    report('rt-colour-marks-pages', ['N', 'Testo'], [[str(i), COLOURED[i % len(COLOURED)]] for i in range(90)], 'Colori su pagine'),
    report('rt-colour-marks-enddots', ['Nome'], [['x']], 'Colori',
           text_before='<para enddots="." color="cmyk(0,1,0,0)">' + 'punti in un paragrafo ciano ' * 80 + '</para>',
           text_after='<para enddots=" ." color="rgba(0,0,0,0.4)" backcolor="pcmyk(0,0,20,0)">' + 'punti trasparenti ' * 60 + '</para>'),
    report('rt-colour-marks-cut', ['Nome'], [['x']], 'Colori',
           text_before='<para bordercolor="rgba(255,0,0,0.5)" borderwidth="2" backcolor="cmyk(0,0,0,0.1)">' + 'bordato su più pagine ' * 150 + '</para>'),
    balance('bs-colour-marks', header(None, '<para bordercolor="cmyk(0,1,0,0)" borderwidth="1" backcolor="rgba(255,0,0,0.5)" ' +
                                             'bcolor="Blacker(red,0.5)"><bullet>*</bullet>ASD Aurora</para>')),
    balance('bs-colour-marks-alpha', header(None, '<para backcolor="rgba(0,0,255,0.3)" bordercolor="cmyk(0,0,1,0)" borderwidth="2">' +
                                                  'ASD <font color="cmyk(1,0,0,0)">Aurora</font> club</para>')),
    attendance('at-colour-marks', ('<para backcolor="rgba(0,255,0,0.4)" bcolor="cmyk(0,1,0,0)"><bullet>*</bullet>Yoga</para>',
                                   ['01/01/2026'], [('<para bordercolor="Blacker(blue,0.5)" borderwidth="1">Rossi</para>', ['P'])]),
               ('Nuoto', ['02/01/2026'], [('Neri', ['P'])])),
    attendance('at-numbered', ('<para lindent="١٢"><bullet size="١٢">*</bullet>Yoga</para>', ['01/01/2026'],
                               [('<para borderwidth="١">Rossi</para>', ['P'])]), ('Nuoto', ['02/01/2026'], [('Neri', ['P'])])),
    # An anchor inside a justified, an indented, a bulleted and an autoLeading paragraph, with the # link that names it.
    report('rt-anchor-justify', ['Nome'], [['x']], 'Ancora giustificata',
           text_before='<para align="justify">' + 'giustificato con <a name="g1"/>ancora e testo ' * 60 + '</para>',
           text_after='<para align="justify">Torna <a href="#g1">alla prima</a> e ancora.</para>'),
    report('rt-anchor-indented', ['Nome'], [['x']], 'Ancora rientrata',
           text_before='<para lindent="18" findent="-9">' + 'rientrato con <a name="i1"/>ancora ' * 60 + '</para>',
           text_after='<para>Vai <a href="#i1">qui</a>.</para>'),
    report('rt-anchor-bullet', ['Nome'], [['x']], 'Ancora con pallino',
           text_before='<para lindent="18" findent="-9" align="justify"><bullet>*</bullet>' +
                       'pallino con <a name="b1"/>ancora ' * 60 + '</para>',
           text_after='<para>Vai <a href="#b1">qui</a>.</para>'),
    report('rt-anchor-autoleading', ['Nome'], [['x']], 'Ancora interlinea',
           text_before='<para autoleading="max">' + 'interlinea con <a name="l1"/>ancora e <font size="20">grande</font> ' * 40 + '</para>',
           text_after='<para autoleading="min">Vai <a href="#l1">qui</a> con <font size="16">grande</font>.</para>'),
    report('rt-anchor-enddots', ['Nome'], [['x']], 'Ancora punti',
           text_before='<para enddots="." align="right">punti con <a name="e1"/>ancora ' * 30 + '</para>',
           text_after='<para>Vai <a href="#e1">qui</a>.</para>'),
    attendance('at-placed', ('<para lindent="6"><bullet>*</bullet>Yoga</para>', ['01/01/2026', '<para align="right">x</para>'],
                             [('<para texttransform="uppercase">Rossi</para>', ['P', '<para enddots=".">A</para>'])]),
               ('Nuoto', ['02/01/2026'], [('Neri', ['P'])])),
]

# Random mixtures of the tags above (a fixed seed): lines, fonts, sizes, colours, rises, links, images and breaks in one text.
import random  # noqa: E402

FUZZ_WORDS = ['il', 'corso', 'di', 'nuoto', 'Supercalifragilistichespiralidoso', 'a', 'b', '&amp;', 'poli\xadsportiva', 'x' * 30, '1,00', 'è']
FUZZ_OPEN = ['<b>', '<i>', '<u>', '<strike>', '<font size="12">', '<font size="6" color="#ff0000">', '<font bgcolor="yellow">', '<sup>', '<sub>',
             '<a href="https://example.test/f">', '<u color="blue" kind="double">', '<span color="green">', '<nobr>', '<font name="Symbol">']
FUZZ_CLOSE = {'<b>': '</b>', '<i>': '</i>', '<u>': '</u>', '<strike>': '</strike>', '<font size="12">': '</font>',
              '<font size="6" color="#ff0000">': '</font>', '<font bgcolor="yellow">': '</font>', '<sup>': '</sup>', '<sub>': '</sub>',
              '<a href="https://example.test/f">': '</a>', '<u color="blue" kind="double">': '</u>', '<span color="green">': '</span>',
              '<nobr>': '</nobr>', '<font name="Symbol">': '</font>'}


def fuzz_text(rng, words):
    out, stack = [], []
    for _ in range(words):
        roll = rng.random()
        if roll < 0.18:
            tag = rng.choice(FUZZ_OPEN)
            stack.append(tag)
            out.append(tag)
        elif roll < 0.34 and stack:
            out.append(FUZZ_CLOSE[stack.pop()])
        elif roll < 0.38:
            out.append('<br/>')
        elif roll < 0.42:
            out.append('<img src="%s" width="%d" height="%d"/>' % (rng.choice([PIX, GREY, TALL]), rng.choice([6, 12, 20]), rng.choice([5, 9, 14])))
        else:
            out.append(rng.choice(FUZZ_WORDS))
    while stack:
        out.append(FUZZ_CLOSE[stack.pop()])
    return ' '.join(out)


for seed in range(6):
    fuzz = random.Random(20261111 + seed)
    CASES.append(report('rt-fuzz-%d' % seed, ['N', 'Testo', 'Altro'],
                        [[str(i), fuzz_text(fuzz, fuzz.randint(3, 40)), fuzz_text(fuzz, fuzz.randint(1, 12))] for i in range(60)], 'Miscela %d' % seed,
                        text_before=fuzz_text(fuzz, 900), text_after=fuzz_text(fuzz, 300)))


# Greek text, single characters by code, and the Courier and Times faces.
GREEKED = 'il <greek>abgde</greek> di <font name="Times-Italic">nuoto</font> e <font name="Courier">pallanuoto</font> <unichar code="0x263A"/>'
GREEK_CELLS = [
    '<greek>abgde</greek>', '<greek>ABC</greek> <greek>xyz</greek>', 'a<greek>ab</greek>b', '<b><greek>de</greek></b>',
    '<unichar code="65"/> <unichar code="0x263A"/> <unichar code="233"/>', 'a<unichar code="66"/>c', GREEKED,
    '<font name="Courier">Courier</font> <font name="Courier-Bold">grassetto</font>', '<font name="Courier-Oblique">corsivo</font> a',
    '<font name="Times-Roman">Romano</font> <font name="Times-Bold">grassetto</font>', '<font name="Times-Italic">corsivo</font> <font name="Times-BoldItalic">entrambi</font>',
    '<para font="Times-Bold">paragrafo Times</para>', 'testo <font name="Times-Roman">città è €</font> fine',
    '<greek>' + 'abgde' * 10 + '</greek>', '<font name="Courier">' + 'poli\xadsportiva ' * 3 + '</font>',
]
CASES += [
    balance('bs-greek-unichar', header(None, 'ASD <greek>abg</greek> <unichar code="0x263A"/> club', city='<greek>de</greek>',
                                       address='<unichar code="233"/> <font name="Times-Italic">Via Roma</font> 1')),
    balance('bs-greek-unichar-long', header(None, ' '.join([GREEKED] * 4), city=GREEKED, address='<greek>' + LONG + '</greek>')),
    balance('bs-times-courier', header(None, '<font name="Times-Bold">ASD Aurora</font>', city='<font name="Courier">Roma</font>',
                                       address='<font name="Times-Italic" size="12">Via Roma 1</font>')),
    balance('bs-greek-unclosed', header(None, 'ASD <greek>Aurora')),
    balance('bs-unichar-code-error', header(None, 'ASD <unichar code="x"/> Aurora')),
    balance('bs-greek-non-latin1', header(None, 'ASD <greek>日</greek> Aurora')),
    balance('bs-index-error', header(None, 'ASD <index item="a"/> Aurora')),
    balance('bs-ondraw-error', header(None, 'ASD <onDraw name="a"/> Aurora')),
    with_additional(balance('bs-greek-additional', header(None, 'ASD Aurora')),
                    [['balance_at_date', 'I saldi <greek>abg</greek> sono riportati.'], ['altro', ' '.join([GREEKED] * 4)],
                     ['solo', '<font name="Courier">' + 'tutto a spaziatura fissa ' * 20 + '</font>']]),
    report('rt-greek-cells', ['<greek>ab</greek>', 'Testo', 'Note'],
           [[GREEK_CELLS[i], GREEK_CELLS[(i + 5) % len(GREEK_CELLS)], GREEK_CELLS[(i + 9) % len(GREEK_CELLS)]] for i in range(len(GREEK_CELLS))],
           'Greco'),
    report('rt-greek-squeezed', ['<greek>ab</greek> %d' % i for i in range(12)],
           [[GREEK_CELLS[(i * 5 + j) % len(GREEK_CELLS)] for j in range(12)] for i in range(len(GREEK_CELLS))], 'Greco stretto'),
    report('rt-greek-pages', ['N', 'Testo'], [[str(i), ' '.join([GREEK_CELLS[i % len(GREEK_CELLS)]] * (1 + i % 4))] for i in range(120)],
           'Greco su pagine'),
    report('rt-greek-text-cut', ['Nome'], [['x']], 'Tagliato', text_before=' '.join([GREEKED] * 130), text_after=' '.join([GREEKED + '<br/>'] * 70)),
    report('rt-times-text-whole', ['Nome'], [['x']], 'Tutto', text_before='<font name="Times-Roman">' + 'testo Times ' * 300 + '</font>',
           text_after='<font name="Courier">' + 'testo Courier ' * 400 + '</font>'),
    report('rt-unichar-cell-error', ['A'], [['<unichar code="1114112"/>']], 'Errore'),
    report('rt-greek-cell-error', ['A'], [['<greek>日</greek>']], 'Errore'),
    attendance('at-greek', ('Yoga <greek>abg</greek>', ['01/01/2026', '<font name="Courier">x</font>'],
                            [('<greek>de</greek> Rossi', ['P', '<unichar code="65"/>'])]), ('Nuoto', ['02/01/2026'], [('Neri', ['P'])])),
]


# <unichar name="..."/>: the character, name alias or named sequence unicodedata.lookup returns, in a header, a cell and
# the text before the table; an unknown name is a syntax error of the paragraph.
UNICHAR_NAMES = ('<unichar name="EURO SIGN"/> <unichar name="LATIN CAPITAL LETTER A WITH MACRON AND GRAVE"/> '
                 '<unichar name="LATIN SMALL LETTER E WITH GRAVE"/>')
CASES += [
    report('rt-unichar-names', ['<unichar name="EURO SIGN"/> Importo', 'Nome'],
           [['<unichar name="LATIN CAPITAL LETTER A WITH MACRON AND GRAVE"/>', '<unichar name="LATIN SMALL LETTER E WITH GRAVE"/>']],
           '<unichar name="EURO SIGN"/> Titolo', text_before=UNICHAR_NAMES),
    report('rt-unichar-name-error', ['A'], [['<unichar name="zzz"/>']], 'Errore'),
]


# /balance-sheet: an income/expense description is any json.loads value; reportlab draws str(value), a list taken for a
# list of flowables (which raises unless it flattens to nothing), in the sheet's portrait A3 layout with its header.
DESCRIBED = [['A) ENTRATE', '', '', ''], ['Entrate Generali', '', '', ''],
             [{'int': '5'}, '1,00', '0,00', '1,00'], [{'float': '1.5'}, '2,00', '0,00', '2,00'],
             [{'bool': True}, '3,00', '0,00', '3,00'], [{'dict': [['k', {'int': '1'}], ['s', 'v']]}, '4,00', '0,00', '4,00'],
             [{'int': '9223372036854775808'}, '5,00', '0,00', '5,00'], ['Totale entrate', '15,00', '0,00', '15,00']]
CASES += [
    balance('bs-description-values', header(None, 'ASD Aurora'), rows=DESCRIBED),
    balance('bs-description-list', header(None, 'ASD Aurora'), rows=DESCRIBED + [[{'list': ['a', {'int': '1'}]}, '', '', '']]),
    balance('bs-description-empty-list', header(None, 'ASD Aurora'), rows=DESCRIBED + [[{'list': []}, '', '', '']]),
]


# Colours beyond plain RGB: the CMYK and PCMYK forms, transparency, the CMYK
# separation colour space, Blacker/Whiter and a literal tuple in text,
# backgrounds, underlines and strikes, and the colours that make reportlab raise
# while it draws.
COLOURED = [
    '<font color="cmyk(1,0,0,0)">ciano</font> e <font color="cmyk(0,0,0,1)">nero</font>',
    '<font color="pcmyk(100,65,0,30)">pcmyk</font> e <font color="pcmyk(0,0,0,100)">pcmyk nero</font>',
    '<font color="rgba(255,0,0,0.5)">alfa</font> e <font color="rgba(0,0,255,0)">chiaro</font>',
    '<font color="cmyka(1,0,0,0,0.5)">alfa cmyk</font> <font color="pcmyka(0,100,0,0,50)">alfa pcmyk</font>',
    '<font backcolor="cmyk(0,0,0,0.2)">sfondo cmyk</font> <font backcolor="rgba(0,128,0,0.3)">sfondo alfa</font>',
    '<u color="rgba(0,0,255,0.5)">sotto alfa</u> <strike color="pcmyk(0,0,0,100)">barrato pcmyk</strike>',
    '<u color="cmyk(0.2,0.3,0.4,0.5)">sotto cmyk</u>',
    '<font color="Blacker(red,0.5)">blacker</font> <font color="Whiter(blue,0.25)">whiter</font>',
    '<font color="Blacker(_CMYK_black,0.4)">blacker cmyk</font> <font color="Whiter(transparent,0.5)">whiter alfa</font>',
    '<font color="(0.2,0.4,0.6)">tupla</font> e <font color="(0.1,0.2,0.3,0.4)">tupla cmyk</font>',
    '<font color="_CMYK_black">nero cmyk</font> <font color="transparent">trasparente</font>',
    '<font color="cmyk(1,0,0,0)" backcolor="rgba(0,0,0,0.2)">combo</font>',
    '<font color="hsl(120,100%,50%)">verde hsl</font> <font color="rgb(10%,20%,30%)">percento</font>',
    '<font color="#123456">esadecimale</font> <font color="Blacker(red,0.5)">b</font>',
]
COLOUR_ROWS = [[COLOURED[i % len(COLOURED)], COLOURED[(i + 7) % len(COLOURED)]] for i in range(len(COLOURED))]
CASES += [
    report('rt-colour-cells', ['<font color="cmyk(1,0,0,0)">Colore</font>', '<font color="rgba(255,0,0,0.5)">Nota</font>'],
           COLOUR_ROWS, 'Colori'),
    report('rt-colour-text', ['Nome'], [['x']], 'Colori',
           text_before=' '.join(COLOURED), text_after='<font color="cmyk(1,0,0,0)">fine</font> e <u color="rgba(0,0,0,0.5)">sotto</u>'),
    report('rt-colour-pages', ['N', 'Testo'], [[str(i), COLOURED[i % len(COLOURED)]] for i in range(120)], 'Colori su pagine',
           text_before=' '.join(COLOURED * 4), text_after='<font color="pcmyk(100,0,0,0)">coda</font>'),
    report('rt-colour-header', ['<font color="cmyk(0,0,1,0)">A</font>'], [['x']], '<font color="rgba(0,0,0,0.5)">Titolo</font>',
           footer_text='<font color="cmyk(1,0,0,0)">piede</font>'),
    report('rt-colour-para', ['A'], [['<para color="cmyk(0,1,0,0)" backcolor="rgba(255,255,0,0.4)" underlinecolor="pcmyk(0,0,100,0)"'
                                    ' strikecolor="cmyk(0,0,0,0.5)">paragrafo <u>sotto</u> <strike>barrato</strike></para>']], 'Paragrafo'),
    # Colours that make reportlab raise while it draws the document.
    report('rt-colour-alpha-error', ['A'], [['<font color="Color(1,0,0,2)">x</font>']], 'Errore'),
    report('rt-colour-list-error', ['A'], [['<font color="fade(CMYKColor(1,0,0,0),[50])">x</font>']], 'Errore'),
    report('rt-colour-dict-error', ['A'], [['<font color="getAllNamedColors()">x</font>']], 'Errore'),
    report('rt-colour-none-error', ['A'], [['<font color="None">x</font>']], 'Errore'),
    report('rt-colour-float-error', ['A'], [['<font color="1.5">x</font>']], 'Errore'),
    report('rt-colour-background-error', ['A'], [['<font backcolor="getAllNamedColors()">x</font>']], 'Errore'),
    report('rt-colour-text-error', ['Nome'], [['x']], 'Errore', text_before='<font color="Color(1,0,0,-1)">x</font>'),
    report('rt-colour-string-background', ['A'], [['<font backcolor="asNative(&quot;red&quot;)">x</font>']], 'Stringa'),
    # A colour value that is false draws no background at all.
    report('rt-colour-empty-background', ['A'], [['<font backcolor="{}">x</font> <font backcolor="--0">y</font>']], 'Vuoto'),
    report('rt-colour-density', ['A'], [['<font color="CMYKColor(0,0,1,0,density=0.5)">densita</font> <font color="pcmyk(0,0,100,0)">pieno</font>']], 'Densita'),
    report('rt-colour-values', ['A'], [['<font color="hsl2rgb(0.5,1,2)">fuori</font> <font color="obj_R_G_B((0.2,0.4,0.6,1))">rgb</font>']], 'Valori'),
    report('rt-colour-string-text-name', ['A'], [['<font color="asNative(&quot;red&quot;)">rosso</font> <u color="Color(0,0,0,0.5)">alfa</u>']], 'Nomi'),
    report('rt-colour-string-text', ['A'], [['<font color="fp_str(1)">x</font>']], 'Stringa'),
    # A separation colour space has a stream without a filter, which TESTS.contracts.pdf
    # cannot record: only the case that raises before it writes one is here.
    report('rt-colour-separation-error', ['A'], [['<font color="CMYKColorSep(1,1,1,0)">x</font>']], 'Errore'),
    # Colour components of every number kind drawn through fp_str, a spotName of
    # another type (ignored by a plain CMYK colour), the \N{...} escapes and the
    # values reportlab raises on while drawing.
    report('rt-colour-numbers', ['A'], [['<font color="Color(100000000000000000000, 0, 0)">grande</font> '
                                        '<font color="Color(100000000000000000001, 0, 0)">arrotondato</font> '
                                        '<font color="Color(True, 0, 0)">vero</font> '
                                        '<font color="CMYKColor(1180591620717411303424,0,0,0)">cmyk grande</font> '
                                        '<font color="Color(0x10,0,0)">sedici</font>']], 'Numeri'),
    report('rt-colour-names-escape', ['A'], [[r"""<font color="'\N{LATIN SMALL LETTER R}ED'">rosso</font> """
                                             r"""<font color="'\N{latin small letter b}lue'">blu</font> """
                                             r"""<font color="'\N{LATIN SMALL LETTER G}REEN'">verde</font>"""]], 'Nomi'),
    report('rt-colour-escape-value-error', ['A'], [[r"""<font color="'\N{VULGAR FRACTION ONE HALF}'">x</font>"""]], 'Errore'),
    report('rt-colour-escape-nul-error', ['A'], [[r"""<font color="'\N{NUL}x'">x</font>"""]], 'Errore'),
    report('rt-colour-spotname', ['A'], [['<font color="CMYKColor(1,0,0,0,spotName=0)">a</font> '
                                          '<font color="PCMYKColor(0,65,100,30,spotName=())">b</font>']], 'Spot'),
    report('rt-colour-escape-error', ['A'], [[r"""<font color="'\N{KEYCAP NUMBER SIGN}'">x</font>"""]], 'Errore'),
    report('rt-colour-spotname-error', ['A'], [['<font color="CMYKColorSep(1,0,0,0,spotName=7)">x</font>']], 'Errore'),
    report('rt-colour-component-error', ['A'], [['<font color="Color(red,0,0)">x</font>']], 'Errore'),
    report('rt-colour-infinite-error', ['A'], [['<font color="Color(1e400,0,0)">x</font>']], 'Errore'),
]


# Inline images whose src names a file, a data: URL or a URL. reportlab's ImageReader reads such a source with
# open_for_read (lib/utils.py): it opens the name as a path (a relative path in the working directory), and when that
# fails it reads a data: URL or fetches the name as a URL. These cases therefore name resources: main() writes the files
# in a temporary directory, which is the working directory while they are built, and serves the URLs from a local
# server. The golden keeps the sources, so the replay writes the same files and serves the same bytes.
IMG_DIR = '$IMG_DIR'
IMG_BASE = '$IMG_BASE'
IMG_LOGO = jpeg(24, 16, (9, 200, 30))
IMG_LOGO_BYTES = base64.b64decode(IMG_LOGO)


def image_source_cases():
    """The <img src> cases of a path, a data: URL and a URL, with the sources they name."""
    sources = {'dir': IMG_DIR, 'base': IMG_BASE,
               'files': [{'path': 'logo.jpg', 'base64': IMG_LOGO}],
               'routes': [{'path': '/logo.jpg', 'base64': IMG_LOGO},
                          {'path': '/missing', 'status': 404},
                          {'path': '/redirect', 'status': 302, 'location': '/logo.jpg'}]}
    cases = []

    def case(name, src):
        cases.append({**report(name, ['Immagine', 'Testo'], [['a <img src="%s"/> b' % src, 'x']], 'Sorgente'),
                      'resources': sources})

    case('img-src-file', 'logo.jpg')
    case('img-src-file-absolute', IMG_DIR + '/logo.jpg')
    case('img-src-file-missing', 'missing.jpg')
    # The header of the balance sheet: the h1 paragraph reads a path that is not a data: URL (the logo pattern of the
    # view reads only a base64 JPEG data URL, so the logo itself is the '[LOGO]' paragraph on both sides).
    cases.append({**balance('img-src-header-file', header(None, 'ASD <img src="' + IMG_DIR + '/logo.jpg"/> Aurora')),
                  'resources': sources})
    case('img-src-url', IMG_BASE + '/logo.jpg')
    case('img-src-url-missing', IMG_BASE + '/missing')
    case('img-src-url-redirect', IMG_BASE + '/redirect')
    case('img-src-file-url', 'file://' + IMG_DIR + '/logo.jpg')
    case('img-src-file-url-missing', 'file://' + IMG_DIR + '/missing.jpg')
    case('img-src-scheme-unknown', 'gopher://127.0.0.1/logo.jpg')
    case('img-src-data-no-media', 'data:;base64,' + IMG_LOGO)
    case('img-src-data-percent', 'data:image/jpeg,' + ''.join('%%%02X' % byte for byte in IMG_LOGO_BYTES))
    case('img-src-data-no-comma', 'data:image/jpeg;base64')
    case('img-src-data-percent-invalid', 'data:,%FF')
    return cases


@contextlib.contextmanager
def image_sources():
    """A working directory holding logo.jpg and a server answering the image routes."""
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == '/logo.jpg':
                self.send_response(200)
                self.send_header('Content-Type', 'image/jpeg')
                self.send_header('Content-Length', str(len(IMG_LOGO_BYTES)))
                self.end_headers()
                self.wfile.write(IMG_LOGO_BYTES)
            elif self.path == '/redirect':
                self.send_response(302)
                self.send_header('Location', '/logo.jpg')
                self.send_header('Content-Length', '0')
                self.end_headers()
            else:
                self.send_response(404)
                self.send_header('Content-Length', '0')
                self.end_headers()

    directory = tempfile.mkdtemp()
    with open(os.path.join(directory, 'logo.jpg'), 'wb') as image:
        image.write(IMG_LOGO_BYTES)
    server = socketserver.TCPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield directory, 'http://127.0.0.1:%d' % server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        shutil.rmtree(directory, ignore_errors=True)


def filled(value, replace):
    """value with every placeholder of replace replaced, in strings, lists and dicts."""
    if isinstance(value, str):
        for old, new in replace.items():
            value = value.replace(old, new)
        return value
    if isinstance(value, list):
        return [filled(item, replace) for item in value]
    if isinstance(value, dict):
        return {key: filled(item, replace) for key, item in value.items()}
    return value


def entry_of(case, replace):
    """The golden entry of a case: built with its sources filled in, recorded with the placeholders, which the replay
    replaces with its own directory and server (the document does not depend on them: images are named after a digest
    of their bytes)."""
    entry = {'name': case['name'], 'spec': case['spec']}
    if 'resources' in case:
        entry['resources'] = case['resources']
    try:
        parts, _, _ = pdf.normalize(pdf.build(filled(case['spec'], replace)))
    except pdf.Malformed:
        raise
    except Exception as error:
        entry['raises'] = type(error).__name__
    else:
        entry['document'] = base64.b64encode(SEPARATOR.join(parts)).decode()
    return entry


def main():
    cases = [entry_of(case, {}) for case in CASES]
    # The image cases read their sources in the working directory of the build, as reportlab does.
    with image_sources() as (directory, base):
        previous = os.getcwd()
        os.chdir(directory)
        try:
            cases += [entry_of(case, {IMG_DIR: directory, IMG_BASE: base}) for case in image_source_cases()]
        finally:
            os.chdir(previous)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({'cases': cases}, ensure_ascii=False, indent=1) + '\n')
    print(len(cases), 'cases,', sum('raises' in case for case in cases), 'raising')


if __name__ == '__main__':
    main()
