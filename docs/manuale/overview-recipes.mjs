import {registrationFormsSourceContracts} from '../../selfhost/tests/browser/manuale/registration-forms-sources.mjs';
import {registrationFormsReviewedSources} from './registration-forms-recipes.mjs';
// The original introduction structure and cards, with application-grounded links.
export const overviewReviewedSources = Object.freeze({
    "UI/src/App.svelte": "c3205ba026c8defaeb9328e1a238d7d4dda4eefe8d78d4ababe65c1eea2e88c6",
    "UI/src/components/Header.svelte": "6b6d5fe62166d6bb5b9c5ef6810403a2f40f72b56acb11df496cb9a443a11867",
    "UI/src/routes.js": "58bddc98548095f14f39fa3141277de8cd00e2133faf49f6761131b109d254b0",
    "UI/src/components/Sidebar.svelte": "9f8344602ae269b5dbbf90dfd6f75e5154f3d5774ad544ac41105b74af2e333c",
    "UI/src/routes/manuale/Manuale.svelte": "1eace920cdddc1d56458d2a004035d323ea547c483bfc9784b7ed3f9ee57b411",
    "UI/src/routes/manuale/ManualContent.svelte": "37ea373beaa40554b46cc7db3a99db0c64463f79823f53e6fb6e8d18fb0931c6",
    "UI/src/routes/manuale/manualPresentation.js": "c52433eb377246d5e3a05093b78af3b8c1197ab0751b062e7644835c99cf63f4",
    "BE/application/manuale/views.py": "78cbb273fcb121bd9a678dcfe2f5083707f6307971af8d5909e060fb4ed0a0ac"
});
const reviewedFiles = overviewReviewedSources;
const sources = [
    ["UI/src/App.svelte", "{:else if isManualPage}", 20],
    ["UI/src/components/Header.svelte", "aria-label=\"Manuale d'uso\"", 16],
    ['UI/src/routes.js', "'/manuale':", 13],
    ['UI/src/components/Sidebar.svelte', 'id="manuale_assozeta"', 16],
    ['UI/src/routes/manuale/Manuale.svelte', 'async function load()', 40],
    ['UI/src/routes/manuale/ManualContent.svelte', "part.kind === 'cards'", 22],
    ['UI/src/routes/manuale/manualPresentation.js', 'export function manualLinkUrl(', 22],
    ['BE/application/manuale/views.py', 'def manual_sections(', 65],
];
export function overviewDraftPages() {
    const card = (title, icon, href, body) => `<Card title="${title}" icon="${icon}" href="${href}">${body}</Card>`;
    return [{path: 'docs/introduzione.mdx', title: 'Cosa desideri approfondire?', id: 'cosa-desideri-approfondire',
        body: `## Cosa desideri approfondire?

Scegli il capitolo che ti serve. Ogni guida segue le funzioni dell'associazione: prima i dati e le attività, poi pagamenti, documenti e impostazioni.

<Info>Usa **Cerca nel manuale** per trovare una procedura. Un risultato apre il capitolo e raggiunge la sezione pertinente.</Info>

<CardGroup cols={2}>
${[
    ['Bacheca', 'grid-2', 'bacheca', 'Consulta i riquadri e la guida alla personalizzazione della bacheca.'],
    ['Libro Soci', 'users', 'libro-soci', 'Trova le guide per creare, consultare e gestire le iscrizioni.'],
    ['Corsi', 'diagram-cells', 'corsi', 'Leggi come gestire le attività e le iscrizioni ai corsi.'],
    ['Istruttori', 'chalkboard-user', 'istruttori', 'Consulta profili, ore lavorate e riepiloghi dei compensi.'],
    ['Camp e Ritiri', 'campground', 'camp-e-ritiri', 'Trova le guide a periodi, servizi e iscrizioni.'],
    ['Calendario', 'calendar', 'calendario', 'Consulta lezioni, eventi e collegamenti del calendario.'],
    ['Carnet', 'ticket', 'carnet', 'Leggi le guide ai pacchetti di lezioni e alle assegnazioni.'],
    ['Registro Presenze', 'list-check', 'registro-presenze', 'Trova la procedura delle presenze e i controlli sul carnet.'],
    ['Pagamenti', 'wallet', 'pagamenti', 'Consulta creazione, modifica e incasso dei pagamenti.'],
    ['Ricevute', 'file-lines', 'ricevute', 'Leggi le guide a ricevute, numerazione e PDF.'],
    ['Contabilità Avanzata', 'calculator', 'contabilita-avanzata', 'Consulta documenti contabili, conti finanziari e giroconti.'],
    ['Bilancio', 'chart-simple', 'bilancio', 'Leggi preparazione, salvataggio e pubblicazione del bilancio.'],
    ['Comunicazioni', 'party-horn', 'comunicazioni', 'Consulta le guide alle comunicazioni dell’associazione.'],
    ['Archivio', 'box-archive', 'archivio', 'Trova consultazione e ripristino dei dati archiviati.'],
    ['Collaboratori', 'users-gear', 'collaboratori', 'Consulta ruoli, permessi e limiti degli inviti.'],
    ['Impostazioni', 'gear', 'impostazioni', 'Leggi le guide alla configurazione dell’organizzazione.'],
].map(values => card(...values)).join('\n')}
</CardGroup>`},
    {path: 'docs/introduzione.mdx', title: 'Manuale', id: 'manuale', body: `## Manuale

Il **Manuale d’uso** si apre in una nuova scheda, in una pagina dedicata con la propria navigazione. I capitoli conservano l'ordine delle guide originali; puoi passare dalla documentazione alle FAQ e ai tutorial.

Quando un risultato è disponibile, trovi testo, passaggi e immagini della sezione. I collegamenti alle altre guide aprono il lettore integrato.

<Note>Se il manuale verificato non è disponibile per questa versione, il lettore mostra il relativo messaggio. Le guide prive di prove compatibili non diventano procedure da seguire.</Note>`},
    {path: 'docs/introduzione.mdx', title: 'Per iniziare', id: 'per-iniziare', status: 'pending',
        reason: 'The initial registration-template setup requires its passed real workflow before procedural publication.',
        body: "## Per iniziare\n\nPrepara il modulo prima di raccogliere nuove iscrizioni. Questo percorso parte da un’associazione già configurata e mostra una configurazione semplice: **Socio e Tesserato**, quota associativa di **30,00 €**, una sezione informativa e un campo facoltativo. Sostituisci le scelte dimostrative con quelle approvate dalla tua associazione.\n\n<Info>Accedi come proprietario o con i permessi di modifica del profilo dell’associazione. I dati anagrafici dell’organizzazione, l’anno fiscale e la stagione sportiva si gestiscono separatamente nelle [Impostazioni](/docs/impostazioni).</Info>\n\n<Steps>\n<Step title=\"Apri il modulo dell’associazione\">\nApri **Organizzazione → Modulo Iscrizioni**. La pagina **Modulo Iscrizione** presenta le schede **Impostazioni**, **Quote**, **Sezioni Modulo** e **Campi Aggiuntivi**. Controlla la tipologia che vuoi raccogliere prima di modificare i testi.\n<Frame>![Pagina Modulo Iscrizione prima della configurazione](/images/tutorials/moduli-iscrizione/1.placeholder.svg)</Frame>\n</Step>\n<Step title=\"Scegli le tipologie da raccogliere\">\nNell’esempio lascia attivo **Socio e Tesserato** e disattiva **Socio** e **Tesserato**. Con una sola categoria attiva il modulo online mostra direttamente quella tipologia; non occorre mantenerle tutte abilitate.\n<Frame>![Sola tipologia Socio e Tesserato selezionata](/images/tutorials/moduli-iscrizione/2.placeholder.svg)</Frame>\n</Step>\n<Step title=\"Imposta la quota semplice\">\nIn **Quote** imposta **Quota associativa** a **30,00 €**, lasciando **Quota Tesseramento** a **0,00 €**. Mantieni disattivate le quote multiple per seguire questo esempio. Le quote esistenti dei soci non vengono ricalcolate dalla sola configurazione del modulo.\n<Frame>![Quota associativa semplice prima del salvataggio](/images/tutorials/moduli-iscrizione/3.placeholder.svg)</Frame>\n</Step>\n<Step title=\"Aggiungi le informazioni da leggere\">\nIn **Sezioni Modulo** premi **Aggiungi**. Compila il nome **Materiale per allenamento** e il testo **Porta borraccia e abbigliamento comodo.**. Seleziona la visibilità per **SOCI E TESSERATI** e lascia disattivate le due visibilità per categorie singole.\n<Frame>![Sezione informativa con titolo, testo e visibilità](/images/tutorials/moduli-iscrizione/4.placeholder.svg)</Frame>\n</Step>\n<Step title=\"Aggiungi un’informazione facoltativa\">\nIn **Campi Aggiuntivi** premi **Testo**, poi clicca sul campo nell’anteprima. Imposta **Etichetta** a **Taglia maglietta**, **Placeholder** a **Esempio: M** e **Etichetta aiuto** a **Indica la taglia desiderata.**. Disattiva **Obbligatorio**.\n<Frame>![Proprietà del campo facoltativo Taglia maglietta](/images/tutorials/moduli-iscrizione/5.placeholder.svg)</Frame>\n</Step>\n<Step title=\"Salva e riapri la configurazione\">\nPremi il **Salva** della pagina e attendi **Modulo d'iscrizione aggiornato correttamente**. Ricarica la pagina e controlla che resti attiva solo **Socio e Tesserato**. Riapri le altre schede: la quota resta **30,00 €**, il nome della sezione compare in maiuscolo e il campo conserva etichetta, aiuto e facoltatività.\n<Frame>![Tipologia conservata dopo il ricaricamento](/images/tutorials/moduli-iscrizione/6.placeholder.svg)</Frame>\n<Frame>![Sezione informativa conservata dopo il salvataggio](/images/tutorials/moduli-iscrizione/7.placeholder.svg)</Frame>\n<Frame>![Proprietà del campo conservate dopo il salvataggio](/images/tutorials/moduli-iscrizione/8.placeholder.svg)</Frame>\n</Step>\n<Step title=\"Controlla il modulo online prima di distribuire il link\">\nApri **Organizzazione → Libro Soci → Condividi link iscrizioni**. Usa l’icona di copia accanto al link per prepararlo negli appunti e **Apri** per controllarlo in una nuova scheda. Verifica nome dell’associazione e tipologia **Socio e Tesserato**. Nell’esempio, **Continua come** con l’account dimostrativo conduce all’anagrafica: **Taglia maglietta** mostra l’aiuto impostato e non è obbligatoria. Fermati prima di inviare una richiesta se stai soltanto controllando il modulo.\n<Frame>![Libro Soci con il comando per condividere il link](/images/tutorials/moduli-iscrizione/9.placeholder.svg)</Frame>\n<Frame>![Finestra del link corrente con URL e QR oscurati](/images/tutorials/moduli-iscrizione/10.placeholder.svg)</Frame>\n<Frame>![Modulo online aperto con la tipologia configurata](/images/tutorials/moduli-iscrizione/11.placeholder.svg)</Frame>\n<Frame>![Campo aggiuntivo nell’anagrafica del modulo online](/images/tutorials/moduli-iscrizione/12.placeholder.svg)</Frame>\n</Step>\n</Steps>\n\n<Note>Il collaboratore in sola lettura può consultare questa pagina, ma il **Salva** superiore è disattivato e il server rifiuta la modifica. La disponibilità della voce del menu dipende anche dal piano dell’associazione.</Note>\n<Frame>![Modulo consultato in sola lettura con Salva disattivato](/images/tutorials/moduli-iscrizione/13.placeholder.svg)</Frame>\n\nProsegui dal [tutorial sui moduli personalizzati](/tutorials/come-creare-moduli-iscrizione-personalizzati) per le opzioni specifiche e dalla [guida al link d’iscrizione](/faq/come-condividere-il-link-iscrizioni) per la condivisione. Quote multiple, nuovo invio, firme, PDF e consegna di messaggi hanno percorsi separati.\n\n<Note>La procedura riguarda la preparazione delle iscrizioni di un’associazione già configurata. Il primo accesso a un’istanza nuova, la creazione dell’account e il percorso di benvenuto richiedono la propria verifica.</Note>"}];
}
export function overviewReferencePages(source) {
    const evidence = sources.map(([path, symbol, length]) => {
        const result = source(path, symbol, length);
        if (result.canonical_source_sha256 !== reviewedFiles[path]) throw new Error('Overview evidence changed; review required: ' + path);
        return result;
    });
    return overviewDraftPages().map(page => {
        if (page.id !== 'per-iniziare') return {...page, evidence, screenshots: [],
            status: page.status || 'verified', reason: page.reason || '', kind: 'code-reference', intent: 'manual.overview'};
        const initialEvidence = registrationFormsSourceContracts.map(([path, symbol, length]) => {
            const result = source(path, symbol, length);
            if (result.canonical_source_sha256 !== registrationFormsReviewedSources[path])
                throw new Error('Initial registration setup changed; review required: ' + path);
            return result;
        });
        return {...page, evidence: [...evidence, ...initialEvidence], screenshots: [],
            status: 'pending', kind: 'workflow', authored: true, intent: 'registration.forms.initial-setup'};
    });
}

// Pure discovery never calls verified builders or fabricates source evidence.
const editorialDescriptor = (page, refs, reviewed, recipe_module, status = page.status, reason = page.reason) => ({
    path: page.path, id: page.id, title: page.title,
    status: ['unsupported', 'needs_external_verification'].includes(status) ? status : 'pending',
    reason: reason || 'Editorial draft metadata only; implementation and workflow proof remain required.',
    recipe_module, verified: false,
    source_contracts: refs.map(([path, symbol, length]) => ({path, symbol, length, reviewed_sha256: reviewed[path]})),
});

export function overviewEditorialContracts() {
    const module = "docs/manuale/overview-recipes.mjs";
    return overviewDraftPages().map(page => editorialDescriptor(page,
        page.id === 'per-iniziare' ? [...sources, ...registrationFormsSourceContracts] : sources,
        page.id === 'per-iniziare' ? {...overviewReviewedSources, ...registrationFormsReviewedSources} : overviewReviewedSources, module));
}
