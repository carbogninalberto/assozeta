# Manuale d’uso: lavoro necessario e criteri di consegna

La fonte dei capitoli è il repository locale `../manuale` (`Bakney/manuale`).
La bozza completa conserva `mint.json`, frontmatter, capitoli, sezioni e componenti
MDX. La fonte serve per struttura e stile; le affermazioni devono essere verificate
nel codice Assozeta e negli scenari reali.

## Preparazione della release

L'obiettivo della release è distribuire il manuale italiano già supportato,
senza estendere la copertura. Il resoconto storico sotto conserva la verifica
originale; non certifica automaticamente revisioni successive.

La produzione usa il volume persistente `manuale_data` condiviso dai servizi
backend. La sincronizzazione verifica hash e formato delle immagini prima di
sostituire atomicamente l'indice. Il lettore autenticato controlla nuovamente gli
hash; un aggiornamento fallito conserva il pacchetto precedente compatibile.
Le build escludono pacchetti locali e file di versione dello sviluppatore.

Il comando `./selfhost/bin/assozeta sync-manual` aggiorna il pacchetto senza
migrazioni o seed. Avvio e aggiornamento tentano la sincronizzazione quando
configurata, senza impedire l'avvio dell'applicazione in caso di errore.
Restano obbligatori i controlli di pubblicazione, revisione esatta e versione.
Vedi [installazione e aggiornamenti](README.md#installazione-in-produzione-e-aggiornamenti).

Criteri di consegna:

1. Eseguire i test backend su PostgreSQL isolato, i test frontend e le verifiche
   operative, incluse installazione pulita, aggiornamento e riavvio.
2. Creare commit separati e coerenti per correzioni, integrazione, strumenti e
   documentazione, verificando prima lo stato dei remoti.
3. Verificare il manuale sulla revisione applicativa finale committata; riusare
   prove precedenti solo quando il runner ne conferma la compatibilità.
4. Verificare lettore, immagini autenticate, navigazione, ricerca, apertura in
   nuova scheda e assistente senza provider tramite browser reale.
5. Committare i byte esatti del manuale risultante nel repository del manuale,
   preparare il pacchetto e pubblicare i branch di lavoro autorizzati.
6. Prima dell'attivazione in produzione, pubblicare separatamente sito e prove,
   verificare la revisione distribuita e promuovere il corpus tramite gli
   strumenti esistenti. Un pacchetto `prepared` non equivale a `published`.

I log locali e il piano dei commit sono conservati nella directory ignorata
`quality-reports/manuale-release-readiness/`. Conservare prove e backup fino
alla pubblicazione dei branch. Le esecuzioni finali contengono i riferimenti
esatti ai commit e i risultati aggiornati, senza modificare le prove storiche.

## Retained verified local delivery — 5 October 2026

The completed run **`ace5b433bd13`** is installed in the ordinary development
reader and the existing MCP/assistant as a **development-local v0.0.0** corpus.
Open **http://localhost:5001/#/manuale**. Both the application sidebar and header
open the manual in a new tab and preserve the original application page/state.
The verified installation replaced the reader-only draft preview.

- **60/60 real workflows passed**, with 700 captured checkpoints: 52 workflows
  retained through independent compatibility validation and eight freshly rerun.
- **434 verified Italian sections**, available across 42 readable pages from the
  complete 43-page/507-section inventory.
- **73 explicit gaps** remain excluded from verified instructions: 61 require
  external-service evidence and 12 describe unsupported procedures.
- Actual Mintlify rendering passed 434 anchors and 968 displayed image references;
  1,402 section/image layouts and 42 page screenshots are retained for review.
- The embedded application reader passed all **434 sections and 982 image
  references**, exact prose/block checks, enlargement dialogs, deep links,
  supported/unsupported searches, mobile navigation, and both new-tab entry points.
- Live MCP/Agent evaluation passed **50 supported questions**, five unsupported
  questions, public/owner permission checks, existing association-data access,
  and the unconfigured-provider fallback. The embedded real WebSocket chat also
  answered a supported question and abstained for an unsupported question with
  no AI provider configured.
- Reader polish checks passed centered icons/search, a single focus highlight,
  keyboard suggestions, fixed header, page-scroll reset, primary-color active
  contents, sidebar auto-scroll and mobile width.
- Regression results: **169 offline tests, 21 backend tests**, and previously
  completed unchanged UI (162 tests) and real frame checks (three tests).

The final ordinary-development smoke check also passed against
`http://localhost:5001`: 434 verified sections, authenticated screenshots, both
new-tab entry points with the application preserved, and supported/unsupported
answers through the existing chat. This development instance has AI enabled
(the button reads **Agente AI**); operation without a configured provider was
separately verified through the real isolated WebSocket and MCP/Agent checks.
The run-owned fixture services and data were removed after verification;
retained evidence and the installed development package remain available.

The reader, MCP and assistant share the same installed verified manual package.
This is local synchronization, not proof of an external support-site sync or
external publication. Runtime index caching notices file replacement, deletion
and corruption; source compatibility and screenshot-byte checks remain active.
The host builds the index using the sealed verifier and real Git worktrees.

The authoring source is
`quality-reports/manuale-development/dee70f925f6b/manual-authoring`; the verified
manual checkout is `quality-reports/manuale/ace5b433bd13/manual`, branch
`add/manuale-ace5b433bd13`. Both repositories retain explicit working-tree
provenance. Application branch: `add/manuale`. No commit, push or external
publication was performed. The ordinary development database was not seeded.

Each new checkpoint has exact consecutive same-state PNG equality. This does
**not** certify two independently repeated full runs. Independent full-run
repeatability remains unproven, especially generated identifiers and downloaded
PDF metadata; retained identical captures are not fresh-repeat proof.

Evidence is retained under `quality-reports/manuale/ace5b433bd13/` and
`quality-reports/manuale-finalization/`. Historical failures below describe
superseded attempts and do not change the current verified local delivery.

### Earlier partial delivery and verification history

The user requested a partial wrap-up instead of completing the original scope.
The fresh build in `10a7100d10b2` succeeded, and its first 11 workflows passed
with 55 real screenshots and no completed-workflow failures before deliberate
interruption. The final offline batch passed 157 tests without skips and
142 static checks. Fixture reset passed; owned services and credentials were
removed. The remaining requirements below are deferred, not accepted as done.

A review preview containing the Italian member-creation procedure and all
55 matching passed-workflow screenshots is available at
`quality-reports/manuale-preview/2026-10-04/index.html`, served locally at
`http://localhost:51200`. Desktop/mobile layout checks passed. It is a partial
review artifact, not an installed verified reader/MCP corpus. Source changes
remain in the current `add/manuale` checkout; no commit or push was performed.

This maintainer status supersedes the historical execution limitations below.
Docker, localhost listeners and Chromium pass `manuale.py doctor`; the live
development API's `/healthz` confirms version `v0.0.0`.

The latest sealed run, `404006c2a9d5`, completed all 60 workflows: 15 passed
and 45 failed. Its 103 screenshots from passed workflows were visually reviewed;
these are partial evidence, not the required 700-checkpoint acceptance. Fixture
reset passed. Only this run's services and volumes were removed; its snapshots,
masters and diagnostics remain available. No corpus was installed or published.

The next source chunk fixes payment filter reset, camp modal lifecycle, carnet
readiness and tenant checks, instructor loading and portal disposal, plus
evidence-backed browser controls and QR decoding. Thirty focused PostgreSQL
tests and four compiled UI regression scopes passed. Their dedicated test
database was destroyed without modifying the existing development database.
The final offline batch and fresh full capture run remain pending.

The Italian camp text now distinguishes the saved accounting receipt from PDF
generation and email delivery. All original section identities are preserved.
Reviewed source bindings retain prior hashes; publication verification remains
false until the full run succeeds. Detailed evidence is under
`quality-reports/manuale-continuation/runtime-diagnosis-404006c2a9d5/`,
`automation-audit/` and `guide-audit/camp-receipt-clarification/`.

Full coverage, two completed identical-input runs, actual invalidation/recovery,
and the same compatible corpus in the localhost reader, existing MCP and
assistant remain required. External publication is not authorized.

## Stato storico della stesura (superato dalla consegna sopra)

| Elemento | Stato |
|---|---|
| Copia completa del manuale locale | Preparata: 43 pagine, 507 sezioni, branch `add/manuale` |
| SVG provvisori | Preparati: 883 file, tutti 1920 × 1080 |
| Posizioni della creazione socio | 8 SVG inseriti nelle tre guide; flusso reale da eseguire |
| Approvazione iscrizione | Procedura e 4 SVG preparati; esecuzione reale e catture da completare |
| Contenuti riscritti | 507 sezioni nelle intestazioni originali; verifica runtime ancora necessaria |
| Flussi registrati | 60 ricette, 700 checkpoint reali previsti |
| Bacheca, carnet e presenze | Bozze e scenari preparati; varianti automatiche e casi ulteriori ancora aperti |
| Contabilità, bilancio, camp e calendari | Bozze e scenari reali preparati; catture da eseguire |
| Moduli di iscrizione e collegamento pubblico | Personalizzazione, copia link e iscrizione pubblica firmata preparati; esecuzione reale ancora necessaria |
| Fonte della pubblicazione | MDX del checkout manuale, con binding di revisione senza duplicare prosa nel manifest |
| Certificati e manutenzione corsi | Tre nuovi scenari: 11 guide complete, 64 checkpoint; stampa nativa e invio link restano esterni |
| Stampe e manutenzione istruttori | Due scenari, otto guide e 33 checkpoint preparati; prove reali da eseguire |
| Organizzazione e permessi | Bozze e scenari preparati; invio/accettazione inviti non eseguiti |
| Ordine delle immagini e componenti MDX | Renderer condiviso di passaggi, note, immagini e schede implementato |
| Checkout corrente | Codice del lettore e publisher copiato su add/manuale; corpus compatibile da rigenerare |
| Panoramica immagini | `SCREENSHOT-PLACEHOLDERS.html` nella copia di stesura |
| Backlog originale | Inventariate tutte le 43 righe; 94 schermate richieste |
| Pagina autonoma del manuale | Nuova scheda, navigazione propria; chrome applicativo escluso, autenticazione conservata |
| Lettore per capitoli e chat strutturata | Implementati nel checkout applicazione; verifica visiva da eseguire |
| Ricerca della guida completa | Peso del titolo aggiunto al ranking; prove offline superate per creazione e codice fiscale |
| Revisione completa dei contenuti | Stesura e binding delle 507 sezioni completati; prove runtime ancora aperte |
| Sostituzione con catture reali | Da completare prima della verifica finale |
| Aggiornamento del localhost | Da eseguire con il nuovo corpus verificato |

Controllo della sessione precedente, 2 ottobre 2026: Git, Node e Compose erano disponibili,
ma la sessione negava l'accesso al daemon Docker, l'apertura di porte locali e
l'avvio di Chromium. Anche PostgreSQL locale si interrompe durante `initdb`
per il divieto di creare memoria condivisa. Le prove sono conservate in
`quality-reports/manuale-development/dee70f925f6b/postgres-runtime-review/`.
`manuale.py doctor` e il controllo iniziale di `run` segnalano ora questi limiti
prima di preparare i checkout. Tre regressioni mirate verificano interruzione
anticipata, conservazione del corpus precedente e diagnostica senza credenziali.
Nessuna nuova cattura o installazione del corpus è stata eseguita in questo blocco.

La sessione di continuazione ha superato `manuale.py doctor`: Docker 29.7.2,
Compose 5.5.0, porte locali e Chromium funzionano. L’API di sviluppo dichiara
`RUNNING_VERSION=v0.0.0`. Il primo run isolato `eb9978b529b9` ha completato
build, migrazioni e seed; il primo scenario ha esposto la chiusura immediata
del menu tag durante lo scroll del pannello. La correzione ha superato una
regressione browser del menu reale. Le catture diagnostiche di quel run non
sono un corpus verificato; servizi e volumi sono stati rimossi, conservando
checkout e rapporti.

Le regressioni di presenze automatiche, scoping di assenze/giornate, camp,
comunicazioni, causali e fatture passive sono state eseguite su PostgreSQL
16.14. Anche il ramo JSON della bacheca è passato. Le correzioni delle fixture
e del rifiuto degli hash contraddittori sono verificate nelle rispettive prove
mirate; i risultati dettagliati sono in
`quality-reports/manuale-continuation/postgres-regressions/summary.json`.
Il lotto completo `0ebc6d66e47f` ha eseguito tutte le 60 ricette: 11 passate,
49 fallite. I 101 checkpoint acquisiti dai flussi passati sono stati esaminati
insieme ai master. Il lotto è diagnostico e non ha generato né installato un
corpus parziale. I rapporti identificano selettori obsoleti, caricamenti prematuri
e difetti applicativi; le correzioni devono passare un nuovo lotto con input
congelati. Una prova separata a zero catture ha confermato che il salvataggio
di una preferenza fiscale alterava il modello tessera non modificato.

Il blocco di correzioni comprende il caricamento coerente di profilo e permessi,
la firma S3 con orologio reale durante la cattura a data fissa, il filtro contabile
autorizzato, il caricamento delle tariffe istruttore e lo storico comunicazioni.
Due gruppi di regressioni PostgreSQL (39 e 11 casi) e una vera prova di
upload/lettura PDF S3 sono passati; i risultati e gli hash sono in
`quality-reports/manuale-continuation/runtime-diagnosis/`.
Copertura completa, ripetibilità, corpus corrente e installazione restano aperti.

## Contenuti utili

- [x] Tutte le 43 pagine di navigazione hanno una revisione delle proprie sezioni; questo non attesta i flussi reali.
- [ ] Ogni procedura spiega da dove iniziare, cosa compilare, cosa premere e come
  controllare che il risultato sia salvato; non solo un riassunto di poche righe.
- [ ] Prerequisiti, permessi, errori, limiti ed effetti su altri dati sono documentati
  quando risultano dal codice e dal comportamento verificato.
- [ ] FAQ e tutorial mantengono la loro funzione, senza duplicare frammenti minimi.
- [ ] Il lettore presenta i capitoli interi, nell’ordine del manuale originale.
- [ ] Le affermazioni obsolete sono corrette o escluse con una lacuna esplicita.

## UI e lettura

- [ ] Confronto visivo con la documentazione originale: navigazione per capitoli,
  titolo, descrizione, gerarchia delle sezioni e indice laterale coerenti.
- [ ] Spaziatura, larghezza delle righe, colori, contrasto e immagini rendono
  agevole leggere un capitolo lungo, su desktop e su telefono.
- [ ] La ricerca apre il capitolo completo e raggiunge la sezione pertinente;
  cercare non elimina gli altri capitoli dalla navigazione.
- [ ] Deep link, ricaricamento, avanti/indietro e navigazione tra capitoli funzionano.
- [ ] Chat: Markdown, passaggi numerati, immagini del relativo passaggio e citazione
  sono leggibili senza ripetere la risposta in un secondo blocco.
- [ ] L’ultimo risultato è realmente visibile su `localhost:5001/#/manuale`.

## Screenshot

- [x] SVG provvisori preparati per tutte le immagini dei capitoli, in una copia
  destinata alla stesura; nessuno SVG provvisorio entra nel corpus verificato.
- [ ] Tutti gli SVG sono sostituiti prima della verifica finale.
- [ ] Ogni cattura reale conserva un originale **1920 × 1080**, scala **1**.
- [ ] I ritagli mostrano controlli utili e mantengono originale, coordinate e hash.
- [ ] Testi, finestre e controlli sono leggibili: niente intere pagine ridotte,
  pannelli tagliati, stati intermedi o screenshot di una UI diversa.
- [ ] Le schermate seguono l’ordine della procedura; dati e token privati oscurati.

## Automazione e prova finale

- [ ] Un run isolato esegue seed, flussi, catture, aggiornamento MDX, indice e cleanup.
- [ ] Il backlog originale e `.manuale-authoring.json` non hanno omissioni silenziose.
- [ ] Ogni sezione verificata ha fonti del codice e le prove richieste dal suo tipo.
- [ ] Rendering completo, API autenticata, browser, MCP esistente e assistente
  usano lo stesso corpus compatibile; nessun placeholder viene presentato come prova.
- [ ] Ripetibilità, invalidazione, versioni incompatibili e assenza di prove verificate.
- [ ] Tutti i controlli vengono eseguiti dopo il blocco di implementazione;
  i risultati finali distinguono cosa è implementato da cosa è verificato.

La lista resta aperta finché non esistono le rispettive prove. I precedenti
controlli di un run conservato non provano le modifiche della nuova bozza.

## Lavoro ancora necessario dopo la stesura completa

- 58 flussi sono collegati a 317 procedure authored complete: filtri/esportazione, CSV, rate e default, ricevute/tessera, automazioni spente, disattivazione 2FA, manutenzione pagamenti, export associazione e compensi/report istruttori. Le altre procedure e i servizi esterni mantengono lacune esplicite.
- Sostituire tutti gli 883 SVG di pianificazione con catture dove richieste; 60 ricette e 700 checkpoint sono preparati, non nuovi risultati eseguiti.
- Materializzazione implementata per i soli flussi riusciti: note standard, frame nei passaggi, hash editoriale e provenienza del report. La sua esecuzione con catture reali resta da completare.
- Eseguire seed, backend, browser, MCP e risposte dell’assistente nell’ambiente isolato. L’audit di questo blocco conferma Docker negato e UI/API non raggiungibili da questa sessione.
- Installare un nuovo corpus compatibile prima di dichiarare aggiornate le guide su localhost.
- Riuso incrementale implementato con binding espliciti per ricetta e provenienza originale; verificare un aggiornamento reale prima di attestare questa parte dell’automazione.

- Inventario delle lacune di esecuzione per tutte le 507 sezioni: `workflow-coverage.json`; template candidati e procedure senza binding completo restano espliciti.
- Pacchetto di rilascio preparabile con commit effettivi e prove conservate; verifica HTTPS del sito, delle evidenze e della revisione implementata; pubblicazione remota e verifica reale della promozione restano da completare.
- CI conserva un riepilogo diagnostico senza errori testuali o input privati; la galleria del renderer include tutte le sezioni e le immagini verificate.
- Corretto il collegamento dell’importo modificabile al modello delle rate; la nuova procedura controlla payload e persistenza, ancora da eseguire nel browser.

- Il batch ripristina il seed anche dopo una cattura fallita: persone, caricamenti medici e token tessera temporanei restano nell’ambiente isolato. Le prove reali del reset sono ancora da eseguire.

- Revisione editoriale automatica opzionale implementata con `--edit-content`: Codex CLI in sola lettura, batch limitati anche dai byte effettivi, estratti di codice condivisi, citazioni controllate, patch rivedibili e rollback. Scoperta delle 507 sezioni originali; procedure modificate senza binding completo e affermazioni esterne restano bozze o lacune esplicite. Esecuzione reale del modello da completare nell’ambiente autorizzato.

- Nuovo blocco di guide: tag (assegnazione, scollegamento e cancellazione globale), archivio e preferenza automatica, rimozione del collaboratore/invito in attesa, attivazione/accesso 2FA e modifica di automazioni spente. Contratti di cattura e bozze MDX preparati; esecuzione reale ancora necessaria.
- Il profilo `collaborator-removal` crea solo record temporanei della fixture. Il reset normale conserva il lettore condiviso; due regressioni Django sulla ripetibilità e sulle collisioni sono preparate, da eseguire nel backend.

- Trasferimento accesso: il profilo `member-transfer` crea una destinataria adulta fittizia, conserva il lettore e ripristina i soli collegamenti della fixture. Rifiuta collisioni e proprietà estranee; due regressioni Django sono preparate, non eseguite.
- Persistenza comunicazioni e correzione manuale del codice fiscale: difetti circoscritti corretti; due regressioni API e i nuovi scenari reali attendono il backend/browser.

- Ultimo aggiornamento editoriale: 15 sezioni riviste nel nuovo blocco; 317 procedure complete predisposte, 60 ricette e 700 checkpoint. Lettore autonomo e link in nuova scheda già nel repository corrente. Sorgenti, MDX e binding controllati offline; catture reali e corpus localhost ancora da completare.

- Stato dopo questa integrazione: tutte le 507 sezioni hanno una destinazione esplicita: 117 descrizioni del codice, 317 procedure collegate a flussi reali ancora da eseguire, 61 verifiche esterne e 12 funzioni non supportate. Non restano lacune di pianificazione; le prove reali dell’intero manuale restano aperte.
- I flussi legacy con binding completo usano ora gli stessi contratti del codice già revisionati: cambiamenti alle fonti bloccano la pubblicazione, senza duplicare il manuale o creare un nuovo MCP.

- Nuovo blocco integrato: anagrafiche fornitori, fatture passive, iscrizioni camp, presenze automatiche, varianti carnet, lettura bacheca e anno fiscale. Otto nuove ricette; i flussi e gli SVG restano predisposti, non eseguiti.
- Rettificati i comandi di modifica/incasso delle fatture attive: non sono disponibili nella UI corrente. Camp pubblico e assenze hanno controlli circoscritti sui dati appartenenti all’account; regressioni backend predisposte, da eseguire.
- Inventario e pubblicazione condividono il catalogo delle descrizioni del codice: introduzione, sicurezza e piani self-hosted non vengono più omessi dal conteggio. Le lacune di esecuzione dei widget e dei servizi esterni restano esplicite.

- Verifica finale di questo blocco: tutte le 43 pagine MDX compilate e tutte le 507 identità conservate; binding e SVG controllati; 29 regressioni offline passate dopo una correzione mirata delle dipendenze; un solo build UI passato. Parte delle regressioni Django è stata ora eseguita nell’ambiente host; restano aperte le prove PostgreSQL e browser descritte sotto.

- Ultimo blocco: due nuove ricette condivise (liste bacheca e classificazione pagamenti), introduzione collegata al flusso già esistente, registro carnet e riepiloghi; 15 sezioni aggiornate senza nuove esecuzioni browser.
- Risultati host: 32 casi distinti passati tra API, lettore, MCP stdio, reset e carnet. Django host 5.2.17 differisce dal 5.2.1 fissato nel progetto; SDK MCP 1.6.0 soddisfa il vincolo del progetto. Il corpus usato dal test MCP è una fixture, non il nuovo manuale. Due test automatici restano bloccati dal lock PostgreSQL e la nuova precondizione JSON PostgreSQL richiede una prova sul database reale.
- Reset delle causali: elimina solo le causali temporanee dell’associazione dimostrativa, conserva baseline, causali globali e IVA condivisa e rifiuta riferimenti estranei. Ripetibilità e confini controllati nel database di test; esecuzione dell’intero run Docker ancora necessaria.

### Installed development preview (2026-10-04)

The ordinary development reader now has an explicitly marked Italian draft preview: 43 pages, 508 reader sections (including introductory content), and 55 screenshots from the 11 passed workflows. Open `http://localhost:5001/#/manuale`. The preview is reader-only; assistant/MCP verification requirements remain unchanged. The header resets inherited control margins to center the search field, button, title and return link.

Reinstall the available snapshot without modifying application records:

```sh
selfhost/bin/assozeta dev-compose exec -T api python manage.py install_manuale_preview \
  --manual-root /manual-code/quality-reports/manuale-development/dee70f925f6b/manual-authoring \
  --run /manual-code/quality-reports/manuale/10a7100d10b2
```

The command uses existing dependencies. Preview packages live in the ignored `BE/manuale/development/preview` directory; removing its `active.json` disables the preview. Production and isolated capture runtimes cannot load it. Ten focused preview/development tests passed. Browser verification uses the actual Vite reader component, application styles and authenticated live API.
