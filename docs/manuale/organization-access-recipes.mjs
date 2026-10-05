// Original-heading Italian drafts. Each projection binds its own complete run.
import {organizationAccessSourceContracts} from '../../selfhost/tests/browser/manuale/organization-access-sources.mjs';

export const organizationAccessCaptureSpecs = {
    'organization-settings': ['images/impostazioni/organizzazione-anno/', [
        'organization-info-original-address-and-owner-controls',
        'organization-address-website-abbreviation-and-sport-before-save',
        'organization-info-persists-after-authenticated-reload',
        'general-settings-fiscal-presets-and-separate-season',
        'custom-fiscal-year-starts-october-fifteen',
        'short-sport-season-and-registration-duration-before-save',
        'saved-fiscal-and-season-settings-persist-after-reload',
        'reader-can-consult-fiscal-season-settings-with-disabled-writes',
    ]],
    'collaborator-permissions': ['images/collaboratori/permessi/', [
        'existing-accepted-custom-collaborator-and-edit-action',
        'custom-reader-profile-shows-disabled-organization-fields',
        'owner-adds-only-update-settings-to-existing-custom-permissions',
        'collaborator-current-profile-after-grant-and-own-name-edit',
        'authorized-own-profile-write-persists-and-owner-organization-is-preserved',
        'revoked-update-permission-restores-reader-disabled-organization-controls',
        'invitation-form-prepared-with-fictitious-email-without-dispatch',
        'full-access-role-persists-and-allows-own-profile-write',
        'full-access-returned-to-original-custom-permissions',
    ]],
};
export const organizationAccessExpectedOutcomes = Object.freeze({
    'organization-settings': Object.freeze({organization: 'Associazione Sportiva Aurora',
        address: 'Via dello Sport 14', cap: '00101', website: 'https://aurora.example.test',
        abbreviated: 'Aurora', sport: 'Ginnastica', fiscal_type: '4', fiscal_month: 10, fiscal_day: 15,
        season_month: 9, season_day: 1, short_season: true, season_end_month: 6, season_end_day: 30,
        subscription_duration: 3, membership_duration: 3, persisted_after_reload: true,
        existing_registrations_unchanged: true, reader_settings_write_status: 403,
        denial_left_settings_unchanged: true, baseline_restored: true}),
    'collaborator-permissions': Object.freeze({existing_actor: true, initial_role: 3, final_role: 3,
        granted_permission: 'other.settings.update', only_requested_permission_added: true,
        persisted_after_owner_reload: true, reader_profile_write_before_status: 403,
        reader_permission_write_status: 403, authorized_own_profile_write_status: 200,
        authorized_name: 'Matteo', authorized_own_profile_persisted: true,
        owner_and_organization_preserved: true, revoked_existing_token_write_status: 403,
        baseline_permissions_restored: true, baseline_name_restored: true, invitation_prepared: true,
        invitation_dispatches: 0, invitation_records_unchanged: true}),
});
const pendingProof = '<Note>Bozza in attesa di prova: i passaggi e le immagini previste richiedono la verifica del flusso reale prima della pubblicazione.</Note>\n\n';
const externalReason = 'Invitation form only: no actual email dispatch, provider delivery, invitation acceptance or registration was demonstrated.';

export function organizationAccessDraftPages() {
    const image = (scenario, number, caption) => `<Frame>![${caption}](/${organizationAccessCaptureSpecs[scenario][0]}${number}.png)</Frame>`;
    const step = (title, body) => `<Step title="${title}">\n${body}\n</Step>`;
    const steps = (...items) => `<Steps>\n${items.join('\n')}\n</Steps>`;
    const intent = (scenario, id) => {
        if (scenario === 'organization-settings') {
            if (id === 'informazioni-dell-organizzazione') return 'organization.update';
            if (['anno-fiscale', 'come-cambiare-l-anno-fiscale'].includes(id)) return 'organization.fiscal.update';
            if (['stagione-sportiva', 'come-cambiare-la-stagione-sportiva',
                'durata-delle-iscrizioni-e-tesseramenti'].includes(id)) return 'organization.season.update';
            return 'organization.settings.read';
        }
        if (['invitare-un-collaboratore', 'come-inviare-un-invito'].includes(id)) return 'collaborators.invite';
        if (['introduzione', 'stato-degli-inviti'].includes(id)) return 'collaborators.read';
        return 'collaborators.permissions.update';
    };
    const page = (scenario, path, title, id, level, body, imageNumbers) => ({scenario, path, title, id,
        intent: intent(scenario, id),
        body: '#'.repeat(level) + ' ' + title + '\n\n' + pendingProof + body, imageNumbers});
    const org = 'organization-settings', access = 'collaborator-permissions';
    const doc = 'docs/impostazioni.mdx', faq = 'faq/come-cambiare-anno-sportivo-fiscale.mdx';
    const collaborators = 'docs/collaboratori.mdx', invitationFaq = 'faq/come-invitare-collaboratori.mdx';
    const orgOpen = step('Apri le informazioni dell’organizzazione', `Dal menu laterale apri **Profilo**, poi **Informazioni Account**.
La pagina **Informazioni** contiene i dati dell'**Account** e, più sotto, quelli dell'**Organizzazione**.
Nell'esempio il proprietario gestisce l'**Associazione Sportiva Aurora**, con sede a **Roma**.
${image(org, 1, 'Dati originali dell’organizzazione Aurora e controlli del proprietario')}`);
    const orgEdit = step('Aggiorna i recapiti e controlla i dati', `Scorri fino a **Organizzazione**. Nell'esempio cambiamo **Indirizzo** da **Via dello Sport 12** a **Via dello Sport 14** e **Cap** da **00100** a **00101**.
Inseriamo **Sito Web**: **https://aurora.example.test**, **Nome Abbreviato**: **Aurora** e **Sport**: **Ginnastica**.
Denominazione, Codice Fiscale e Città restano quelli dell'associazione dimostrativa.
Sono presenti anche Partita IVA, IBAN, WhatsApp, Federazione e Matricola federazione: usa i dati effettivi dell'associazione quando compili questi campi.
${image(org, 2, 'Indirizzo, sito web, nome abbreviato e sport prima del salvataggio')}`);
    const orgSave = step('Salva e verifica i dati dell’associazione', `Premi **Salva** nella parte superiore della pagina **Informazioni**.
Dopo la conferma, riapri la pagina e controlla i campi modificati: l'esempio mostra **Via dello Sport 14**, **00101**, **Aurora** e **Ginnastica**.
${image(org, 3, 'Informazioni dell’organizzazione salvate e presenti dopo il ricaricamento')}`);
    const settingsOpen = step('Apri le impostazioni generali', `Dal menu laterale apri **Profilo**, poi premi **Generali** nel menu del profilo.
La pagina contiene **Anno fiscale** e **Stagione sportiva** in due riquadri distinti.
Prima della modifica Aurora usa un anno fiscale **Gennaio–Dicembre** e una stagione che inizia il **1 Settembre**.
${image(org, 4, 'Anno fiscale e stagione sportiva sono configurati separatamente')}`);
    const fiscal = step('Scegli l’inizio dell’anno fiscale', `Nel riquadro **Anno fiscale**, scegli il periodo dal menu.
Nell'esempio selezioniamo **Altro Periodo (personalizzato)**, poi **Ottobre** e **15**.
Controlla il riepilogo del periodo prima di salvare: con questo inizio, l'anno termina il **14 Ottobre** dell'anno successivo.
${image(org, 5, 'Periodo fiscale personalizzato con inizio il 15 Ottobre')}`);
    const fiscalSave = step('Salva la configurazione fiscale', `Premi **Salva** in alto nella pagina, poi ricarica **Generali**.
Controlla che **Altro Periodo (personalizzato)**, **Ottobre** e **15** siano ancora selezionati.
${image(org, 7, 'Periodo fiscale e stagione salvati e riletti dal server')}`);
    const season = step('Imposta la stagione sportiva', `Nel riquadro **Stagione sportiva**, seleziona direttamente mese e giorno di inizio: nell'esempio **Settembre** e **1**.
Attiva **Dura meno di un anno** per indicare anche la fine e scegli **Giugno**, **30**.
Controlla nuovamente il giorno iniziale dopo aver cambiato il mese finale e verifica il riepilogo della stagione.
Se la stagione dura un anno intero, lascia disattivata l'opzione: la fine viene proposta come il giorno precedente all'inizio dell'anno successivo.
${image(org, 6, 'Stagione dal 1 Settembre al 30 Giugno e durata delle iscrizioni')}`);
    const duration = step('Scegli come calcolare le nuove durate', `Più sotto trovi **Durata delle iscrizioni** e **Durata dei tesseramenti**, con un menu per ciascuna voce.
Nell'esempio scegliamo **Dall'iscrizione al termine della stagione sportiva** in entrambi i menu.
La stagione indica il periodo dell'attività; questi menu stabiliscono da quando decorre la singola iscrizione o il singolo tesseramento.
${image(org, 6, 'Durata dalla data di iscrizione al termine della stagione configurata')}`);
    const seasonSave = step('Salva e ricontrolla la stagione', `Premi **Salva**, ricarica **Generali** e ricontrolla inizio, fine e durata.
Aurora mostra **1 Settembre–30 Giugno**, con **Dura meno di un anno** attiva.
${image(org, 7, 'Stagione sportiva e anno fiscale conservati dopo il ricaricamento')}`);
    const readOnly = `<Note>Per modificare l'organizzazione e questi periodi usa l'account del proprietario dell'associazione.
Un collaboratore autorizzato alla sola consultazione vede i valori, ma trova disattivati i controlli dell'anno fiscale e della stagione.
${image(org, 8, 'Collaboratore in sola lettura con controlli delle impostazioni disattivati')}</Note>`;
    const fiscalOptions = `| Opzione dell'anno fiscale | Periodo proposto |\n| --- | --- |\n| **Anno solare (Gennaio-Dicembre)** | 1 Gennaio–31 Dicembre |\n| **Anno Sportivo 1 (Settembre-Agosto)** | 1 Settembre–31 Agosto |\n| **Anno Sportivo 2 (Giugno-Maggio)** | 1 Giugno–31 Maggio |\n| **Altro Periodo (personalizzato)** | Mese e giorno scelti da te |`;
    const effects = `Il salvataggio cambia le impostazioni usate dall'associazione. Le date delle iscrizioni già presenti nell'esempio restano invariate.
La durata delle nuove iscrizioni dipende dalla regola selezionata in **Durata delle iscrizioni**; la durata dei nuovi tesseramenti usa il menu dedicato.
<Warning>Un nuovo inizio dell'anno fiscale cambia il periodo con cui vengono raggruppati i dati contabili. Non considerare i riepiloghi fiscali già consultati come fissi: ricontrollali dopo una modifica.</Warning>
<Note>Questa procedura verifica la configurazione salvata e la conservazione delle iscrizioni esistenti. Prima di usare una nuova configurazione per la contabilità della tua associazione, controlla il periodo effettivo con chi la gestisce.</Note>`;
    const listOpen = step('Apri i collaboratori', `Dal menu laterale apri **Collaboratori**.
La riga del collaboratore già collegato, **MARCO NERI**, mostra l'email **lettura@aurora.example.test**, lo stato **Accettato** e il ruolo **Personalizzato**.
Usa l'email per riconoscere la persona prima di modificare i permessi.
${image(access, 1, 'Collaboratore Marco Neri già collegato con accesso Personalizzato')}`);
    const permissionEdit = step('Apri la modifica del collaboratore', `Premi la matita **Modifica** nella riga del collaboratore.
Si apre **Modifica collaboratore**. Il menu **Permessi** propone **Accesso Completo** e **Personalizzato**.
Lascia **Personalizzato** se vuoi scegliere le singole autorizzazioni.
${image(access, 3, 'Finestra di modifica dei permessi del collaboratore esistente')}`);
    const permissionCheck = step('Aggiungi il permesso necessario', `Nell'area **Impostazioni**, lascia selezionato **Vedi le impostazioni** e aggiungi **Aggiorna le impostazioni**.
Nell'esempio non cambiamo gli altri permessi di consultazione del collaboratore e non gli assegniamo Accesso Completo.
I permessi possono riguardare lettura, creazione, aggiornamento o eliminazione, a seconda dell'area: controlla le caselle disponibili per la funzione che serve.
${image(access, 3, 'Permesso Aggiorna le impostazioni aggiunto alle autorizzazioni esistenti')}`);
    const permissionSave = step('Salva e verifica l’accesso della persona', `Premi **Salva** nella finestra di modifica.
Chiedi al collaboratore di riaprire l'applicazione e verificare la funzione interessata: i menu già aperti possono ancora mostrare i permessi precedenti.
Nell'esempio, Marco aggiorna il **Nome** del proprio account in **Profilo → Informazioni Account**, salva **Matteo** e lo ritrova dopo il ricaricamento.
${image(access, 4, 'Collaboratore autorizzato che prepara la modifica del proprio nome')}
${image(access, 5, 'Nome Matteo salvato nel solo account collaboratore')}
<Note>Questa prova riguarda il profilo personale del collaboratore. Il nome del proprietario e i dati dell'organizzazione restano invariati. Anche quando il modulo mostra i campi dell'organizzazione abilitati, in questa versione il loro salvataggio tramite il profilo del collaboratore non aggiorna l'associazione: per quei dati usa il proprietario.</Note>`);
    const revoke = step('Togli un’autorizzazione che non serve più', `Apri di nuovo **Modifica collaboratore**, deseleziona **Aggiorna le impostazioni** e premi **Salva**.
Nel nostro esempio il nome personale torna **Marco** prima di togliere il permesso e tutte le autorizzazioni iniziali vengono ripristinate.
Dopo aver riaperto l'applicazione, i controlli dell'organizzazione sono di nuovo disattivati; il salvataggio del profilo viene rifiutato anche con la sessione già esistente.
${image(access, 6, 'Permesso di aggiornamento rimosso e accesso in sola lettura ripristinato')}`);
    const invitation = steps(listOpen, step('Prepara il modulo di invito', `Premi **Collaboratore** in alto a destra per aprire **Creazione di un collaboratore**.
Inserisci l'**Email** e scegli **Accesso Completo** oppure **Personalizzato**.
Nell'esempio usiamo l'indirizzo fittizio **nuovo.collaboratore@example.test**, scegliamo **Personalizzato** e selezioniamo soltanto **Vedi le impostazioni**.
${image(access, 7, 'Modulo di invito preparato con email fittizia e permessi personalizzati')}
<Warning>La verifica si ferma alla preparazione del modulo. L'invio con **Salva**, la consegna dell'email e l'accettazione dell'invito richiedono una prova con il servizio email della tua installazione. Qui il modulo viene chiuso con **Chiudi** senza inviare.</Warning>`));
    const roleLimits = `<Note>**Accesso Completo** concede i permessi ordinari dell'associazione. Non assegna il ruolo di proprietario dell'installazione: operazioni quali il ripristino dei dati mantengono controlli separati sul proprietario o sull'amministratore.</Note>`;
    return [
        page(org, doc, "Informazioni dell'organizzazione", 'informazioni-dell-organizzazione', 2, steps(orgOpen, orgEdit, orgSave), [1, 2, 3]),
        page(org, doc, 'Anno fiscale', 'anno-fiscale', 2,
            "Per configurare l'anno fiscale dell'associazione, scegli il periodo in **Profilo → Generali** e salva la modifica.\n\n" + fiscalOptions + '\n\n' + steps(settingsOpen, fiscal, fiscalSave) + '\n\n' + readOnly, [4, 5, 7, 8]),
        page(org, doc, 'Stagione sportiva', 'stagione-sportiva', 2, steps(settingsOpen, season, seasonSave), [4, 6, 7]),
        page(org, doc, 'Durata delle iscrizioni e tesseramenti', 'durata-delle-iscrizioni-e-tesseramenti', 2,
            `| Regola | Decorrenza |\n| --- | --- |\n| **Un'anno solare dalla data di iscrizione** | Dalla data di iscrizione, per un anno |\n| **Da inizio stagione sportiva** | Dall'inizio al termine della stagione |\n| **Dall'iscrizione al termine della stagione sportiva** | Dalla data di iscrizione al termine della stagione |\n\n` + steps(duration, seasonSave) + '\n\n' + effects, [6, 7]),
        page(org, faq, 'Cambiare l\'anno sportivo e fiscale', 'cambiare-l-anno-sportivo-e-fiscale', 1,
            "L'organizzazione usa un periodo di riferimento per la contabilità e uno per l'attività sportiva. La guida mostra dove trovare i due riquadri, leggere i valori e salvare la scelta adatta all'associazione.\n\n" + steps(settingsOpen), [4]),
        page(org, faq, 'Qual è la differenza?', 'qual-e-la-differenza', 2,
            '| Periodo | Uso |\n| --- | --- |\n| **Anno fiscale** | Periodo di riferimento dei riepiloghi contabili |\n| **Stagione sportiva** | Periodo dell’attività usato dalle regole di durata di iscrizioni e tesseramenti |\n\n' + steps(settingsOpen) + '\n\n<Info>I periodi possono essere diversi: nell’esempio la stagione inizia il 1 Settembre e il nuovo anno fiscale il 15 Ottobre.</Info>', [4]),
        page(org, faq, 'Opzioni disponibili', 'opzioni-disponibili', 2, fiscalOptions + '\n\n<Note>Queste quattro opzioni appartengono all’**Anno fiscale**. Per la **Stagione sportiva** scegli direttamente mese e giorno; per una stagione più breve attiva **Dura meno di un anno** e indica anche la fine.</Note>\n\n' + steps(fiscal, season), [5, 6]),
        page(org, faq, "Come cambiare l'anno fiscale", 'come-cambiare-l-anno-fiscale', 2,
            "Per configurare l'anno fiscale, apri **Profilo → Generali** e seleziona il periodo fiscale desiderato.\n\n" + steps(settingsOpen, fiscal, fiscalSave) + '\n\n' + readOnly, [4, 5, 7, 8]),
        page(org, faq, 'Come cambiare la stagione sportiva', 'come-cambiare-la-stagione-sportiva', 2, steps(settingsOpen, season, duration, seasonSave), [4, 6, 7]),
        page(org, faq, 'Cosa cambia dopo la modifica', 'cosa-cambia-dopo-la-modifica', 2, effects + '\n\n' + steps(seasonSave), [7]),
        page(access, collaborators, 'Introduzione', 'introduzione', 2,
            'I collaboratori lavorano sui dati della stessa associazione con le autorizzazioni assegnate dal responsabile. Questa procedura modifica un collaboratore già collegato.\n\n' + steps(listOpen) + '\n\n' + roleLimits, [1]),
        page(access, collaborators, 'Invitare un collaboratore', 'invitare-un-collaboratore', 2, invitation, [1, 7]),
        page(access, collaborators, 'Livelli di permessi', 'livelli-di-permessi', 2,
            '| Livello | Scelta nel modulo |\n| --- | --- |\n| **Accesso Completo** | Permessi ordinari completi sulle funzioni dell’associazione |\n| **Personalizzato** | Caselle per le singole autorizzazioni |\n\n' + steps(permissionEdit) + '\n\n' + roleLimits, [3]),
        page(access, collaborators, 'Accesso Completo', 'accesso-completo', 3,
            'Il menu propone **Accesso Completo**. Valuta questa scelta solo se la persona deve usare tutte le funzioni ordinarie dell’associazione.\n\n' + steps(permissionEdit) + '\n\n' + roleLimits, [3]),
        page(access, collaborators, 'Personalizzato', 'personalizzato', 3,
            'Scegli le autorizzazioni necessarie per ciascuna area. **Seleziona tutto** e **Rimuovi tutto** agiscono sulla selezione: prima di salvare ricontrolla le caselle.\n\n' + steps(permissionCheck, revoke), [3, 6]),
        page(access, collaborators, 'Stato degli inviti', 'stato-degli-inviti', 2,
            'La colonna **Invito** della riga di Marco Neri mostra **Accettato**, perché il suo account è già collegato all’associazione.\n\n' + steps(listOpen) + '\n\n<Note>La creazione di inviti **In attesa**, la scadenza e l’accettazione tramite email non sono state eseguite in questa prova. Per questi passaggi serve la verifica del servizio email dell’installazione.</Note>', [1]),
        page(access, collaborators, 'Modificare i permessi di un collaboratore', 'modificare-i-permessi-di-un-collaboratore', 2,
            steps(listOpen, permissionEdit, permissionCheck, permissionSave, revoke), [1, 3, 4, 5, 6]),
        page(access, invitationFaq, 'Invitare un collaboratore', 'invitare-un-collaboratore', 1,
            'Puoi preparare un invito dalla lista collaboratori, scegliendo le autorizzazioni della persona. L’invio e l’accettazione via email richiedono una verifica separata.\n\n' + invitation, [1, 7]),
        page(access, invitationFaq, 'Come inviare un invito', 'come-inviare-un-invito', 2, invitation, [1, 7]),
        page(access, invitationFaq, 'Livelli di accesso', 'livelli-di-accesso', 2,
            'Il menu **Permessi** propone **Accesso Completo** e **Personalizzato**. L’esempio modifica un accesso Personalizzato già esistente.\n\n' + steps(permissionEdit, permissionCheck) + '\n\n' + roleLimits, [3]),
        page(access, invitationFaq, 'Stato degli inviti', 'stato-degli-inviti', 2,
            'Per un account già collegato la lista mostra **Accettato**; questo è lo stato di Marco Neri nell’esempio.\n\n' + steps(listOpen) + '\n\n<Note>Gli stati degli inviti inviati via email richiedono una prova di invio e accettazione, che non è compresa in questa procedura.</Note>', [1]),
        page(access, invitationFaq, 'Modificare i permessi', 'modificare-i-permessi', 2,
            steps(listOpen, permissionEdit, permissionCheck, permissionSave, revoke), [1, 3, 4, 5, 6]),
    ];
}

export function organizationAccessPages(id, report, source) {
    if (!organizationAccessCaptureSpecs[id]) return [];
    if (report.id !== id || report.status !== 'passed' || report.backend !== 'real'
        || report.fixture_version !== 8 || report.fixture_profile !== 'baseline'
        || report.capture_format !== 'full-hd-v1' || report.viewport?.width !== 1920
        || report.viewport?.height !== 1080 || report.device_scale_factor !== 1)
        throw new Error('Organization/access requires the passed real v8 baseline Full HD scenario');
    const [prefix, checkpoints] = organizationAccessCaptureSpecs[id];
    if (report.screenshots.length !== checkpoints.length || report.screenshots.some((capture, index) =>
        capture.path !== prefix + (index + 1) + '.png' || capture.checkpoint !== checkpoints[index]
        || capture.master?.width !== 1920 || capture.master?.height !== 1080))
        throw new Error('Organization/access captures differ from the reviewed step checkpoints');
    const outcome = report[id.replaceAll('-', '_')];
    if (Object.entries(organizationAccessExpectedOutcomes[id]).some(([key, value]) => outcome?.[key] !== value))
        throw new Error('Organization/access outcomes differ from the reviewed workflow');
    if (id === 'collaborator-permissions' && !report.external_gaps?.some(gap =>
        gap.operation === 'collaborator-invitation-delivery-and-acceptance' && gap.status === 'needs_external_verification'))
        throw new Error('Invitation provider gap must remain explicit');
    const evidence = organizationAccessSourceContracts[id].map(args => source(report, ...args));
    return organizationAccessDraftPages().filter(page => page.scenario === id).map(({scenario, imageNumbers, ...page}) => {
        const external = id === 'collaborator-permissions' && (page.id === 'invitare-un-collaboratore'
            || page.id === 'come-inviare-un-invito');
        return {...page, body: external ? page.body : page.body.replace(pendingProof, ''), evidence,
            screenshots: imageNumbers.map(number => report.screenshots[number - 1]),
            ...(external ? {status: 'needs_external_verification', reason: externalReason} : {})};
    });
}

// Discovery baseline only; this metadata cannot grant source or workflow verification.
export const organizationAccessReviewedSources = Object.freeze({
    "UI/src/components/Sidebar.svelte": "9f8344602ae269b5dbbf90dfd6f75e5154f3d5774ad544ac41105b74af2e333c",
    "UI/src/routes.js": "58bddc98548095f14f39fa3141277de8cd00e2133faf49f6761131b109d254b0",
    "UI/src/utils/Permissions.js": "100a24508b57b6c73a323ab4f128ee2c79aef3212a2cd0178172199d59e51854",
    "UI/src/routes/profile/ProfileMenu.svelte": "160192309bf2770c1bcbc88045b1f68963dd6df947ba3a56d0c01a85266dc57d",
    "UI/src/routes/profile/sections/Account.svelte": "d1ca0c771a8044a47daaad294ba07ead7618e8cda9c1123ea6dd9c3a9791c200",
    "BE/application/views/profile_views.py": "37aca983a9edb10673bd76786b7f8efce7adfeaa129e9d4848df85ead31936d1",
    "BE/application/serializers/auth_serializers.py": "edecff4a6618fc1862dc681056df9ab699de1a8cbb80cc004cf3f5642b7a1798",
    "BE/application/models/user_models.py": "3834a5655391ca750830d03175d7f663276697e50ab3fcfffcfd9ad45b7b931c",
    "BE/application/permissions_registry.py": "b8318ad63daa9452ac736b2152449b14c49f73bc1417622a4181b7c3ceb3b1fa",
    "UI/src/routes/profile/sections/Settings.svelte": "f620ddf62f5a04e8b8b978674e32338496dcd55e15a290a9cb8804b21e28fe2e",
    "BE/application/utils/api_utils.py": "5db286fc73e92356b476514f4d6561a3300b36668ee99831ca1b197835a5cabf",
    "BE/application/models/subscriptions_models.py": "b6b592e5ced8b685df6196e877ad75679bdf650c0d79774de9c537455eab465a",
    "BE/application/views/subscriptions_views.py": "7ef946a94b5d6e55f1ccdfd2de22fa9532101449587cc08753c4bde36b133881",
    "BE/application/serializers/subscriptions_serializers.py": "1e53f7d66701653856b6d5701f5bd80a6d5febb06872a27abc9a50615859f451",
    "UI/src/routes/connectedCollaborators/ConnectedCollaborators.svelte": "e4a1f60f0fd4683722136602d0e0f3db8f67f9d5cb2b3b71972ae810f50eedf6",
    "UI/src/routes/connectedCollaborators/CollaboratorActions.svelte": "2df48f56f3e4035b34ca2db58fa83423b7db8f2cce72d983e5dd1c826907ca8b",
    "UI/src/routes/connectedCollaborators/modals/EditModal.svelte": "0dee9f7d4cbf0fbdda753774b5497998b80422504ae930712f84ef7ead47130b",
    "UI/src/components/PermissionsComponent.svelte": "7141ca3deab090a2364016d6d9139515e07a839f101d5ead2498f6446e5c7500",
    "BE/application/views/collaborator_views.py": "2f23bc4e052414615a267744cfbc97785cd65778f505651809bed97f1cf5bf3d",
    "BE/application/serializers/collaborators_serializers.py": "a79281391ff5d528e8e61fb693c47ac9f3711d181a1765a178f659495c3ec09f",
    "BE/instance/permissions.py": "ce413a53cb3dcf3cca2478c4df656078cd105f6faff868b53a4073e973885f58"
});

// Pure discovery never calls verified builders or fabricates source evidence.
const editorialDescriptor = (page, refs, reviewed, recipe_module, status = page.status, reason = page.reason) => ({
    path: page.path, id: page.id, title: page.title,
    status: ['unsupported', 'needs_external_verification'].includes(status) ? status : 'pending',
    reason: reason || 'Editorial draft metadata only; implementation and workflow proof remain required.',
    recipe_module, verified: false,
    source_contracts: refs.map(([path, symbol, length]) => ({path, symbol, length, reviewed_sha256: reviewed[path]})),
});

export function organizationAccessEditorialContracts() {
    const module = "docs/manuale/organization-access-recipes.mjs";
    return organizationAccessDraftPages().map(page => editorialDescriptor(page,
        organizationAccessSourceContracts[page.scenario], organizationAccessReviewedSources, module,
        page.scenario === 'collaborator-permissions' && ['invitare-un-collaboratore', 'come-inviare-un-invito'].includes(page.id)
            ? 'needs_external_verification' : page.status,
        page.scenario === 'collaborator-permissions' && ['invitare-un-collaboratore', 'come-inviare-un-invito'].includes(page.id)
            ? externalReason : page.reason));
}
