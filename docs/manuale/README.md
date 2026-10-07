# Manuale e conoscenza verificata

Il manuale utente rimane nel repository [Bakney/manuale](https://github.com/Bakney/manuale). Assozeta contiene l'ambiente dimostrativo, gli scenari browser, le evidenze del codice e la ricerca usata dall'MCP e dall'assistente esistenti.

Se i repository di input hanno metadati Git in sola lettura, aggiungere `--standalone-checkouts`: il runner crea cloni locali indipendenti dentro la directory del run, conserva il commit e l'origine effettivi, applica solo le modifiche dichiarate e registra `checkout_kind: standalone-local-clone`. Senza questa opzione usa i worktree collegati. Non richiede rete o token GitHub.

## Esecuzione locale

Controllo rapido dell'ambiente, senza creare worktree, dati o catture:

```sh
python3 docs/scripts/manuale.py doctor
```

Controlla Git, Node, Compose, accesso al daemon Docker, apertura di una porta
locale e avvio del Chromium installato nel progetto. Ogni processo ha un timeout;
il rapporto espone versioni e indicazioni, senza stampare configurazioni o
credenziali del daemon. Con `--output PERCORSO.json` conserva il risultato.
`run` esegue lo stesso controllo prima della preparazione e termina con codice
**2** se l'ambiente manca o impedisce l'esecuzione. Un run precedente e il suo
corpus restano intatti. Un esito positivo attesta i prerequisiti, non le guide.

Prerequisiti: Git, Python 3, Docker Compose, Node e le dipendenze del progetto browser. Il runner crea due worktree e un progetto Docker con database, archiviazione, immagini e porte dedicati. Compila il frontend reale prima di catturare le schermate. L'API usa un solo processo e un orologio controllato alle 12:00 UTC della data di riferimento, senza cambiare i controlli di autenticazione. Un worker Celery del progetto isolato esegue i task reali delle ricevute e del salvataggio dei PDF. Conserva le modifiche e i rapporti per la revisione.

```sh
npm --prefix selfhost/tests/browser ci
cd selfhost/tests/browser
npx playwright install chromium
cd ../../..
python3 docs/scripts/manuale-scaffold.py create --reference ../manuale --output quality-reports/manuale-authoring --branch add/manuale
python3 docs/scripts/manuale-scaffold.py apply-drafts --output quality-reports/manuale-authoring
python3 docs/scripts/manuale.py run --manual-repo PERCORSO_MANUALE_REVISIONATO --allow-dirty --standalone-checkouts
```

`create` e `apply-drafts` preparano la struttura e le bozze delle ricette iniziali.
Per il run completo scegliere il checkout del manuale con tutte le sezioni authored
revisionate, sul suo ramo `add/manuale`. Il preflight controlla questa condizione
prima dei servizi. La bozza locale può essere trasferita con un bundle Git;
il bundle contiene prosa, binding e SVG provvisori, non un corpus pubblicabile.

Per i soli test offline del manuale, installare la dipendenza host minima:

```sh
python3 -m pip install -r docs/manuale/requirements-offline.txt
python3 -m unittest discover -s docs/scripts/tests -p 'test_manuale*.py'
```

Per documentare intenzionalmente le modifiche locali non ancora committate, aggiungere `--allow-dirty`. I loro hash sono registrati come evidenza del working tree. `--application-ref` e `--manual-ref` selezionano le revisioni. `--keep-services` mantiene l'istanza disponibile per l'ispezione.

Il runner stampa la directory del risultato. I comandi `start`, `capture`, `generate`, `verify`, `index`, `evaluate`, `export` e `cleanup` accettano `--run DIRECTORY` per eseguire una fase su quel risultato. `verify` usa il renderer Mintlify `mint@4.2.955` in una copia temporanea: l'eventuale migrazione da `mint.json` a `docs.json` resta in quella copia. `cleanup` rimuove esclusivamente i servizi e i volumi del progetto registrato, lasciando i worktree e i rapporti. Le credenziali browser sono private e vengono eliminate al termine della pulizia.

La modalità incrementale seleziona le ricette dichiarate in [`recipes.json`](./recipes.json):

```sh
python3 docs/scripts/manuale.py run --manual-repo ../manuale --changed-since COMMIT_PRECEDENTE
python3 docs/scripts/manuale.py run --manual-repo ../manuale --manual-changed-since COMMIT_MANUALE_PRECEDENTE
```

Le dipendenze comuni, i cambiamenti di permessi e le dipendenze applicative sconosciute selezionano tutte le ricette. Anche i file eliminati sono registrati. Per conservare le guide invariate durante un aggiornamento, indicare un run precedente:

```sh
python3 docs/scripts/manuale.py run --manual-repo PERCORSO_MANUALE_REVISIONATO --changed-since COMMIT_PRECEDENTE --reuse-evidence DIRECTORY_RUN_PRECEDENTE
```

Il nuovo corpus unisce le catture fresche ai risultati compatibili conservati. Il riuso richiede gli stessi input applicativi, configurazione, fixture, checkpoint e testo editoriale della singola ricetta; aggiunte, eliminazioni e cambiamenti delle dipendenze lo invalidano. `evidence-reuse.json` registra le decisioni e i rifiuti. Report, revisioni originali, identificativi di cattura e PNG conservano i propri byte. Il corpus risultante è legato alla revisione corrente: i controlli di versione in produzione restano esatti. Senza `--reuse-evidence`, la modalità incrementale include solo i flussi selezionati. Il run completo rimane disponibile.

Usando `--reuse-evidence` senza filtri incrementali, il runner controlla tutte le
ricette: conserva soltanto le catture compatibili e riesegue quelle rifiutate.
Può recuperare catture già validate e materializzate anche se il rendering del
run precedente è fallito. Non riusa il rendering fallito né installa quel corpus:
generazione, rendering, indice, MCP e lettore vengono verificati nel nuovo run.
La costruzione dell’indice usa il verificatore Python congelato sul computer
che contiene i worktree Git. Il controllo iniziale richiede le dipendenze di
`docs/manuale/requirements-offline.txt` nello stesso ambiente Python del runner.
L’immagine API riceve l’indice completo per le prove MCP e del lettore; non
richiede Git né collegamenti ai metadati Git del computer host.

Le citazioni conservano l’identità stabile della sezione e usano l’ancora misurata
nel renderer, comprese apostrofi tipografici e lettere accentate.

Prima di avviare servizi, `authoring-preflight.json` segnala le sezioni authored prive di testo revisionato. Una procedura selezionata senza il relativo binding interrompe la preparazione: scegliere il checkout o la revisione del manuale che contiene la stesura. Le altre sezioni mancanti restano lacune esplicite.

`workflow-coverage.json` inventaria tutte le sezioni della stesura e distingue
procedure collegate, riferimenti testuali, procedure senza scenario e vecchi
template ancora da controllare. È un piano di lavoro: `coverage.json` resta il
rapporto delle prove effettivamente eseguite. Nessun conteggio di preparazione
attesta catture o completezza del manuale.

Per ripetere una verifica senza ricompilare un frontend invariato, usare `--reuse-frontend DIRECTORY_DI_UN_RUN_VERIFICATO_NEL_BROWSER`. Il runner confronta revisione di base, ambiente di compilazione e hash di tutti gli input UI; copia il bundle soltanto se coincidono. Gli hash degli asset vengono conservati in `frontend-build.json`. Serve una verifica browser reale riuscita; un errore successivo in un altro scenario non impedisce di riutilizzare lo stesso bundle, mentre scenari, renderer e MCP vengono comunque verificati di nuovo.

Ogni nuovo checkpoint attende che le richieste di rete siano terminate e verifica
la stabilità dello stesso stato con due catture consecutive identiche. Conserva
entrambi i master reali in `masters/` e `masters/repeats/`; l’indice ricontrolla
i relativi hash. Un massimo di cinque confronti limita l’attesa e un’interfaccia
che continua a cambiare fa fallire lo scenario. Questa prova verifica che il
singolo schermo sia fermo: non equivale alla ripetibilità di un intero flusso
eseguito di nuovo con nuovi identificativi o link temporanei.

Ripetendo `capture --run DIRECTORY` vengono conservate le catture precedenti e misurata la differenza dei pixel. Con input identici la soglia massima è 0,1%; il rapporto `variance.json` registra anche l'identità dei byte. Una variazione oltre soglia interrompe la generazione, conservando manuale e indice precedenti. Se gli input cambiano, il rapporto distingue quel confronto dalla ripetibilità a input invariati.

Il batch ripristina il seed prima di ogni flusso e dopo l'ultimo. Quando un
flusso fallisce, conserva la diagnostica e continua con gli altri flussi
indipendenti dopo un nuovo reset. `capture-execution.json` elenca ogni esito;
qualsiasi fallimento termina il batch con errore prima di generare il manuale.

Per confrontare **due run completi** con gli stessi input:

```sh
python3 docs/scripts/manuale-repeatability.py compare \
  --before DIRECTORY_PRIMO_RUN --after DIRECTORY_SECONDO_RUN \
  --output quality-reports/manuale-repeatability.json
```

Il runner registra `fixture-identity.json` prima della pulizia; per un run già
avviato che conserva `browser-input.json`, usare
`python3 docs/scripts/manuale-repeatability.py fixture --run DIRECTORY`.
Questo fingerprint confronta il contratto pubblico del seed e la sua sorgente,
senza salvare password o JWT; non è un digest completo del database. Il confronto
richiede renderer, MCP/assistente, lettore e scenari reali riusciti, controlla gli
hash dei PNG e dei master e misura la stessa soglia di 0,1%. Confronta inoltre
input, testo/struttura del corpus, dipendenze, risultati degli scenari e fixture.
Le identità normalizzate escludono solo nonce di run/cattura, URL della preview,
credenziali ruotate e byte delle immagini misurati separatamente. Gli hash
originali del corpus, dei report e dei PNG restano nel rapporto: non vengono
riscritti per far apparire identici due artefatti diversi. Hash dei download,
stati salvati e altri esiti restano confronti esatti. Una prova sintetica del
comparatore non attesta la ripetibilità del manuale reale.

## Revisione editoriale opzionale con Codex

L’esecuzione normale verifica la stesura esistente e non usa un modello. Per
richiedere anche una revisione MDX prima del preflight e delle catture:

```sh
python3 docs/scripts/manuale.py run --manual-repo ../manuale \
  --changed-since COMMIT_PRECEDENTE --edit-content \
  --editor-timeout 180 --editor-max-sections 12
```

Serve un **Codex CLI già installato**, con un accesso CLI esistente oppure
`CODEX_API_KEY` configurata esternamente per l’invocazione. Il runner non
installa il CLI, non crea credenziali e non esegue accessi al posto dell’operatore.
La [documentazione ufficiale della modalità non interattiva](https://learn.chatgpt.com/docs/non-interactive-mode)
descrive autenticazione e `--output-schema`.

`--editor-timeout` limita l’invocazione CLI a 180 secondi per impostazione predefinita;
`--editor-max-sections` limita il batch a 12 sezioni. Usare la selezione
incrementale per lavorare in batch contenuti e rivedere gli esiti prima di
estendere il lavoro. `--editor-profile` e `--editor-model` sono facoltativi:
indicare soltanto un profilo già configurato o un modello scelto esplicitamente.
Senza un profilo il CLI ignora la configurazione utente e usa i propri valori
predefiniti. Non viene inventato un profilo, un modello o un provider.

L’adattatore [`manuale-edit.py`](../scripts/manuale-edit.py) invia contesto strutturato a un processo Codex in sola lettura,
isolato dagli hook del progetto. Valida la risposta strutturata e applica le
modifiche MDX consentite nel checkout del run; poi esegue gli stessi controlli,
scenari e catture reali del percorso normale. Le proposte rimangono **bozze**:
non attestano la verità del modello, non creano nuove asserzioni degli scenari e
non sostituiscono la prova completa del flusso. Contratti delle fonti o simboli
cambiati possono interrompere il lavoro con diagnostica esplicita; l’indice
valido precedente resta conservato, soggetto ai suoi controlli di compatibilità.

In CI `workflow_dispatch.edit_content` è disattivato per impostazione
predefinita. `MANUAL_AUTO_EDIT=true` abilita l’opzione solo per push o esecuzioni
programmate sul ramo predefinito dell’applicazione. Un dispatch editoriale deve
usare quel ramo e omettere `manual_ref` oppure scegliere esattamente il valore
fidato di `MANUAL_REF` (in sua assenza, `main`). Pull request e
`repository_dispatch` eseguono soltanto la verifica, senza modello o chiave
editoriale, anche quando la variabile automatica è attiva.

Il workflow usa soltanto un CLI già disponibile e fallisce esplicitamente se
l’editing è richiesto ma il CLI manca. Il runner CI ospitato non è una promessa
di disponibilità del CLI: predisporre una versione fissata nell’ambiente fidato
prima di abilitare l’opzione. L’eventuale secret `CODEX_API_KEY` è limitato al
passo editoriale; il runner lo rimuove dal proprio ambiente dopo quella fase,
anche in caso di errore, prima di build e processi browser/backend. Non salvare
autenticazione, chiavi o log editoriali privati negli artefatti pubblici. Il
workflow conserva i permessi Git in sola lettura e non pubblica le modifiche.
Un runner che esegue anche codice di pull request non deve conservare accessi
CLI o chiavi persistenti: fornire la credenziale soltanto al passo fidato.

## Artefatti

- `run.json`: revisioni, provenienza delle modifiche locali, porte, progetto e ramo del manuale.
- `runtime-preflight.json`: prerequisiti runtime del comando completo; non è una prova dei flussi.
- `authoring-preflight.json`: disponibilità delle sezioni revisionate prima dell’avvio dei servizi.
- `evidence-reuse.json`, `evidence-origin/`: compatibilità per ricetta e manifest originali, quando richiesto il riuso.
- `tags-create-assign.json`: verifiche del browser reale, dipendenze del codice e screenshot.
- `members-approve.json`: richiesta creata e firmata tramite il vero modulo, conferma dell’approvazione, stato accettato dopo ricaricamento, quota ancora da incassare; verifica il collaboratore in sola lettura e il rifiuto di una seconda approvazione.
- `members-create.json`: profilo e scelta dell’account, dati anagrafici, calcolo del codice fiscale, firma dimostrativa, passaggio dei documenti, riepilogo e salvataggio; verifica lo stato in attesa e il pagamento non incassato, più il divieto del collaboratore in sola lettura.
- `members-search.json`: ricerca di un nome, nessuna corrispondenza e ripristino dell'elenco.
- `payments-create-edit-approve.json`: nuova entrata in contanti, modifica e incasso; verifica anche la ricevuta registrata senza PDF immediato e i divieti del collaboratore.
- `receipts-approve-download.json`: numerazione, generazione e recupero dei PDF mancanti, anteprima, copia del link e download reale del browser; nessun invio di email.
- `manifest.json`: sezioni, evidenze e inventario completo della navigazione/backlog.
- `coverage.json`: copertura dimostrata e pagine ancora da verificare.
- `index.json`: corpus verificato, con identità stabile e indice per la ricerca.
- `render.json`, `renders/index.html`: verifica del renderer e galleria Full HD per ogni sezione e immagine verificata, da ispezionare visivamente.
- `evaluation.json`: domande italiane, citazioni e astensione attraverso MCP stdio e assistente.
- `export/`: MDX, immagini, patch binaria e rapporti da revisionare, senza credenziali o log privati.
- `variance.json`: confronto misurato delle catture ripetute, quando disponibile.
- `application/` e `manual/`: worktree di esecuzione e modifiche del manuale da revisionare.
- `diagnostics-public/summary.json`: stato e hash dei report in caso di errore CI; esclude errori testuali, token, immagini non verificate e download privati.

I file di configurazione, le sessioni e `execution.log` restano nella directory ignorata `quality-reports/manuale/`. Il log può contenere dettagli diagnostici privati: non distribuirlo come parte del corpus pubblico.

## Installazione nel lettore di sviluppo

Per rendere disponibile il risultato nell'istanza di sviluppo già in esecuzione:

```sh
python3 docs/scripts/manuale.py run --manual-repo ../manuale --allow-dirty --release v0.0.0 --install-dev
# Oppure, dopo una verifica completa della stessa versione e dello stesso checkout:
python3 docs/scripts/manuale.py install-dev --run quality-reports/manuale/ID_RUN
```

`--release` deve coincidere con `RUNNING_VERSION` dell'API di destinazione. L'installazione aggiorna soltanto il pacchetto locale ignorato `BE/manuale/development/` e applica il mount del codice all'API di sviluppo; non esegue migrazioni o seed nel database esistente. Il lettore e l'MCP leggono lo stesso pacchetto, con citazioni interne e PNG autenticati. Il puntatore viene sostituito soltanto dopo verifiche browser, renderer, MCP/assistente, hash del checkout e immagini riuscite. Ogni richiesta controlla versione e dipendenze applicative delle singole sezioni: le guide cambiate vengono escluse, quelle ancora applicabili restano leggibili. Gli hash del tooling rimangono un controllo di generazione e installazione.

Questo pacchetto conserva esplicitamente la provenienza del working tree e lo stato `development-local`. È disponibile soltanto in sviluppo, senza una revisione di produzione configurata; non viene accettato dalla sincronizzazione o dalle istanze di produzione. Un indice di rilascio configurato esplicitamente ha precedenza.

## MCP e assistente

I nuovi strumenti `search_manual`, `get_manual_section`, `get_manual_evidence` e `get_manual_gaps` sono registrati nell'MCP esistente. Lo stesso elenco è usato dall'assistente. Le domande esplicite sulle procedure recuperano testo verificato e producono una risposta estrattiva con citazione, senza affidare a un modello l'invenzione di nuovi passaggi. `get_manual_gaps`, disponibile soltanto all'identità fidata del proprietario, mostra inventario, sezioni non verificate, vincoli esterni e fonti cambiate; il parametro facoltativo `page` restringe la diagnostica a un percorso MDX. I client stdio non possono accedere a queste informazioni o fornire un'identità proprietario. La diagnostica può ispezionare un pacchetto precedente non più applicabile, mantenendo l'astensione della ricerca normale.

La ricerca combina BM25 e similarità semantica LSA sul corpus italiano verificato. LSA usa NumPy, già presente nell'ambiente backend tramite pandas; non richiede credenziali esterne. Il vocabolario dipende dal corpus e non equivale alla comprensione generale di un modello linguistico. Le domande prive di parole pertinenti non producono una procedura.

Configurare nel backend:

```dotenv
MANUAL_URL=https://manuale.bakney.com/docs/introduzione
MANUAL_INDEX_PATH=/percorso/index.json
MANUAL_APPLICATION_REVISION=COMMIT_IMPLEMENTAZIONE_VERIFICATA
MANUAL_SOURCE_ROOT=/percorso/repository
MANUAL_CORPUS_BASE_URL=https://raw.githubusercontent.com/Bakney/manuale/main/assozeta-index
```

`MANUAL_SOURCE_ROOT` è facoltativo per distribuzioni senza il repository completo; nell'ambiente di verifica controlla anche gli hash del codice e degli strumenti usati per produrre le evidenze. Le immagini backend di produzione leggono la revisione dal file `CODE_REVISION`, scritto durante la build; il workflow delle immagini passa il commit Git effettivo. Il corpus deve dichiarare lo stesso `RUNNING_VERSION`: usare `--release VERSIONE` durante la generazione. La ricerca di produzione rifiuta revisioni incompatibili, input non committati e corpus soltanto locali. In sviluppo è disponibile l'installazione esplicita descritta sopra, con verifica continua degli hash.

`MANUAL_CORPUS_BASE_URL` è facoltativo e disabilitato per impostazione predefinita. Senza un’origine configurata o un pacchetto locale compatibile, la navigazione del manuale e l’assistente solo manuale restano nascosti. Un provider AI configurato mantiene disponibile il proprio assistente. Gli endpoint di configurazione verificano la disponibilità locale senza scaricare il corpus; dopo una modifica ricaricare l’applicazione. Se configurato, la ricerca tenta un aggiornamento al massimo ogni cinque minuti per processo da `BASE/COMMIT_APPLICAZIONE/VERSIONE.json`. Accetta soltanto un corpus pubblicato, committato e compatibile; un download fallito o corrotto conserva l'indice precedente, che deve comunque superare i controlli di applicabilità. Per aggiornare subito usare `python manage.py sync_manuale_corpus`. Nessun argomento MCP può scegliere l'origine o la versione. Gli asset di rilascio nel repository pubblico e la loro promozione dopo la distribuzione del sito richiedono ancora il completamento della pubblicazione coordinata.

### Installazione in produzione e aggiornamenti

Il Compose di produzione conserva indice e schermate nel volume `manuale_data`,
montato in `/app/manuale` su API e servizi backend. L'immagine crea questa directory
con il proprietario non privilegiato dell'applicazione. La compilazione Docker
esclude i pacchetti locali `BE/manuale/`: il risultato di sviluppo non viene
distribuito accidentalmente né considerato una pubblicazione.

Dopo aver pubblicato e verificato il corpus della revisione e versione esatte,
impostare `MANUAL_CORPUS_BASE_URL` nel file `.env` dell'installazione e usare:

```sh
./selfhost/bin/assozeta sync-manual
```

Il comando usa l'immagine della versione configurata e il volume persistente,
senza migrazioni o seed. Eseguirlo prima di aprire il lettore per evitare che il
primo accesso debba scaricare tutte le schermate. Se la configurazione è cambiata,
ricreare i servizi con il normale comando `start` per applicarla anche ai processi
API. Quando l'origine è configurata, anche l'avvio e l'aggiornamento tramite CLI
tentano la sincronizzazione prima di avviare i servizi applicativi; un errore
viene segnalato senza impedire l'avvio dell'applicazione. La verifica della
pubblicazione rimane un passaggio separato: il comando
rifiuta pacchetti preparati o soltanto locali.

La sincronizzazione verifica ogni PNG tramite SHA-256 prima di sostituire
atomicamente l'indice. Le immagini sono memorizzate per hash e servite attraverso
gli endpoint autenticati del manuale, con un nuovo controllo dei byte a ogni
lettura. Un riavvio o una ricreazione dei container conserva il volume; un nuovo
corpus riusa le immagini già valide. Un errore mantiene l'indice precedente e le
sue schermate, sempre soggetti alla compatibilità della versione corrente.
Un volume nuovo può essere ripopolato dal corpus pubblico. Non cancellare il
volume durante un normale aggiornamento. Dopo ogni sincronizzazione riuscita,
la pulizia elimina soltanto i PNG per hash non più referenziati e vecchi di almeno
24 ore. Per immagini appena escluse dall’indice attivo, le 24 ore decorrono dal
ritiro, così le letture in corso conservano una finestra di tolleranza. Le immagini
attive, i file recenti, i link simbolici e i file estranei restano intatti. Un lock
sul volume serializza download, promozione e pulizia tra processi API e CLI.
Un errore di download non avvia la pulizia; un errore di pulizia dopo la promozione
viene segnalato nei log e ritentato alla prossima sincronizzazione riuscita.

Per una release successiva: aggiornare soltanto le guide interessate, eseguire la
verifica sul commit applicativo definitivo con `--reuse-evidence`, committare il
risultato esatto nel repository manuale, preparare il pacchetto, pubblicare e
verificarne la disponibilità, promuovere il corpus per quella revisione/versione
e infine eseguire `sync-manual`. Un commit o un push da soli non pubblicano il
corpus. Il contenuto non supportato rimane una lacuna esplicita.

Il trasporto implementato dal comando esistente è stdio:

```sh
cd BE
python manage.py run_mcp_server --association-id UUID_ASSOCIAZIONE --transport stdio
```

Un client usa quell'eseguibile e quegli argomenti per scoprire insieme strumenti dati e strumenti manuale. Le evidenze tecniche sono accessibili soltanto attraverso l'identità fidata del proprietario nell'assistente; un client stdio pubblico non può promuoversi a proprietario.

Configurazione JSON per un client che supporta `mcpServers`, da adattare ai percorsi
effettivi dell’ambiente backend:

```json
{
  "mcpServers": {
    "assozeta": {
      "command": "/PERCORSO/venv/bin/python",
      "args": ["/PERCORSO/assozeta/BE/manage.py", "run_mcp_server", "--association-id", "UUID_ASSOCIAZIONE", "--transport", "stdio"]
    }
  }
}
```

Il processo deve avere la stessa configurazione Django/database dell’istanza.
Con `tools/list` si scoprono gli strumenti esistenti e quelli del manuale; una
chiamata `tools/call` usa `{"name":"search_manual","arguments":{"query":"Come creare un socio?"}}`.
Non configurare il trasporto SSE: il comando esistente non lo implementa.

## Architettura e verifica proporzionata

Il percorso da mantenere è **MDX nel repository manuale → pacchetto generato → lettore/chat e MCP esistente**. Il pacchetto contiene testo, immagini, indice di ricerca e provenienza; il lettore e MCP condividono le sezioni applicabili. Le catture richiedono un solo ambiente dimostrativo isolato e restano 1920 × 1080 con eventuali ritagli registrati.

La validità in lettura è ora controllata per sezione: cambiare un test, il renderer o uno strumento di acquisizione non nasconde automaticamente tutte le guide. Una sezione viene esclusa se cambiano le sorgenti applicative che la documentano; i controlli comuni di accesso e versione restano attivi. Generazione e installazione mantengono la validazione stretta di tutti gli input prima di promuovere un nuovo pacchetto. L'indice conservato non viene riscritto o rietichettato come una nuova prova.

Per ogni aggiornamento usare un solo blocco finale: compilazione MDX e riferimenti, prove dei flussi modificati, verifica delle risposte interessate. Eseguire l'intera suite nelle modifiche al nucleo di recupero, autorizzazione o pubblicazione e nei controlli di rilascio. Non ripetere verifiche già superate senza una nuova modifica, un errore o una questione ancora aperta.

La generazione ora legge la prosa dal checkout MDX e verifica `.manuale-evidence.json`, che contiene soltanto hash, titoli e collegamenti alle ricette revisionate. Le ricette JavaScript conservano i modelli iniziali della stesura e i controlli sulle prove; non sostituiscono il testo editoriale durante la pubblicazione. Dopo aver controllato nel codice una modifica intenzionale al testo, registrarla senza modificare la ricetta:

```sh
python3 docs/scripts/manuale-scaffold.py review-content --output PERCORSO_MANUALE --section 'docs/corsi.mdx#creare-un-corso'
```

`review-content` non verifica un flusso e non rende pubblicabili gli SVG. `apply-drafts` serve a inizializzare le bozze e rifiuta di sovrascrivere prosa modificata o già mantenuta direttamente in MDX. Il run inizializza automaticamente una copia originale pulita nel proprio checkout, dopo la selezione dei flussi. Conserva un input MDX già revisionato senza sovrascriverlo; revisioni e hash degli input restano distinti dai file della stesura generata. `--allow-dirty` registra anche immagini, FAQ, tutorial e sidecar del manuale.

Quando cambia il modulo di una ricetta, ispezionare le fonti e correggere la
prosa coinvolta; poi aggiungere `--refresh-recipe-bindings` a `review-content`
per le sole sezioni esplicitamente elencate. Il comando conserva il vecchio
hash per revisione e rimuove un eventuale sigillo di cattura precedente. Non
aggiorna automaticamente i binding delle altre sezioni e non verifica il flusso.

Le modifiche ai moduli di una ricetta selezionano solo i flussi che li importano; i test e la documentazione del tooling non richiedono nuove catture. Gli helper comuni e le dipendenze applicative sconosciute conservano la selezione completa. `run.json` registra il motivo della selezione per ciascun percorso. Nessuna prova precedente viene rietichettata come nuova.

## Stesura e procedure aggiuntive

La stesura conserva 43 pagine e 507 sezioni del repository originale. Il registro
[`recipes.json`](./recipes.json) descrive i flussi eseguibili; il piano
[`screenshot-plan.json`](./screenshot-plan.json) prepara gli SVG Full HD.
I conteggi e le attività ancora aperte sono in
[`DELIVERY_CHECKLIST.md`](./DELIVERY_CHECKLIST.md).

Le procedure aggiuntive condividono [`authored-workflows.mjs`](./authored-workflows.mjs):
solo metadati di esecuzione, checkpoint, fonti e posizioni delle immagini.
Non duplicano la prosa MDX. Una sezione passa a verificata solo se il suo flusso
reale produce tutti i checkpoint e gli esiti richiesti. Le prove parziali non
promuovono una procedura intera; telefono, consegna email e servizi esterni
mantengono lacune esplicite quando non sono esercitati.

Dopo una cattura riuscita, il publisher sostituisce gli slot delle immagini e
inserisce i frame nei passaggi indicati. Rimuove solo le note standard di attesa
di quella procedura, conserva gli altri avvisi e aggiorna il binding con hash
del testo risultante, hash editoriale originale e provenienza del report.
Una nuova generazione non duplica le immagini.

La fixture 8 ruota una password privata per il solo proprietario dimostrativo
per consentire un vero nuovo accesso con OTP. Il reset disattiva il doppio
fattore soltanto su quell’account. Password, token e QR restano fuori da corpus,
report e immagini; gli input browser hanno permessi 0600.

La rimozione di collaboratori usa soltanto il profilo opt-in
`collaborator-removal`: un account temporaneo e un invito non accettato,
entrambi appartenenti al proprietario dimostrativo. Il profilo `baseline`
conserva il collaboratore di lettura usato dagli altri scenari e rimuove soltanto
i due identificativi temporanei di proprietà della fixture. Collisioni con
identificativi appartenenti a un altro proprietario vengono rifiutate.
La rimozione viene verificata riutilizzando il token temporaneo dopo l’azione;
nessun invito viene spedito durante la preparazione.

Il profilo `member-transfer` viene scelto dalla ricetta di trasferimento dell’accesso: prepara una destinataria adulta già esistente, senza inviare credenziali o email di benvenuto. Il reset ripristina i collegamenti della sola associazione fittizia e rimuove il destinatario e il suo canale di notifica. Identità o proprietà di altre associazioni causano un rifiuto, prima della cancellazione. Questo flusso non attesta trasferimenti per minori/tutori né consegna di notifiche a dispositivi esterni.

I flussi aggiuntivi coprono assegnazione e rimozione dei tag, archivio e relativa
preferenza, cancellazione di un collaboratore e di un invito in attesa,
attivazione del doppio fattore e accesso con OTP. L’app di autenticazione sul
telefono, lo scheduler dell’archivio e la consegna di inviti/email mantengono
lacune esplicite. Lo scenario delle automazioni modifica anche una regola già
salvata, mantenendola spenta: salvataggio e riapertura dell’oggetto aggiornato
sono distinti dall’invio dei messaggi.

La configurazione privata generata da `dev-config` usa `DEBUG=True` e
`RATELIMIT_ENABLE=False`; le prove di login con OTP non attestano il blocco
dei tentativi ripetuti nelle installazioni di produzione.

Le notifiche dei run dimostrativi in sviluppo usano il backend email in memoria.
Lo scenario dell’export verifica questo sostituto prima di creare lo ZIP; il
download viene controllato localmente ed eliminato, conservando solo hash e
conteggi. Lo ZIP contiene anche dati riservati e non entra nei materiali pubblici.
Questa prova non dimostra la consegna di email all’esterno.

Il manuale interno apre `/#/manuale` in una nuova scheda, con navigazione propria
senza layout della dashboard. Chat e manuale usano lo stesso renderer per
Markdown, passi, note e immagini, con dimensioni di lettura contenute e zoom.

## Verifica finale

Implementare il blocco completo, poi controllare MDX e binding, i test della parte
modificata e una build quando cambia la UI. Eseguire le procedure reali
interessate nell’istanza isolata e controllare il risultato in manuale, chat e
MCP prima di installare un nuovo pacchetto. Le prove offline non sostituiscono
la verifica browser e non autorizzano a rietichettare un vecchio corpus.

La skill riutilizzabile è
[`skills/assozeta-manuale/SKILL.md`](../../skills/assozeta-manuale/SKILL.md).
Per la scoperta locale, da un checkout Assozeta scrivibile:

```sh
mkdir -p .agents/skills
ln -s ../../skills/assozeta-manuale .agents/skills/assozeta-manuale
```

Il collegamento non sovrascrive un’installazione esistente. In alternativa,
collegare la stessa directory assoluta da `~/.agents/skills/assozeta-manuale`.
Codex legge `.agents/skills` del repository e dell’utente e segue i collegamenti
simbolici; invocare `$assozeta-manuale` e riavviare Codex se non compare.
[Documentazione ufficiale della scoperta delle skill](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills).
Queste sono istruzioni di installazione: il runner non modifica configurazioni
personali né installa credenziali.
Il goal e i criteri di consegna sono in
[`MANUALE_AUTOMATION_AND_MCP_RAG_GOAL.md`](../goal-prompts/MANUALE_AUTOMATION_AND_MCP_RAG_GOAL.md).
Il modello CI del repository manuale è
[`ci/notify-assozeta.yml`](./ci/notify-assozeta.yml); token, esecuzione GitHub e
pubblicazione del sito richiedono ancora l’integrazione prevista.
In Assozeta, la variabile repository `MANUAL_REF` seleziona la revisione della
stesura da verificare quando non è fornito un input manuale o un dispatch.
Il ramo locale deve essere reso disponibile al runner GitHub prima di usare
questa configurazione; nessun bundle locale viene pubblicato automaticamente.

Il secret `ASSOZETA_DISPATCH_TOKEN` appartiene al repository manuale: per il
dispatch verso Assozeta, usare un token fine-grained limitato al repository
destinatario con permesso **Contents: write**, come richiede l’
[endpoint GitHub repository dispatch](https://docs.github.com/en/rest/repos/repos#create-a-repository-dispatch-event).
Il template osserva anche i binding di revisione, sul ramo `main` e `add/manuale`.
Il workflow ricevente deve essere disponibile sul ramo predefinito per il dispatch,
come indicato negli [eventi GitHub Actions](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#repository_dispatch).
Configurazione, installazione del workflow e invio remoto restano da completare.

## Preparazione di un rilascio

Dopo un run valutato e il commit del suo esatto risultato nel repository manuale:

```sh
python3 docs/scripts/manuale-release.py \
  --run DIRECTORY_RUN_VALUTATO \
  --application-repo CHECKOUT_APPLICAZIONE_COMMITTATO \
  --manual-repo CHECKOUT_MANUALE_COMMITTATO \
  --output DIRECTORY_NUOVA_PACCHETTO \
  --release VERSIONE
```

Il comando controlla commit puliti, versione, byte MDX/PNG e sidecar, catalogo,
indice e rapporti riusciti. Conserva le prove originali e distingue la revisione
editoriale di input da quella finale. Il risultato è `prepared`, con un piano
di pubblicazione da revisionare: non esegue push o deploy e la sincronizzazione
di produzione lo rifiuta. Dopo la distribuzione autorizzata, lo stesso comando
può verificare la disponibilità usando soltanto richieste HTTPS pubbliche:

```sh
python3 docs/scripts/manuale-release.py --verify-availability \
  --prepared DIRECTORY_PACCHETTO_PREPARATO \
  --application-repo CHECKOUT_APPLICAZIONE_COMMITTATO \
  --manual-repo CHECKOUT_MANUALE_COMMITTATO \
  --output DIRECTORY_NUOVA_VERIFICA \
  --release VERSIONE \
  --evidence-base URL_HTTPS_PROVE_PUBBLICHE \
  --deployment-environment AMBIENTE_EFFETTIVO
```

Il rapporto distingue contenuti disponibili e revisione distribuita: controlla
sorgenti Git immutabili, ancore e prosa visibile delle sezioni, PNG e prove
pubbliche. L’output di promozione locale richiede anche il record del commit
distribuito con successo nell’ambiente configurato, tramite le
[API GitHub Deployment](https://docs.github.com/en/rest/deployments/deployments)
e [Deployment Status](https://docs.github.com/en/rest/deployments/statuses).
L'ispezione pubblica del 2 ottobre 2026 trova già un deployment Mintlify
nell'ambiente GitHub `staging`, con stato riuscito per il commit manuale
`0475c2c8b4d397982558c8453a4819587e7a9da9` e URL
`https://manuale.bakney.com` ([deployment e stato](https://api.github.com/repos/Bakney/manuale/deployments/3829319394/statuses)). Usare `--deployment-environment staging` soltanto
quando il deployment più recente attesta il commit finale che si vuole
pubblicare: il successo del vecchio manuale non verifica la nuova stesura.
In assenza della prova per quel commit il pacchetto resta `prepared`, con la
prova mancante nel rapporto e senza indice pubblicabile.
Questo controllo HTTP non sostituisce la verifica visiva e consente testo
aggiuntivo nella pagina; non attesta l’assenza di altre affermazioni non verificate.
Nessuno dei due comandi esegue push, deploy o installa il corpus.

Se arriva prima il codice applicativo, il corpus incompatibile resta escluso;
eseguire il run sul commit effettivamente distribuito. Se arriva prima il manuale,
conservare il pacchetto preparato e promuovere solo dopo la corrispondente versione
applicativa e la pubblicazione verificata. Un errore conserva il corpus precedente,
che resta soggetto ai controlli di compatibilità.

La scoperta editoriale include tutte le 507 sezioni originali: 505 hanno contratti delle sorgenti; due sezioni su infrastruttura e conformità restano lacune esterne senza una prova ricavabile dal codice. Questo inventario non verifica le procedure. Il run completo considera tutte le sezioni supportate, comprese quelle senza uno scenario di cattura. La modalità incrementale considera le dipendenze applicative e le pagine MDX cambiate. Ogni estratto di codice viene letto una volta e inviato una volta per batch; il runner riduce i batch quando il contenuto supera il limite del prompt.

I testi modificati senza un binding completo della procedura rimangono in attesa di prova, anche quando la bozza passa a un altro checkout o una cattura parziale riesce. Le affermazioni esterne o non supportate restano lacune esplicite: cambiare la sorgente non le rende verificabili dal modello. `editorial/manual.patch`, `editorial/tooling.patch` e `editorial/report.json` rendono le modifiche rivedibili; i log del modello restano privati. Il rapporto registra il numero di batch, le sorgenti ispezionate e le lacune esterne, senza attestare catture reali.
