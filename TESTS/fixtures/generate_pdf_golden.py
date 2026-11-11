"""Write the golden documents of BE_GO/internal/pdfexport (pdf-exports.md).

Every case is a '$pdf' spec of TESTS/contracts/pdf.py built with reportlab
4.2.5 exactly as the contract builds its expectation; the golden file keeps
the decoded form the opt-in PDF standard compares (TESTS.contracts.pdf
normalize). A case reportlab raises on records the exception class instead.
Run from the repository root with the migration interpreter:
``python -I TESTS/fixtures/generate_pdf_golden.py``.
"""
import base64
import io
import json
import sys
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


def main():
    cases = []
    for case in CASES:
        entry = {'name': case['name'], 'spec': case['spec']}
        try:
            parts, _, _ = pdf.normalize(pdf.build(case['spec']))
        except pdf.Malformed:
            raise
        except Exception as error:
            entry['raises'] = type(error).__name__
        else:
            entry['document'] = base64.b64encode(SEPARATOR.join(parts)).decode()
        cases.append(entry)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({'cases': cases}, ensure_ascii=False, indent=1) + '\n')
    print(len(cases), 'cases,', sum('raises' in case for case in cases), 'raising')


if __name__ == '__main__':
    main()
