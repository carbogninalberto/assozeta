export const tagSourceContracts = [
    ['UI/src/components/Sidebar.svelte', '<span class="menu-text">Organizzazione</span>', 50],
    ['UI/src/routes.js', "'/members/list':", 18],
    ['UI/src/utils/Permissions.js', 'export const canPerformAction', 30],
    ['UI/src/routes/association/Members/MembersList.svelte', 'async function createTag()', 25],
    ['UI/src/routes/association/Members/MembersList.svelte', 'async function assignTag()', 110],
    ['UI/src/components/tables/BKNDatatable.svelte', 'data-row={rowIndex}', 50],
    ['BE/application/views/subscriptions_views.py', 'def subscription_tags_add(', 25],
    ['BE/application/views/subscriptions_views.py', 'def subscription_tags_assign(', 20],
    ['BE/application/views/subscriptions_views.py', 'def subscription_tags_unassign(', 20],
    ['BE/application/services/subscription_service.py', 'def create_tag(', 28],
    ['BE/application/services/subscription_service.py', 'def assign_tag_to_subscription(', 42],
    ['BE/application/services/subscription_service.py', 'def unassign_tag_from_subscription(', 40],
    ['BE/application/models/subscriptions_models.py', 'class Tags(', 18],
    ['BE/application/permissions_registry.py', "'subscription/tags/add'", 6],
    ['BE/application/urls.py', "path(r'subscription/tags/list'", 6],
];
export const tagSources = [...new Set(tagSourceContracts.map(([path]) => path))];
export const tagCaptureSpecs = {'tags-create-assign': ['images/tutorials/tags/', [
    'members-list', 'new-tag-name', 'tag-selected', 'assignment-persisted',
    'tag-unchecked-before-apply', 'tag-removal-persists-after-reload',
]]};
export const tagExpectedOutcome = Object.freeze({created: true, assigned_after_reload: true,
    removed_after_reload: true, tag_definition_preserved: true, unrelated_member_unchanged: true,
    reader_create_status: 403, reader_assign_status: 403, reader_unassign_status: 403});
const image = (number, alt) => `<Frame>![${alt}](/images/tutorials/tags/${number}.png)</Frame>`;
export function tagDraftPages() {
    const path = 'tutorials/come-assegnare-i-tag-agli-atleti.mdx';
    return [{path, title: 'Indice', id: 'indice', intent: 'members.tags.read', imageNumbers: [], body: `# Indice

Questa guida mostra come creare **Principianti**, assegnarlo all'iscrizione di **Giulia Bianchi** e rimuovere il collegamento conservando il tag disponibile.

<Steps><Step title="Crea e assegna">Parti dall'elenco delle iscrizioni, seleziona Giulia, crea il tag e conferma **APPLICA**.</Step><Step title="Controlla il salvataggio">Ricarica l'elenco e verifica che il tag compaia nella riga della persona.</Step><Step title="Rimuovi il collegamento">Riapri **Assegna Tag**, lascia il tag non selezionato e applica; controlla di nuovo dopo il ricaricamento.</Step></Steps>

<Note>Serve il permesso di modifica delle iscrizioni. Un collaboratore in lettura può consultare l'elenco, ma non creare, assegnare o rimuovere tag.</Note>`},
    {path, title: 'Creare un nuovo tag', id: 'creare-un-nuovo-tag', intent: 'members.tags.assign', imageNumbers: [1,2,3,4], body: `# Creare un nuovo tag

Apri **Organizzazione → Iscrizioni**. Nell'esempio useremo l'iscrizione di **Giulia Bianchi** e un tag chiamato **Principianti**.

<Steps>
<Step title="Seleziona l'iscrizione">Premi la casella nella riga di Giulia. Compaiono le azioni per le iscrizioni selezionate.
${image(1, 'Elenco delle iscrizioni da cui selezionare Giulia')}</Step>
<Step title="Inserisci un nome nuovo">Premi **Assegna Tag**. Nel campo **Nome tag...** scrivi **Principianti**, poi premi il pulsante **+**.
${image(2, 'Nome del tag da creare nel menu Assegna Tag')}</Step>
<Step title="Seleziona e applica">Attendi che il tag compaia, seleziona la sua casella e premi **APPLICA**.
${image(3, 'Principianti selezionato prima dell’applicazione')}</Step>
<Step title="Controlla dopo il ricaricamento">Il tag compare nella riga di Giulia. Ricarica l'elenco per verificare che il collegamento sia conservato.
${image(4, 'Tag nella riga di Giulia dopo il ricaricamento')}</Step>
</Steps>

<Note>Creare un tag e assegnarlo sono due operazioni distinte. Il pulsante **+** crea il nome nell'elenco; **APPLICA** lo collega alle iscrizioni selezionate.</Note>`},
    {path, title: 'Seleziona gli atleti', id: 'seleziona-gli-atleti', intent: 'members.tags.assign', imageNumbers: [1], body: `## Seleziona gli atleti

Trova la persona nell'elenco **Iscrizioni** e premi la casella della sua riga. Per il percorso mostrato seleziona soltanto **Giulia Bianchi**.
Controlla il nome prima di aprire **Assegna Tag**, perché l'azione riguarda le righe selezionate.

${image(1, 'Elenco da cui selezionare l’iscrizione da aggiornare')}

Le operazioni su più persone richiedono di controllare l'intera selezione. La prova di questa guida usa una sola iscrizione; Luca e Sara restano senza il tag dimostrativo.`},
    {path, title: 'Assegna Tag', id: 'assegna-tag', intent: 'members.tags.assign', imageNumbers: [2,3,4], body: `## Assegna Tag

Nel menu **Assegna Tag**, il campo **Nome tag...** cerca nell'elenco dei tag. Se il nome è nuovo e non corrisponde a un tag esistente, il pulsante **+** permette di crearlo.

<Steps><Step title="Controlla il tag da applicare">Seleziona **Principianti** nell'elenco. La casella deve risultare attiva prima della conferma.
${image(3, 'Casella del tag attiva')}</Step>
<Step title="Conferma il collegamento">Premi **APPLICA** e attendi il completamento dell'azione. Ricarica la pagina e controlla la riga di Giulia.
${image(4, 'Collegamento conservato dopo il ricaricamento')}</Step></Steps>

<Warning>La selezione comprende anche i tag da mantenere. Le caselle lasciate disattivate rimuovono i relativi collegamenti dalle iscrizioni selezionate: controlla l'elenco prima di applicare.</Warning>`},
    {path, title: 'Modificare e/o eliminare uno o più tag', id: 'modificare-e-o-eliminare-uno-o-piu-tag', intent: 'members.tags.remove', imageNumbers: [5,6], body: `# Modificare e/o eliminare uno o più tag

Per togliere **Principianti** dall'iscrizione di Giulia, modifica la selezione nel menu e controlla il risultato salvato.

<Steps><Step title="Riapri le azioni della persona">Seleziona Giulia e premi **Assegna Tag**. Per il caso mostrato lascia disattivata la casella **Principianti**.
${image(5, 'Tag non selezionato prima della conferma')}</Step>
<Step title="Applica e controlla">Premi **APPLICA**. Ricarica la pagina: nella riga di Giulia non deve più comparire **Principianti**.
${image(6, 'Riga di Giulia dopo la rimozione del collegamento e il ricaricamento')}</Step></Steps>

<Note>Questa operazione rimuove il collegamento alla persona; il tag **Principianti** resta nell'elenco e può essere assegnato di nuovo. Il cestino accanto al nome del tag è un'azione diversa, che questa procedura non esegue.</Note>`}];
}
export function tagPages(id, report, source) {
    if(id !== 'tags-create-assign') return [];
    const [prefix, checkpoints] = tagCaptureSpecs[id];
    if(report.status !== 'passed' || report.backend !== 'real' || report.fixture_version !== 8
        || report.fixture_profile !== 'baseline' || report.capture_format !== 'full-hd-v1'
        || report.viewport?.width !== 1920 || report.viewport?.height !== 1080 || report.device_scale_factor !== 1
        || report.screenshots?.length !== checkpoints.length) throw new Error('Tags require compatible complete real FullHD fixture7 capture');
    checkpoints.forEach((checkpoint, i) => {
        if(report.screenshots[i].checkpoint !== checkpoint || report.screenshots[i].path !== prefix+(i+1)+'.png')
            throw new Error('Tags checkpoint or image path differs');
    });
    for(const [key,value] of Object.entries(tagExpectedOutcome)) if(report.tags_workflow?.[key] !== value) throw new Error('Tags outcome missing: '+key);
    const evidence = tagSourceContracts.map(([path,symbol,length]) => source(report,path,symbol,length));
    return tagDraftPages().map(page => ({...page, status: 'verified', reason: '', evidence,
        screenshots: page.imageNumbers.map(number => report.screenshots[number-1])}));
}

// Discovery baseline only; this metadata cannot grant source or workflow verification.
export const tagReviewedSources = Object.freeze({
    "UI/src/components/Sidebar.svelte": "5fd791241452c301697730caf21a841c29a186876c33834b579d7846610d2568",
    "UI/src/routes.js": "58bddc98548095f14f39fa3141277de8cd00e2133faf49f6761131b109d254b0",
    "UI/src/utils/Permissions.js": "100a24508b57b6c73a323ab4f128ee2c79aef3212a2cd0178172199d59e51854",
    "UI/src/routes/association/Members/MembersList.svelte": "2205d4071d49dc3c96bad02299dd157668037807f33811b395f811197841650a",
    "UI/src/components/tables/BKNDatatable.svelte": "a236f1526b3033905925d3354b4361907c2f3e96699efc1e59cc1e927c6b9692",
    "BE/application/views/subscriptions_views.py": "d7022114e515be2da57e83b59fbe8042b06a273b61db7030839b31c1297022dc",
    "BE/application/services/subscription_service.py": "95a5be71f2a72859d80e4f19dbcbcb2c8e61d43180152e7258cffe7ce772201d",
    "BE/application/models/subscriptions_models.py": "b6b592e5ced8b685df6196e877ad75679bdf650c0d79774de9c537455eab465a",
    "BE/application/permissions_registry.py": "b8318ad63daa9452ac736b2152449b14c49f73bc1417622a4181b7c3ceb3b1fa",
    "BE/application/urls.py": "b475e7ed8c26a891854c4021d7308aefb192d2a26df8b3ee916a33f88e7cc359"
});

// Pure discovery never calls verified builders or fabricates source evidence.
const editorialDescriptor = (page, refs, reviewed, recipe_module, status = page.status, reason = page.reason) => ({
    path: page.path, id: page.id, title: page.title,
    status: ['unsupported', 'needs_external_verification'].includes(status) ? status : 'pending',
    reason: reason || 'Editorial draft metadata only; implementation and workflow proof remain required.',
    recipe_module, verified: false,
    source_contracts: refs.map(([path, symbol, length]) => ({path, symbol, length, reviewed_sha256: reviewed[path]})),
});

export function tagEditorialContracts() {
    const module = "docs/manuale/tag-recipes.mjs";
    return tagDraftPages().map(page => editorialDescriptor(page, tagSourceContracts, tagReviewedSources, module));
}
