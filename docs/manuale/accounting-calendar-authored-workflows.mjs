// Exact authored procedures backed by the existing real scenarios. No import
// invokes a browser, prose builder, hash callback or verification report.
import {accountingBalanceCaptureSpecs, accountingBalanceExpectedOutcomes} from './accounting-balance-recipes.mjs';
import {campsCalendarCaptureSpecs, campsCalendarExpectedOutcomes} from './camps-calendar-recipes.mjs';
import {accountingBalanceSources} from '../../selfhost/tests/browser/manuale/accounting-balance-sources.mjs';
import {campsCalendarSources} from '../../selfhost/tests/browser/manuale/camps-calendar-sources.mjs';
const metadata = {
    "accounting-balance-manage": [
        {
            "path": "docs/contabilita-avanzata.mdx",
            "id": "conti-finanziari",
            "title": "Conti finanziari",
            "numbers": [
                1,
                3,
                5
            ],
            "images": [
                1,
                3,
                5
            ]
        },
        {
            "path": "docs/contabilita-avanzata.mdx",
            "id": "tipologie-di-conto",
            "title": "Tipologie di conto",
            "numbers": [
                2,
                3
            ],
            "images": [
                2
            ]
        },
        {
            "path": "docs/contabilita-avanzata.mdx",
            "id": "creare-un-nuovo-conto",
            "title": "Creare un nuovo conto",
            "numbers": [
                1,
                2,
                3
            ],
            "images": [
                1,
                2,
                3
            ]
        },
        {
            "path": "docs/contabilita-avanzata.mdx",
            "id": "modificare-un-conto",
            "title": "Modificare un conto",
            "numbers": [
                4,
                5,
                18
            ],
            "images": [
                4,
                5,
                18
            ]
        },
        {
            "path": "docs/contabilita-avanzata.mdx",
            "id": "giroconti",
            "title": "Giroconti",
            "numbers": [
                6,
                7,
                8
            ],
            "images": [
                6,
                7,
                8
            ]
        },
        {
            "path": "docs/contabilita-avanzata.mdx",
            "id": "creare-un-giroconto",
            "title": "Creare un giroconto",
            "numbers": [
                6,
                7,
                8
            ],
            "images": [
                6,
                7,
                8
            ]
        },
        {
            "path": "docs/contabilita-avanzata.mdx",
            "id": "eliminare-un-giroconto",
            "title": "Eliminare un giroconto",
            "numbers": [
                16,
                17
            ],
            "images": [
                16,
                17
            ]
        },
        {
            "path": "docs/bilancio.mdx",
            "id": "bilancio",
            "title": "Bilancio",
            "numbers": [
                9,
                10,
                11
            ],
            "images": [
                9,
                10,
                11
            ]
        },
        {
            "path": "docs/bilancio.mdx",
            "id": "conti-economici",
            "title": "Conti economici",
            "numbers": [
                1,
                2,
                3
            ],
            "images": [
                1,
                2,
                3
            ]
        },
        {
            "path": "docs/bilancio.mdx",
            "id": "giroconti",
            "title": "Giroconti",
            "numbers": [
                6,
                7,
                8,
                16,
                17
            ],
            "images": [
                6,
                7,
                8,
                16,
                17
            ]
        },
        {
            "path": "docs/bilancio.mdx",
            "id": "pubblicazione-del-bilancio",
            "title": "Pubblicazione del bilancio",
            "numbers": [
                11,
                12,
                13,
                14,
                15
            ],
            "images": [
                11,
                12,
                13,
                14,
                15
            ]
        },
        {
            "path": "faq/come-generare-bilancio.mdx",
            "id": "cos-e-il-bilancio",
            "title": "Cos'è il bilancio",
            "numbers": [
                11
            ],
            "images": [
                11
            ]
        },
        {
            "path": "faq/come-generare-bilancio.mdx",
            "id": "configurare-i-conti-economici",
            "title": "Configurare i conti economici",
            "numbers": [
                1,
                2,
                3,
                4,
                5
            ],
            "images": [
                1,
                2,
                3,
                4,
                5
            ]
        },
        {
            "path": "faq/come-generare-bilancio.mdx",
            "id": "i-giroconti-trasferimenti-tra-conti",
            "title": "I giroconti (trasferimenti tra conti)",
            "numbers": [
                6,
                7,
                8,
                16,
                17
            ],
            "images": [
                6,
                7,
                8,
                16,
                17
            ]
        },
        {
            "path": "faq/come-generare-bilancio.mdx",
            "id": "generare-e-consultare-il-bilancio",
            "title": "Generare e consultare il bilancio",
            "numbers": [
                9,
                10,
                11
            ],
            "images": [
                9,
                10,
                11
            ]
        },
        {
            "path": "faq/come-generare-bilancio.mdx",
            "id": "pubblicare-il-bilancio",
            "title": "Pubblicare il bilancio",
            "numbers": [
                11,
                12,
                13,
                14,
                15
            ],
            "images": [
                11,
                12,
                13,
                14,
                15
            ]
        }
    ],
    "camps-calendar-manage": [
        {
            "path": "docs/camp-e-ritiri.mdx",
            "id": "cos-e-un-camp-o-ritiro",
            "title": "Cos'è un Camp o Ritiro",
            "numbers": [
                1
            ],
            "images": [
                1
            ]
        },
        {
            "path": "docs/camp-e-ritiri.mdx",
            "id": "creare-un-camp-o-ritiro",
            "title": "Creare un Camp o Ritiro",
            "numbers": [
                1,
                2,
                3
            ],
            "images": [
                1,
                2,
                3
            ]
        },
        {
            "path": "docs/camp-e-ritiri.mdx",
            "id": "gestire-i-periodi",
            "title": "Gestire i periodi",
            "numbers": [
                6,
                7,
                13
            ],
            "images": [
                6,
                7,
                13
            ]
        },
        {
            "path": "docs/camp-e-ritiri.mdx",
            "id": "aggiungere-un-periodo",
            "title": "Aggiungere un periodo",
            "numbers": [
                4,
                5
            ],
            "images": [
                4,
                5
            ]
        },
        {
            "path": "docs/camp-e-ritiri.mdx",
            "id": "modificare-un-periodo",
            "title": "Modificare un periodo",
            "numbers": [
                6,
                7,
                13
            ],
            "images": [
                6,
                7,
                13
            ]
        },
        {
            "path": "docs/camp-e-ritiri.mdx",
            "id": "gestire-i-servizi",
            "title": "Gestire i servizi",
            "numbers": [
                8,
                9
            ],
            "images": [
                8,
                9
            ]
        },
        {
            "path": "docs/camp-e-ritiri.mdx",
            "id": "aggiungere-un-servizio",
            "title": "Aggiungere un servizio",
            "numbers": [
                8,
                9
            ],
            "images": [
                8,
                9
            ]
        },
        {
            "path": "docs/camp-e-ritiri.mdx",
            "id": "modificare-o-eliminare-un-servizio",
            "title": "Modificare o eliminare un servizio",
            "numbers": [
                10,
                11,
                12
            ],
            "images": [
                10,
                11,
                12
            ]
        },
        {
            "path": "docs/calendario.mdx",
            "id": "il-calendario-generale",
            "title": "Il calendario generale",
            "numbers": [
                14,
                27
            ],
            "images": [
                14,
                27
            ]
        },
        {
            "path": "docs/calendario.mdx",
            "id": "modalita-di-visualizzazione",
            "title": "Modalità di visualizzazione",
            "numbers": [
                14,
                15,
                16,
                17,
                18
            ],
            "images": [
                14,
                15,
                16,
                17,
                18
            ]
        },
        {
            "path": "docs/calendario.mdx",
            "id": "vista-mese",
            "title": "Vista Mese",
            "numbers": [
                15
            ],
            "images": [
                15
            ]
        },
        {
            "path": "docs/calendario.mdx",
            "id": "vista-settimana",
            "title": "Vista Settimana",
            "numbers": [
                16
            ],
            "images": [
                16
            ]
        },
        {
            "path": "docs/calendario.mdx",
            "id": "vista-giorno",
            "title": "Vista Giorno",
            "numbers": [
                17
            ],
            "images": [
                17
            ]
        },
        {
            "path": "docs/calendario.mdx",
            "id": "vista-lista",
            "title": "Vista Lista",
            "numbers": [
                14,
                18
            ],
            "images": [
                14,
                18
            ]
        },
        {
            "path": "docs/calendario.mdx",
            "id": "navigare-nel-calendario",
            "title": "Navigare nel calendario",
            "numbers": [
                15
            ],
            "images": [
                15
            ]
        },
        {
            "path": "docs/calendario.mdx",
            "id": "creare-un-evento-o-promemoria",
            "title": "Creare un evento o promemoria",
            "numbers": [
                14,
                19,
                20,
                23,
                24,
                25
            ],
            "images": [
                14,
                19,
                20,
                23,
                24,
                25
            ]
        },
        {
            "path": "docs/calendario.mdx",
            "id": "modificare-o-eliminare-un-evento",
            "title": "Modificare o eliminare un evento",
            "numbers": [
                21,
                22,
                28,
                27
            ],
            "images": [
                21,
                22,
                27,
                28
            ]
        },
        {
            "path": "docs/calendario.mdx",
            "id": "esportare-il-calendario",
            "title": "Esportare il calendario",
            "numbers": [
                24
            ],
            "images": [
                24
            ]
        },
        {
            "path": "docs/calendario.mdx",
            "id": "condivisione-del-calendario-con-gli-atleti",
            "title": "Condivisione del calendario con gli atleti",
            "numbers": [
                26
            ],
            "images": [
                26
            ]
        }
    ]
};
const captions = {
    "accounting-balance-manage": [
        "Cassa Aurora e i saldi iniziale e attuale",
        "Tipo Banca selezionato nel nuovo conto",
        "Conto bancario salvato e riletto nell’elenco",
        "Correzione del saldo iniziale nella finestra di modifica",
        "Saldo iniziale corretto del conto bancario",
        "Giroconto con origine e destinazione distinte",
        "Giroconto conservato dopo il ricaricamento",
        "Saldi dopo lo spostamento di 20 euro",
        "Rendiconto, periodo selezionato e scelta dell’anno sociale",
        "Riga manuale con descrizione e importi prima del salvataggio",
        "Riga manuale e importi conservati dopo il ricaricamento",
        "Conferma della pubblicazione con indicazione che è annullabile",
        "Rendiconto pubblicato conservato dopo il ricaricamento",
        "Conferma per annullare la pubblicazione",
        "Bozza ripristinata e riga manuale conservata",
        "Conferma per eliminare il giroconto",
        "Giroconto eliminato e saldi ripristinati",
        "Conto bancario inutilizzato rimosso e cassa iniziale conservata"
    ],
    "camps-calendar-manage": [
        "Elenco Camp e Ritiri e pulsante di creazione",
        "Titolo e descrizione del nuovo ritiro prima della creazione",
        "Camp creato e pagina di gestione dopo il ricaricamento",
        "Intervallo, quota e numero massimo del periodo prima di Crea",
        "Periodo aggiunto e presente dopo il ricaricamento",
        "Nuove date, quota e limite vuoto prima del salvataggio",
        "Periodo aggiornato e conservato dopo il ricaricamento",
        "Servizio Pranzo con costo e causale di entrata prima di Crea",
        "Servizio aggiunto e causale conservata dopo il ricaricamento",
        "Modifica del servizio con i comandi Salva ed Elimina",
        "Nome e costo del servizio aggiornati dopo il ricaricamento",
        "Servizio eliminato e assente dopo il ricaricamento",
        "Periodo consultato dal collaboratore senza i comandi di creazione e salvataggio",
        "Calendario generale nella vista lista giorno e barra dei comandi",
        "Griglia mensile",
        "Griglia settimanale per orari",
        "Griglia del giorno",
        "Lista della settimana",
        "Evento generale con nome, orari e descrizione prima del salvataggio",
        "Evento generale salvato e presente dopo il ricaricamento",
        "Modifica del nome e della descrizione con le date disattivate",
        "Evento aggiornato e presente dopo il ricaricamento",
        "Nuovo evento con il corso Ginnastica per tutti selezionato",
        "Calendario generale con la lezione del corso e l’appuntamento generale",
        "Calendario del corso con la stessa lezione salvata",
        "Finestra Condividi calendario con collegamento e codice per il sito",
        "Evento aperto dal collaboratore con salvataggio ed eliminazione disattivati",
        "Evento generale eliminato e lezione del corso ancora presente"
    ]
};
const common = ['selfhost/tests/browser/manuale/scenario.mjs', 'selfhost/tests/browser/manuale/frame.mjs',
    'selfhost/tests/browser/manuale/redaction.mjs', 'selfhost/tests/browser/playwright.manual.config.mjs',
    'BE/application/management/commands/seed_manuale.py', 'BE/application/management/commands/run_manuale_instance.py',
    'docs/manuale/accounting-calendar-authored-workflows.mjs'];
function spec(id, captures, expected, sources, script, sourceModule, recipeModule, outcomeField) {
    const [prefix, points] = captures[id];
    return {version: 1, script, prefix, pages: [...new Set(metadata[id].map(section => section.path))], sources,
        dependencies: [...common, script, sourceModule, recipeModule],
        checkpoints: points.map((checkpoint, index) => ({id: checkpoint, caption: captions[id][index]})),
        sections: metadata[id].map(section => ({path: section.path, id: section.id, title: section.title,
            checkpoints: section.numbers.map(number => points[number - 1]),
            images: section.images.map(number => ({from: '/' + prefix + number + '.placeholder.svg', checkpoint: points[number - 1]})),
            insert_images: []})), outcome: {field: outcomeField, expected}};
}
export const accountingCalendarAuthoredWorkflows = Object.freeze({
    'accounting-balance-manage': spec('accounting-balance-manage', accountingBalanceCaptureSpecs,
        {...accountingBalanceExpectedOutcomes['accounting-balance-manage'], account_type_options_visible: true,
            cancelled_transfer_delete_preserves_balances: true},
        accountingBalanceSources, 'selfhost/tests/browser/manuale/accounting-balance.mjs',
        'selfhost/tests/browser/manuale/accounting-balance-sources.mjs', 'docs/manuale/accounting-balance-recipes.mjs',
        'accounting_balance_manage'),
    'camps-calendar-manage': spec('camps-calendar-manage', campsCalendarCaptureSpecs,
        {...campsCalendarExpectedOutcomes['camps-calendar-manage'], view_selection_buttons_active: true,
            previous_next_today_navigation_preserves_events: true},
        campsCalendarSources, 'selfhost/tests/browser/manuale/camps-calendar.mjs',
        'selfhost/tests/browser/manuale/camps-calendar-sources.mjs', 'docs/manuale/camps-calendar-recipes.mjs',
        'camps_calendar_manage'),
});
