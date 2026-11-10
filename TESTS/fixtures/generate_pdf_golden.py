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
