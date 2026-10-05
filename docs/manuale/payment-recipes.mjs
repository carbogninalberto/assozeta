// Draft editorial provenance only; this map does not alter runtime verification.
export const paymentReviewedSources = Object.freeze({
    "BE/application/models/invoices_models.py": "5848c1bcdbafdd6d644f5dd70e82979095d1bdbe85c989075784e54418fc1af6",
    "BE/application/models/payment_models.py": "9dbcab8698adbde447a9c4f6eb52143409cf197fde3ce90d0aa8da56a1f8ebda",
    "BE/application/models/user_models.py": "3834a5655391ca750830d03175d7f663276697e50ab3fcfffcfd9ad45b7b931c",
    "BE/application/permissions_registry.py": "b8318ad63daa9452ac736b2152449b14c49f73bc1417622a4181b7c3ceb3b1fa",
    "BE/application/printing_tasks.py": "858df2f90a7788ca9c2a27ce43266b62d8ec9b1f86ab197570834975b6b2fde9",
    "BE/application/serializers/payment_serializers.py": "abc31bd21b536db8cfe5972c24d0befed834914fb8bdeb832205c2e8b62b4e4f",
    "BE/application/services/invoice_service.py": "98ba3acf0950b310fb5e47abbd8ba57cc9737b757fa461e5c2989ab18af95519",
    "BE/application/utils/printing.py": "58461a8b9022432f807ba7d912821aaae4ab2e44608c58d43e4280556ee7c760",
    "BE/application/views/invoice_views.py": "bb0f44ec825d4050322f306d280e078178b1978e42a5964c80c8d400b187a331",
    "BE/application/views/payment_views.py": "4f45b99318afaa7189177f73cf82863f6f281dc8a2992b15dcf299b11f8e8c34",
    "BE/application/views/profile_views.py": "37aca983a9edb10673bd76786b7f8efce7adfeaa129e9d4848df85ead31936d1",
    "BE/docmanager/tasks.py": "ffd749d2177f27b1dff05dd9245f96ac21579782e39cb6882aa46fe79befbc87",
    "BE/docmanager/views/document_view.py": "990fa77d97d984ed0971b230ff8d20e36a8b4a65c2c21222f6b580cca5fb3b37",
    "BE/docmanager/views/printing_views.py": "f9127869836c46408d9e5f48fc16733c6fce66efedb2e2fa7f96344095e01753",
    "BE/templates/document/application/invoice.html": "5a0b5f66655c8b7d288a42dba051e9a9ff2cc3e49e7f6227f442ddd2c926039d",
    "UI/src/components/Sidebar.svelte": "9f8344602ae269b5dbbf90dfd6f75e5154f3d5774ad544ac41105b74af2e333c",
    "UI/src/components/filters/FilterSelect.svelte": "556100c1c9aecd36f7aad7a3de0ad389ecd908517a8e8934586474ff111c35e5",
    "UI/src/components/modals/InvoicePreviewModal.svelte": "d65ee4f5163e304ff2f4a5de11fbca7f724c388e7c1e652e3d1b44d98fc21ed6",
    "UI/src/routes.js": "58bddc98548095f14f39fa3141277de8cd00e2133faf49f6761131b109d254b0",
    "UI/src/routes/accounting/payment/PaymentList.svelte": "34017cc9d3bb103ff10b326cf95876190ce6060886c018816bd85bf8bbaa64fe",
    "UI/src/routes/accounting/payment/modals/AddEditModal.svelte": "b40fb3c86aa525f87e4f7268ddb7dede3f544e959d28e6baa227419d615a009e",
    "UI/src/routes/accounting/receipts/ReceiptList.svelte": "243a880cd8fd23cb453b5542579f5d603c1d803ad6d4762203a232132e49d702",
    "UI/src/routes/accounting/receipts/invoiceActionState.js": "6c1c7673e6dd2e227bdd8f50ea6bea439662b15ebd414b4bd011d39b22432289",
    "UI/src/routes/accounting/receipts/modals/EditModal.svelte": "33f23e11f3b2d8e8abc3ec748c8190915d6114f5128cadc7664d8824304f5873",
    "UI/src/routes/accounting/receipts/modals/ShareModal.svelte": "0c86445d001a0d9141f26b673471a5b8b1ed1b9f15dc22264e125dc34d61bda3",
    "UI/src/routes/profile/sections/Settings.svelte": "f620ddf62f5a04e8b8b978674e32338496dcd55e15a290a9cb8804b21e28fe2e",
    "UI/src/utils/Permissions.js": "100a24508b57b6c73a323ab4f128ee2c79aef3212a2cd0178172199d59e51854"
});

// These projections require the named real workflows, not discovery inventory.
export const paymentCaptureSpecs = {
    'payments-create-edit-approve': ['images/pagamenti/registrazione/', [
        'filled-incoming-cash-payment', 'created-payment-in-list', 'edited-payment-amount',
        'edited-payment-persists-after-reload', 'approval-without-immediate-pdf',
        'approved-payment-persists-with-receipt-record',
    ]],
    'receipts-approve-download': ['images/ricevute/consultazione/', [
        'approval-with-pdf-and-no-email', 'receipt-list-with-generated-pdfs-and-progressives',
        'receipt-pdf-preview-without-share-token',
        'copy-download-link-with-token-redacted',
    ]],
    'receipts-edit-delete': ['images/ricevute/modifica-eliminazione/', [
        'older-receipt-with-progressive-seven', 'edit-receipt-progressive-to-nine',
        'edited-receipt-persists-after-reload', 'confirm-deletion-of-older-receipt',
        'payment-unpaid-after-receipt-deletion',
    ]],
};

export function paymentPages(id, report, source) {
    const spec = paymentCaptureSpecs[id];
    if (!spec) return [];
    const [prefix, checkpoints] = spec;
    if (report.screenshots.length !== checkpoints.length || report.screenshots.some((image, index) =>
        image.path !== prefix + (index + 1) + '.png' || image.checkpoint !== checkpoints[index]))
        throw new Error('Payment/receipt checkpoints do not match the reviewed steps: ' + id);
    if (id === 'receipts-edit-delete' && report.fixture_profile !== 'receipts-edit-delete')
        throw new Error('Receipt mutation requires its older-receipt fixture');
    return paymentDefinitions(id, report, source);
}

function paymentDefinitions(id, report, source) {
    if (id === 'receipts-edit-delete') return receiptMutationPages(report, source);
    const evidence = [
        source(report, 'UI/src/components/Sidebar.svelte', '<span class="menu-text">Pagamenti</span>', 53),
        source(report, 'UI/src/routes.js', "'/payment/list/:id?':", 30),
        source(report, 'UI/src/utils/Permissions.js', 'export const canPerformAction', 30),
        source(report, 'UI/src/routes/accounting/payment/modals/AddEditModal.svelte', 'async function create(formData)', 45),
        source(report, 'UI/src/routes/accounting/payment/modals/AddEditModal.svelte', 'async function update(formData)', 40),
        source(report, 'UI/src/routes/accounting/payment/modals/AddEditModal.svelte', "label: 'Descrizione'", 86),
        source(report, 'UI/src/routes/accounting/payment/PaymentList.svelte', 'window.markAsPaid =', 111),
        source(report, 'BE/application/views/payment_views.py', 'def payment_add(request):', 112),
        source(report, 'BE/application/views/payment_views.py', 'def payment_update(request, uid):', 160),
        source(report, 'BE/application/views/payment_views.py', 'def payment_approve(request, uid):', 178),
        source(report, 'BE/application/serializers/payment_serializers.py', 'class PaymentOptimizedSerializer(', 52),
        source(report, 'BE/application/models/user_models.py', 'auto_paid_payment =', 7),
        source(report, 'BE/application/models/payment_models.py', 'class Payment(GroupModelMixin):', 68),
        source(report, 'BE/application/models/invoices_models.py', 'class Invoice(GroupModelMixin):', 49),
        source(report, 'BE/application/services/invoice_service.py', 'def get_next_invoice_number(', 89),
        source(report, 'BE/application/permissions_registry.py', "'payment/add'", 14),
    ];
    const page = (path, title, sectionId, body, screenshots) => ({path, title, id: sectionId, body, evidence, screenshots});
    if (id === 'payments-create-edit-approve') return [
        page('docs/pagamenti.mdx', 'Creare un pagamento', 'creare-un-pagamento', `## Creare un pagamento

<Steps>
  <Step title="Apri il modulo">
    Apri **Pagamenti** dal menu laterale e premi **Pagamento**.
  </Step>
  <Step title="Compila una nuova entrata">
    Inserisci **Descrizione** e **Importo**. In **Intestato a** scegli la persona; indica il **Conto** e la **Causale**.
    Nell'esempio registriamo **35,00** in contanti per **Giulia Bianchi** sul conto **Cassa Aurora**.
    Mantieni **Tipologia → Entrata** e **Tipo Quota → Altro**, con la **Data Incasso** vuota.
    <Frame>![Modulo compilato per una nuova entrata in contanti](/images/pagamenti/registrazione/1.png)</Frame>
  </Step>
  <Step title="Crea e controlla">
    Premi **Crea**. Chiudi i dettagli per tornare all'elenco e trovare il nuovo pagamento.
    Con l'incasso automatico disattivato, il pagamento viene creato **In attesa** e va incassato separatamente.
    <Frame>![Pagamento creato nell'elenco](/images/pagamenti/registrazione/2.png)</Frame>
  </Step>
</Steps>

<Note>Un collaboratore con il solo permesso di lettura può consultare i pagamenti. Per crearli serve il permesso di creazione.</Note>`, report.screenshots.slice(0, 2)),
        page('docs/pagamenti.mdx', 'Modificare un pagamento', 'modificare-un-pagamento', `## Modificare un pagamento

<Steps>
  <Step title="Apri un pagamento in attesa">
    Nell'elenco **Pagamenti**, premi il pulsante con il suggerimento **Modifica** nella riga del pagamento.
  </Step>
  <Step title="Modifica l'importo">
    Nel modulo **Modifica Pagamento** cambia **Importo**, per esempio da **35,00** a **40,00**, poi premi **Modifica**.
    <Frame>![Modifica dell'importo prima dell'incasso](/images/pagamenti/registrazione/3.png)</Frame>
  </Step>
  <Step title="Controlla il salvataggio">
    Chiudi i dettagli e torna all'elenco. Il nuovo importo rimane salvato anche dopo il ricaricamento della pagina.
    <Frame>![Importo modificato e conservato dopo il ricaricamento](/images/pagamenti/registrazione/4.png)</Frame>
  </Step>
</Steps>

<Note>Il solo accesso in lettura non permette di modificare un pagamento.</Note>`, report.screenshots.slice(2, 4)),
        page('docs/pagamenti.mdx', 'Segna un pagamento come "pagato"', 'segna-un-pagamento-come-pagato', `## Segna un pagamento come "pagato"

<Steps>
  <Step title="Conferma l'incasso">
    Nella riga di un'entrata **In attesa**, premi il pulsante con il suggerimento **Segna come pagato**.
    Nella finestra **Incassare il pagamento?** indica la **Data Incasso**.
  </Step>
  <Step title="Scegli la generazione del PDF">
    **Genera ricevuta** è selezionato inizialmente. Se lo togli, il sistema registra comunque una ricevuta per l'entrata intestata alla persona, ma non ne genera subito il PDF.
    Nell'esempio togliamo la selezione e lasciamo **Invia email** disattivato; premiamo **Incassa**.
    <Frame>![Conferma dell'incasso senza generazione immediata del PDF](/images/pagamenti/registrazione/5.png)</Frame>
  </Step>
  <Step title="Controlla lo stato">
    Chiudi i dettagli. Il pagamento risulta **Pagato** e conserva la data indicata anche dopo il ricaricamento.
    <Frame>![Pagamento incassato e conservato nell'elenco](/images/pagamenti/registrazione/6.png)</Frame>
  </Step>
</Steps>

<Note>Per incassare serve il permesso di modifica dei pagamenti. [Leggi come consultare il PDF della ricevuta](/docs/ricevute#come-posso-scaricare-una-ricevuta).</Note>`, report.screenshots.slice(4)),
    ];
    evidence.push(
        source(report, 'UI/src/routes.js', "'/invoice/list':", 19),
        source(report, 'UI/src/routes/accounting/receipts/ReceiptList.svelte', 'const availability = getInvoiceActionAvailability(', 60),
        source(report, 'UI/src/routes/accounting/receipts/invoiceActionState.js', 'export function getInvoiceActionAvailability(', 17),
        source(report, 'UI/src/components/modals/InvoicePreviewModal.svelte', '<iframe', 8),
        source(report, 'UI/src/routes/accounting/receipts/ReceiptList.svelte', 'pdfLink={getInvoiceDocumentUrl(invoiceDialog.row, true)}', 19),
        source(report, 'UI/src/routes/accounting/receipts/modals/ShareModal.svelte', '<Clipboard', 32),
        source(report, 'BE/application/views/payment_views.py', 'def payment_cancel(request, uid):', 43),
        source(report, 'BE/application/views/invoice_views.py', 'def invoice_list(request):', 161),
        source(report, 'BE/application/printing_tasks.py', 'def print_document_invoice(', 11),
        source(report, 'BE/docmanager/views/printing_views.py', 'def document_invoice(request, uid):', 52),
        source(report, 'BE/docmanager/views/document_view.py', 'def retrieve_document(request, uid):', 80),
        source(report, 'BE/application/utils/printing.py', 'def print_and_store_pdf(', 48),
        source(report, 'BE/templates/document/application/invoice.html', '{% if enumerate_invoices is True %}', 4),
        source(report, 'BE/docmanager/tasks.py', "@shared_task(name='save_to_storage')", 15),
        source(report, 'BE/application/permissions_registry.py', "'invoice/list'", 9),
    );
    return [
        page('docs/ricevute.mdx', 'Come vengono emesse le ricevute?', 'come-vengono-emesse-le-ricevute', `## Come vengono emesse le ricevute?

Per un'entrata intestata a una persona, [conferma l'incasso dalla pagina Pagamenti](/docs/pagamenti#segna-un-pagamento-come-pagato).
Mantieni **Genera ricevuta** selezionato per avviare anche la generazione del PDF.
Nell'esempio incassiamo la quota associativa di **Sara Conti**, pari a **25,00**, lasciando **Invia email** disattivato.

<Frame>![Incasso di una quota associativa con generazione del PDF](/images/ricevute/consultazione/1.png)</Frame>

<Note>Se togli **Genera ricevuta**, la ricevuta viene comunque registrata senza il PDF immediato. Aprendo l'elenco **Ricevute**, il sistema avvia la generazione dei PDF mancanti.</Note>`, report.screenshots.slice(0, 1)),
        page('docs/ricevute.mdx', 'Numerazione progressiva', 'numerazione-progressiva', `## Numerazione progressiva

Alla conferma dell'incasso di un'entrata intestata a una persona, il sistema assegna un numero alla ricevuta.
Nell'esempio, con numero iniziale pari a zero e senza altre ricevute nell'anno configurato, i due incassi producono le ricevute **1** e **2**.
Trovi il progressivo nella colonna **Numero** dell'elenco **Ricevute**.

<Frame>![Due ricevute registrate con progressivi 1 e 2](/images/ricevute/consultazione/2.png)</Frame>`, report.screenshots.slice(1, 2)),
        page('docs/ricevute.mdx', 'Come posso scaricare una ricevuta?', 'come-posso-scaricare-una-ricevuta', `## Come posso scaricare una ricevuta?

<Steps>
  <Step title="Apri le ricevute">
    Dal menu laterale apri **Documenti fiscali → Ricevute**. Trova la riga della persona e il numero della ricevuta.
    <Frame>![Elenco delle ricevute dell'associazione](/images/ricevute/consultazione/2.png)</Frame>
  </Step>
  <Step title="Consulta il PDF">
    Premi il pulsante con il suggerimento **Ricevuta n.2**, usando il numero della ricevuta che vuoi aprire.
    Si apre l'anteprima del PDF. Premi **Chiudi** per tornare all'elenco.
    <Frame>![Anteprima del PDF della ricevuta](/images/ricevute/consultazione/3.png)</Frame>
  </Step>
  <Step title="Scarica il documento">
    Chiudi l'anteprima e premi **Condividi ricevuta** nella stessa riga.
    Nella finestra **Condividi link ricevuta**, premi il pulsante accanto al link per copiarlo.
    Apri il link copiato in una nuova scheda del browser: il PDF viene scaricato.
    <Frame>![Pulsante per copiare il link di download; il link è oscurato nell'immagine](/images/ricevute/consultazione/4.png)</Frame>
  </Step>
</Steps>

<Note>Un collaboratore con il permesso di lettura delle ricevute può consultarle. Il solo permesso di lettura non autorizza modifica, eliminazione o il comando di generazione manuale della ricevuta.</Note>`, report.screenshots.slice(1)),
    ];
}

function receiptMutationPages(report, source) {
    const refs = [
        ['UI/src/components/Sidebar.svelte', '<span class="menu-text">Documenti fiscali</span>', 32],
        ['UI/src/routes.js', "'/invoice/list':", 19],
        ['UI/src/utils/Permissions.js', 'export const canPerformAction', 30],
        ['UI/src/routes/accounting/receipts/ReceiptList.svelte', 'window.deleteInvoice =', 43],
        ['UI/src/routes/accounting/receipts/ReceiptList.svelte', 'const availability = getInvoiceActionAvailability(', 75],
        ['UI/src/routes/accounting/receipts/ReceiptList.svelte', 'label="Anno ricevute"', 8],
        ['UI/src/components/filters/FilterSelect.svelte', 'function change(event)', 20],
        ['UI/src/routes/accounting/receipts/invoiceActionState.js', 'export function getInvoiceActionAvailability(', 17],
        ['UI/src/routes/accounting/receipts/modals/EditModal.svelte', 'async function update(data)', 38],
        ['UI/src/routes/accounting/receipts/modals/EditModal.svelte', 'name="number"', 20],
        ['UI/src/routes/profile/sections/Settings.svelte', 'bind:checked={settings.enumerate_invoices}', 41],
        ['BE/application/views/profile_views.py', 'def profile_info(request):', 34],
        ['BE/application/views/invoice_views.py', 'def invoice_list(request):', 161],
        ['BE/application/views/invoice_views.py', 'def invoice_update(request, uid):', 62],
        ['BE/application/views/invoice_views.py', 'def invoice_delete(request, uid):', 31],
        ['BE/application/views/payment_views.py', 'def payment_approve(request, uid):', 178],
        ['BE/application/services/invoice_service.py', 'def get_next_invoice_number(', 89],
        ['BE/application/models/user_models.py', 'temporary_invoice_deletion =', 11],
        ['BE/application/models/user_models.py', 'def get_invoice_template(', 9],
        ['BE/application/models/payment_models.py', 'invoice = models.ForeignKey(Invoice', 19],
        ['BE/application/models/invoices_models.py', 'class Invoice(GroupModelMixin):', 49],
        ['BE/application/printing_tasks.py', 'def print_document_invoice(', 11],
        ['BE/docmanager/views/printing_views.py', 'def document_invoice(request, uid):', 52],
        ['BE/docmanager/views/printing_views.py', 'def document_invoice_view(request, uid):', 217],
        ['BE/application/utils/printing.py', 'def print_and_store_pdf(', 48],
        ['BE/templates/document/application/invoice.html', '{% if enumerate_invoices is True %}', 4],
        ['BE/application/permissions_registry.py', "'invoice/list'", 9],
    ];
    const evidence = refs.map(args => source(report, ...args));
    const page = (title, id, body, screenshots) => ({path: 'docs/ricevute.mdx', title, id, body, evidence, screenshots});
    return [
        page('Modifica del progressivo della ricevuta', 'modifica-del-progressivo-della-ricevuta', `## Modifica del progressivo della ricevuta

<Steps>
  <Step title="Trova la ricevuta">
    Apri **Documenti fiscali → Ricevute**. Se vuoi includere anche anni diversi da quello corrente, scegli **Filtra Anno** nel campo **Anno ricevute**.
    Nella riga della persona premi il pulsante con il suggerimento **Modifica**.
    <Frame>![Ricevuta di Giulia Bianchi con numero 7](/images/ricevute/modifica-eliminazione/1.png)</Frame>
  </Step>
  <Step title="Cambia il numero">
    Nel modulo **Modifica ricevuta**, cambia **Numero**, per esempio da **7** a **9**. Premi **Salva**.
    <Frame>![Nuovo progressivo nel modulo della ricevuta](/images/ricevute/modifica-eliminazione/2.png)</Frame>
  </Step>
  <Step title="Controlla il risultato">
    Il nuovo numero compare nell'elenco anche dopo il ricaricamento. Il PDF viene rigenerato con il progressivo aggiornato.
    La modifica del numero non cambia l'importo del pagamento collegato.
    <Frame>![Progressivo 9 conservato nell'elenco delle ricevute](/images/ricevute/modifica-eliminazione/3.png)</Frame>
  </Step>
</Steps>

<Note>Per modificare una ricevuta serve il relativo permesso. Un collaboratore in sola lettura non può salvarne una modifica.</Note>`, report.screenshots.slice(0, 3)),
        page('Eliminare una ricevuta', 'eliminare-una-ricevuta', `## Eliminare una ricevuta

<Steps>
  <Step title="Seleziona la ricevuta da eliminare">
    Apri **Documenti fiscali → Ricevute** e trova la ricevuta. Premi il pulsante con il suggerimento **Elimina Ricevuta** nella sua riga.
  </Step>
  <Step title="Conferma l'eliminazione">
    Nella finestra **Vuoi eliminare definitivamente la ricevuta?**, premi **Elimina**.
    La ricevuta viene rimossa dall'elenco.
    <Frame>![Conferma dell'eliminazione della ricevuta](/images/ricevute/modifica-eliminazione/4.png)</Frame>
  </Step>
  <Step title="Controlla il pagamento collegato">
    Apri **Pagamenti**. Il pagamento rimane presente con lo stesso importo, torna **In attesa** e non è più collegato alla ricevuta eliminata.
    <Frame>![Pagamento tornato in attesa dopo l'eliminazione della ricevuta](/images/ricevute/modifica-eliminazione/5.png)</Frame>
  </Step>
</Steps>

<Note>In questa versione non è applicato un limite di sette giorni per questa cancellazione. Nell'esempio la ricevuta ha 29 giorni. Serve comunque il permesso di eliminazione; il solo accesso in lettura non basta.</Note>`, report.screenshots.slice(3)),
        {...page('Configurazioni possibili', 'configurazioni-possibili', `### Configurazioni possibili

Le impostazioni delle ricevute comprendono:

1. **Numera Ricevute**: controlla la visualizzazione del progressivo nella testata del modello di ricevuta illustrato. Il numero del record viene assegnato all'incasso anche se questa opzione è disattivata.
2. **Numero iniziale ricevute**: il prossimo progressivo parte dal maggiore tra questo valore e i numeri già presenti nell'intervallo considerato, incrementato di uno.
3. **Data uguale per ricevute e pagamenti**: durante l'incasso, se attiva, imposta la data di creazione della ricevuta sulla data del pagamento.

<Note>La cancellazione descritta sopra non richiede l'attivazione di una finestra temporanea di sette giorni. Restano necessari i permessi dell'operazione.</Note>`, []),
            status: 'pending', reason: 'Corrected obsolete configuration claims from actual source; settings UI and PDF/date variants still need their own workflow'},
    ];
}

export function paymentDraftPages() {
    return Object.keys(paymentCaptureSpecs).flatMap(id => paymentDefinitions(id, {screenshots: []}, () => ({})))
        .map(({evidence, screenshots, ...page}) => page);
}

// Original legacy section identities, without invoking a proof builder.
const paymentEditorialSections = [
    [
        "docs/pagamenti.mdx",
        "creare-un-pagamento",
        "Creare un pagamento"
    ],
    [
        "docs/pagamenti.mdx",
        "modificare-un-pagamento",
        "Modificare un pagamento"
    ],
    [
        "docs/pagamenti.mdx",
        "segna-un-pagamento-come-pagato",
        "Segna un pagamento come \"pagato\""
    ],
    [
        "docs/ricevute.mdx",
        "come-vengono-emesse-le-ricevute",
        "Come vengono emesse le ricevute?"
    ],
    [
        "docs/ricevute.mdx",
        "numerazione-progressiva",
        "Numerazione progressiva"
    ],
    [
        "docs/ricevute.mdx",
        "come-posso-scaricare-una-ricevuta",
        "Come posso scaricare una ricevuta?"
    ],
    [
        "docs/ricevute.mdx",
        "modifica-del-progressivo-della-ricevuta",
        "Modifica del progressivo della ricevuta"
    ],
    [
        "docs/ricevute.mdx",
        "eliminare-una-ricevuta",
        "Eliminare una ricevuta"
    ],
    [
        "docs/ricevute.mdx",
        "configurazioni-possibili",
        "Configurazioni possibili"
    ]
];
const paymentEditorialSourceContracts = {
    "payments": [
        [
            "UI/src/components/Sidebar.svelte",
            "<span class=\"menu-text\">Pagamenti</span>",
            53
        ],
        [
            "UI/src/routes.js",
            "'/payment/list/:id?':",
            30
        ],
        [
            "UI/src/utils/Permissions.js",
            "export const canPerformAction",
            30
        ],
        [
            "UI/src/routes/accounting/payment/modals/AddEditModal.svelte",
            "async function create(formData)",
            45
        ],
        [
            "UI/src/routes/accounting/payment/modals/AddEditModal.svelte",
            "async function update(formData)",
            40
        ],
        [
            "UI/src/routes/accounting/payment/modals/AddEditModal.svelte",
            "label: 'Descrizione'",
            86
        ],
        [
            "UI/src/routes/accounting/payment/PaymentList.svelte",
            "window.markAsPaid =",
            111
        ],
        [
            "BE/application/views/payment_views.py",
            "def payment_add(request):",
            112
        ],
        [
            "BE/application/views/payment_views.py",
            "def payment_update(request, uid):",
            160
        ],
        [
            "BE/application/views/payment_views.py",
            "def payment_approve(request, uid):",
            178
        ],
        [
            "BE/application/serializers/payment_serializers.py",
            "class PaymentOptimizedSerializer(",
            52
        ],
        [
            "BE/application/models/user_models.py",
            "auto_paid_payment =",
            7
        ],
        [
            "BE/application/models/payment_models.py",
            "class Payment(GroupModelMixin):",
            68
        ],
        [
            "BE/application/models/invoices_models.py",
            "class Invoice(GroupModelMixin):",
            49
        ],
        [
            "BE/application/services/invoice_service.py",
            "def get_next_invoice_number(",
            89
        ],
        [
            "BE/application/permissions_registry.py",
            "'payment/add'",
            14
        ]
    ],
    "receipts": [
        [
            "UI/src/components/Sidebar.svelte",
            "<span class=\"menu-text\">Pagamenti</span>",
            53
        ],
        [
            "UI/src/routes.js",
            "'/payment/list/:id?':",
            30
        ],
        [
            "UI/src/utils/Permissions.js",
            "export const canPerformAction",
            30
        ],
        [
            "UI/src/routes/accounting/payment/modals/AddEditModal.svelte",
            "async function create(formData)",
            45
        ],
        [
            "UI/src/routes/accounting/payment/modals/AddEditModal.svelte",
            "async function update(formData)",
            40
        ],
        [
            "UI/src/routes/accounting/payment/modals/AddEditModal.svelte",
            "label: 'Descrizione'",
            86
        ],
        [
            "UI/src/routes/accounting/payment/PaymentList.svelte",
            "window.markAsPaid =",
            111
        ],
        [
            "BE/application/views/payment_views.py",
            "def payment_add(request):",
            112
        ],
        [
            "BE/application/views/payment_views.py",
            "def payment_update(request, uid):",
            160
        ],
        [
            "BE/application/views/payment_views.py",
            "def payment_approve(request, uid):",
            178
        ],
        [
            "BE/application/serializers/payment_serializers.py",
            "class PaymentOptimizedSerializer(",
            52
        ],
        [
            "BE/application/models/user_models.py",
            "auto_paid_payment =",
            7
        ],
        [
            "BE/application/models/payment_models.py",
            "class Payment(GroupModelMixin):",
            68
        ],
        [
            "BE/application/models/invoices_models.py",
            "class Invoice(GroupModelMixin):",
            49
        ],
        [
            "BE/application/services/invoice_service.py",
            "def get_next_invoice_number(",
            89
        ],
        [
            "BE/application/permissions_registry.py",
            "'payment/add'",
            14
        ],
        [
            "UI/src/routes.js",
            "'/invoice/list':",
            19
        ],
        [
            "UI/src/routes/accounting/receipts/ReceiptList.svelte",
            "const availability = getInvoiceActionAvailability(",
            60
        ],
        [
            "UI/src/routes/accounting/receipts/invoiceActionState.js",
            "export function getInvoiceActionAvailability(",
            17
        ],
        [
            "UI/src/components/modals/InvoicePreviewModal.svelte",
            "<iframe",
            8
        ],
        [
            "UI/src/routes/accounting/receipts/ReceiptList.svelte",
            "pdfLink={getInvoiceDocumentUrl(invoiceDialog.row, true)}",
            19
        ],
        [
            "UI/src/routes/accounting/receipts/modals/ShareModal.svelte",
            "<Clipboard",
            32
        ],
        [
            "BE/application/views/payment_views.py",
            "def payment_cancel(request, uid):",
            43
        ],
        [
            "BE/application/views/invoice_views.py",
            "def invoice_list(request):",
            161
        ],
        [
            "BE/application/printing_tasks.py",
            "def print_document_invoice(",
            11
        ],
        [
            "BE/docmanager/views/printing_views.py",
            "def document_invoice(request, uid):",
            52
        ],
        [
            "BE/docmanager/views/document_view.py",
            "def retrieve_document(request, uid):",
            80
        ],
        [
            "BE/application/utils/printing.py",
            "def print_and_store_pdf(",
            48
        ],
        [
            "BE/templates/document/application/invoice.html",
            "{% if enumerate_invoices is True %}",
            4
        ],
        [
            "BE/docmanager/tasks.py",
            "@shared_task(name='save_to_storage')",
            15
        ],
        [
            "BE/application/permissions_registry.py",
            "'invoice/list'",
            9
        ],
        [
            "UI/src/components/Sidebar.svelte",
            "<span class=\"menu-text\">Documenti fiscali</span>",
            32
        ],
        [
            "UI/src/routes/accounting/receipts/ReceiptList.svelte",
            "window.deleteInvoice =",
            43
        ],
        [
            "UI/src/routes/accounting/receipts/ReceiptList.svelte",
            "const availability = getInvoiceActionAvailability(",
            75
        ],
        [
            "UI/src/routes/accounting/receipts/ReceiptList.svelte",
            "label=\"Anno ricevute\"",
            8
        ],
        [
            "UI/src/components/filters/FilterSelect.svelte",
            "function change(event)",
            20
        ],
        [
            "UI/src/routes/accounting/receipts/modals/EditModal.svelte",
            "async function update(data)",
            38
        ],
        [
            "UI/src/routes/accounting/receipts/modals/EditModal.svelte",
            "name=\"number\"",
            20
        ],
        [
            "UI/src/routes/profile/sections/Settings.svelte",
            "bind:checked={settings.enumerate_invoices}",
            41
        ],
        [
            "BE/application/views/profile_views.py",
            "def profile_info(request):",
            34
        ],
        [
            "BE/application/views/invoice_views.py",
            "def invoice_update(request, uid):",
            62
        ],
        [
            "BE/application/views/invoice_views.py",
            "def invoice_delete(request, uid):",
            31
        ],
        [
            "BE/application/models/user_models.py",
            "temporary_invoice_deletion =",
            11
        ],
        [
            "BE/application/models/user_models.py",
            "def get_invoice_template(",
            9
        ],
        [
            "BE/application/models/payment_models.py",
            "invoice = models.ForeignKey(Invoice",
            19
        ],
        [
            "BE/docmanager/views/printing_views.py",
            "def document_invoice_view(request, uid):",
            217
        ]
    ]
};

// Metadata is fresh on every call and never grants publication verification.
export function paymentEditorialContracts() {
    return paymentEditorialSections.map(([path, id, title]) => ({path, id, title})).map(page => ({
        path: page.path, id: page.id, title: page.title, status: 'pending',
        reason: 'Procedura in attesa di esecuzione e revisione delle fonti.',
        recipe_module: 'docs/manuale/payment-recipes.mjs', verified: false,
        source_contracts: paymentEditorialSourceContracts[page.path === 'docs/pagamenti.mdx' ? 'payments' : 'receipts'].map(([path, symbol, length]) => ({
            path, symbol, length, reviewed_sha256: paymentReviewedSources[path],
        })),
    }));
}
