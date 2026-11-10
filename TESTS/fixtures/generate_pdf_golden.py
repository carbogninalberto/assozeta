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
