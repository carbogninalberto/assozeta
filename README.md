<div align="center">
  <img src="UI/public/oem/assozeta/brand/logo.svg" alt="Logo Assozeta" width="220">

**Il gestionale open source per le associazioni sportive dilettantistiche**

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-6633cc.svg)](LICENSE)
[![Svelte 4](https://img.shields.io/badge/Svelte-4-ff3e00.svg?logo=svelte&logoColor=white)](https://svelte.dev/)
[![Vite 5](https://img.shields.io/badge/Vite-5-646cff.svg?logo=vite&logoColor=white)](https://vitejs.dev/)
[![Django 5](https://img.shields.io/badge/Django-5.2-092e20.svg?logo=django&logoColor=white)](https://www.djangoproject.com/)

<a href="https://t.me/+Cxbg24Az1S0wMjI0">
  <img src="https://img.shields.io/badge/Telegram-Unisciti_alla_community-26A5E4?style=for-the-badge&logo=telegram&logoColor=white" alt="Unisciti alla community Assozeta su Telegram">
</a>

[Segnala un problema](https://github.com/carbogninalberto/assozeta/issues)

</div>

## Contributors

<!-- contributors:start -->
<a href="https://github.com/carbogninalberto"><img src="https://avatars.githubusercontent.com/u/17618905?v=4&amp;s=64" width="64" height="64" alt="@carbogninalberto" title="@carbogninalberto"></a>
<!-- contributors:end -->

> [!IMPORTANT]
> Il repository comprende la UI Svelte in `UI/`, il backend Django in `BE/` e uno stack Docker completo in [`selfhost/`](selfhost/README.md).

## Il progetto

Assozeta è un'applicazione full-stack per digitalizzare la gestione quotidiana di Associazioni e Società Sportive Dilettantistiche. Nasce dall'esperienza di [Bakney](https://bakney.com) e rende disponibile alla community una base open source completa e personalizzabile.

Il progetto riunisce in un solo spazio anagrafiche, iscrizioni, corsi, presenze, documenti, pagamenti, contabilità e comunicazioni. La disponibilità effettiva delle funzioni dipende dal backend, dal piano e dalla configurazione dell'istanza e dai servizi esterni abilitati.

Il backend implementa export e import completi nel formato applicativo `bakney_sport_export_v1`. Non è dichiarata una compatibilità generale con backup storici o con formati prodotti da altre versioni. Gli export applicativi possono includere l'hash della password del proprietario per consentirne il ripristino: devono quindi essere protetti come dati sensibili.

## Funzionalità

### Associati e tesserati

- Anagrafiche di soci, atleti, minori e tutori
- Iscrizioni, rinnovi, tesseramenti e tessere digitali
- Importazione degli associati da CSV e XLSX
- Documenti, certificati medici e firme
- Tag, archivio e storico delle attività

### Corsi e attività

- Corsi, calendari, lezioni e rilevazione presenze
- Istruttori, collaboratori e sedi
- Carnet e pacchetti di ingressi
- Camp e ritiri sportivi
- Calendari pubblici e sincronizzazione con Google Calendar
- Check-in tramite QR eseguito da un operatore autenticato

### Pagamenti e contabilità

- Quote, scadenze, pagamenti e ricevute
- Entrate, uscite, conti, trasferimenti e riepiloghi di bilancio
- Ricevute e documenti contabili per clienti e fornitori
- Fornitori e clienti
- Stripe Connect e pagamenti online per i flussi supportati

### Iscrizioni e moduli online

- Moduli pubblici personalizzabili
- Form builder con campi, allegati e firme
- Iscrizioni individuali, multiple e familiari
- Raccolta digitale di dati e documenti

### Comunicazioni e controllo

- Invio email tramite SMTP
- Automazioni basate su eventi e pianificazione, con azioni email
- Dashboard, report, grafici ed esportazioni CSV/XLSX
- Notifiche in tempo reale tramite WebSocket
- Autenticazione a due fattori TOTP e audit log

## Architettura tecnica

### Frontend

| Area | Tecnologie principali |
| --- | --- |
| Framework e build | Svelte 4, Vite 5 |
| Routing | svelte-spa-router |
| UI e contenuti | Componenti Svelte, Bootstrap 4, TipTap |
| Grafici e calendario | ECharts, Chart.js, Event Calendar |
| Comunicazione | Axios, REST, WebSocket |
| App mobile | Capacitor |

### Backend

| Area | Tecnologie principali |
| --- | --- |
| API | Python 3.13, Django 5.2, Django REST Framework 3.16 |
| Realtime | Django Channels, ASGI, WebSocket |
| Processi asincroni | Celery, Celery Beat |
| Persistenza | PostgreSQL o SQLite, Redis |
| Storage | Object storage S3-compatible |
| Autenticazione | JWT con chiavi EdDSA, TOTP |
| Documentazione API | OpenAPI, Swagger UI, ReDoc |

Le integrazioni opzionali comprendono Stripe, SMTP, Google Calendar e un servizio esterno di rendering PDF. Le funzioni che le utilizzano richiedono credenziali e processi dedicati.

## Struttura del repository

```text
assozeta/
├── BE/                        # Backend Django e API applicative
│   ├── application/           # API e dominio applicativo
│   ├── communications/        # Email, notifiche interne e automazioni
│   ├── core/                  # Configurazione Django, ASGI e Celery
│   ├── docmanager/            # Documenti e rendering
│   ├── instance/              # Configurazione dell'istanza self-hosted
│   └── notifications/         # Notifiche realtime
├── UI/
│   ├── public/
│   │   └── oem/assozeta/    # Asset e manifest del brand
│   ├── scripts/              # Utility per CSS e build
│   ├── src/
│   │   ├── components/       # Componenti riutilizzabili
│   │   ├── layouts/          # Layout dell'applicazione
│   │   ├── routes/           # Pagine e aree funzionali
│   │   ├── store/            # Stato condiviso
│   │   └── utils/            # Client API e utility
│   ├── endpoints.js          # Catalogo degli endpoint consumati dalla UI
│   ├── oems.json             # Configurazione dei brand e degli host
│   ├── package.json
│   └── vite.config.js
├── selfhost/                  # Compose, configurazione e operazioni self-hosted
├── Makefile                   # Comandi di sviluppo locale
├── LICENSE
└── README.md
```

## Avvio locale

### Avvio completo con Docker

```bash
make dev
```

Il comando avvia UI, API, PostgreSQL, Redis, MinIO, renderer PDF, worker e scheduler Celery. La UI è disponibile su [http://localhost:5001](http://localhost:5001), con HMR Vite; Django viene ricaricato automaticamente quando cambiano i sorgenti Python.

Al primo avvio:

1. Esegui `make dev-config` per generare `selfhost/.env.dev` con segreti sicuri e commenti esplicativi. Le integrazioni opzionali possono restare vuote; in sviluppo si modificano normalmente solo le porte `DEV_*` e `VITE_USE_POLLING`.
2. Esegui `make dev`, attendi che venga stampato il **first-run setup token** e che i servizi risultino avviati. `make dev` genera automaticamente `.env.dev` anche se il primo comando è stato saltato.
3. Apri [http://localhost:5001](http://localhost:5001), lascia `localhost` come dominio e incolla nel campo **Token di Setup** il token esatto stampato dal comando.
4. Completa il wizard creando l'associazione e l'account proprietario, quindi accedi con l'email e la password scelte.

Il token non è una stringa arbitraria: viene generato in `selfhost/.env.dev`. Inserire un valore casuale causa una risposta HTTP `401` da `POST /api/instance/configure`. Per recuperare il solo valore del token:

```bash
awk -F= '$1 == "INSTANCE_SETUP_TOKEN" { print substr($0, index($0, "=") + 1) }' selfhost/.env.dev
```

Non è necessario eseguire un reset dopo un tentativo con token errato; basta tornare al primo passaggio e usare il valore corretto. Le istruzioni complete sono in [`selfhost/README.md`](selfhost/README.md#first-development-setup).

Durante lo sviluppo Vite inoltra automaticamente:

- `/api` verso il container Django, rimuovendo il prefisso `/api`
- `/ws` verso il container ASGI

Consulta [`selfhost/README.md`](selfhost/README.md) per i comandi di sviluppo, reset e diagnostica.

I canali WebSocket usati dalla UI sono `/ws/notifications/`, `/ws/health/`, `/ws/updates/` e `/ws/agent/`.

### Requisiti del backend

Il backend è configurato per essere eseguito sulla porta `8000`. Un'istanza completa richiede:

- Python 3.13 e le dipendenze definite in `BE/requirements.txt`;
- PostgreSQL, oppure SQLite per configurazioni limitate;
- Redis per cache, sessioni, Celery e Channels;
- storage S3-compatible per file e documenti;
- processi separati per API ASGI, worker Celery e Celery Beat;
- un servizio di rendering PDF per ricevute e documenti generati.

Lo stack di sviluppo genera automaticamente una configurazione locale in `selfhost/.env.dev` e inizializza database, storage e dati di riferimento.

Lo storage self-host usa MinIO su rete Docker privata: non viene pubblicato da Caddy e non usa ACL oggetto. Le firme delle iscrizioni vengono salvate con una chiave interna privata; eventuali record storici con `signature_url` pubblico continuano a essere letti. I comandi self-host di backup/restore sono specifici per il MinIO privato incluso: ripristinano database e albero MinIO completo, poi le migrazioni correnti convertono i backup pre-patch con `Subscription.signature` URL/base64 e recuperano in modo conservativo eventuali oggetti `subscriptions/<UUID>/signature_*.png` gia' presenti. I fallback URL restano supportati. Per provider pubblici compatibili S3, ad esempio DigitalOcean Spaces, il runtime applicativo abilita automaticamente `AWS_S3_USE_OBJECT_ACL` per gli endpoint `*.digitaloceanspaces.com`; l'impostazione puo' essere sovrascritta e `AWS_S3_PUBLIC_BASE_URL` puo' indicare il dominio CDN pubblico. Questa compatibilita' non trasforma il ciclo di vita self-host in un sistema di backup DigitalOcean.

## Configurazione

### Ambiente e API

Gli script npm impostano `DEPLOY_ENV` su `development`, `staging` o `production`. Gli host API e frontend vengono selezionati da `UI/oems.json` in base all'ambiente:

```json
{
  "hosts": {
    "prod": {
      "frontend": "https://app.example.org",
      "api": "/api"
    },
    "staging": {
      "frontend": "https://staging.example.org",
      "api": "/api"
    }
  }
}
```

`UI/endpoints.js` definisce i percorsi consumati dall'applicazione e normalmente non deve essere modificato per cambiare host.

La build accetta inoltre queste variabili opzionali:

| Variabile         | Scopo                                          | Valore predefinito |
| ----------------- | ---------------------------------------------- | ------------------ |
| `OEM_ENV`         | Seleziona una configurazione in `oems.json`    | `assozeta`         |
| `CLIENT_ID`       | Client ID per l'integrazione OAuth configurata | vuoto              |
| `APPLE_CLIENT_ID` | Client ID per Sign in with Apple               | vuoto              |

Esempio:

```bash
OEM_ENV=assozeta CLIENT_ID=your-client-id npm run build:vite:production
```

### Personalizzazione OEM

Per aggiungere un brand:

1. Aggiungi una voce in `UI/oems.json`.
2. Crea gli asset in `UI/public/oem/<nome-brand>/`.
3. Imposta `OEM_ENV=<nome-brand>` quando avvii o compili la UI.

La configurazione OEM controlla logo, metadati, host, colore principale, integrazioni e visibilità di alcune funzioni dell'interfaccia. La struttura minima degli asset è:

```text
UI/public/oem/<nome-brand>/
├── brand/
│   ├── logo.svg
│   └── logo_dark_mode.svg
└── manifest.json
```

## Build e deploy

Genera una build di produzione con:

```bash
cd UI
npm run build:vite:production
```

I file compilati vengono salvati in `UI/dist/public/`. Per verificarli localmente:

```bash
npm run start:vite
```

In produzione configura il web server o reverse proxy per:

- servire `UI/dist/public/` come applicazione SPA, con fallback a `index.html`;
- inoltrare `/api` al backend REST rimuovendo il prefisso `/api`;
- inoltrare `/ws` al backend WebSocket;
- gestire HTTPS e gli header di sicurezza appropriati.

La sola pubblicazione dei file statici non è sufficiente per un'istanza funzionante. Oltre al backend, le funzioni asincrone richiedono worker e scheduler Celery; documenti, comunicazioni e pagamenti richiedono i relativi servizi esterni.

### Release self-host e visibilità dei pacchetti GHCR

Alla pubblicazione di una release, `.github/workflows/publish-images.yml` pubblica le immagini `linux/amd64` e `linux/arm64` su GHCR e allega l'archivio self-host alla release.

I pacchetti container di GHCR sono privati per impostazione predefinita, ma l'installer self-host (`assozeta install` / `assozeta upgrade`) esegue il pull delle immagini senza credenziali. Prima della prima release è quindi necessario un intervento una tantum, da parte del proprietario del pacchetto o di un amministratore, per impostare su **Public** la visibilità di tutti e tre i pacchetti:

- `ghcr.io/<repository-owner>/assozeta-backend`
- `ghcr.io/<repository-owner>/assozeta-web`
- `ghcr.io/<repository-owner>/assozeta-renderer`

(`Package settings > Change visibility > Public`.)

La visibilità non può essere impostata automaticamente dalla pipeline tramite `GITHUB_TOKEN`: la CI la verifica soltanto. Finché i tre pacchetti non sono pubblici, la verifica anonima eseguita prima della pubblicazione della release fallisce e il bundle self-host non viene allegato; il messaggio di errore riporta i tre pacchetti e chiede di rilanciare la pipeline dopo aver impostato la visibilità.

## Qualità del codice

Il repository include un controllo diagnostico per confrontare le classi usate nei sorgenti con gli stili distribuiti:

```bash
cd UI
npm run css:verify
```

Il comando può segnalare anche classi dinamiche o riferimenti legacy e va quindi usato come supporto alla revisione, non come test automatico.

Il backend include test Django mirati sotto `BE/*/tests/`. I test girano nel container Docker di sviluppo con PostgreSQL reale. Per eseguirli dal repository root:

```bash
./run_tests.sh                                   # seriale, con coverage
./run_tests.sh --parallel                        # parallelo via pytest-xdist
./run_tests.sh -k test_login
./run_tests.sh --no-coverage -v                  # seriale senza coverage
./run_tests.sh --open                            # apre il report HTML di coverage
./run_tests.sh application/tests/test_auth_login.py  # test specifico
make dev-test                                    # esegue i test backend e poi css:verify
```

I test richiedono che l'immagine di sviluppo includa le dipendenze installate. Dopo il primo `make dev` o `make dev-rebuild` le dipendenze sono disponibili. Non serve un database di test pre-creato o cleanup manuale: Django crea e distrugge automaticamente il database di test su PostgreSQL.

## Contribuire

Issue, correzioni e nuove funzionalità sono benvenute.

1. Apri una [issue](https://github.com/carbogninalberto/assozeta/issues) per descrivere bug o modifiche rilevanti.
2. Crea un fork e un branch dedicato, ad esempio `feature/nome-funzionalita`.
3. Mantieni lo stile del codice esistente e limita la modifica allo scopo della PR.
4. Esegui i controlli disponibili e aggiorna la documentazione interessata.
5. Apri una pull request spiegando comportamento, motivazione e modalità di verifica.

Non includere credenziali, chiavi API, dati personali o configurazioni di produzione nei commit.

## Supporto e community

- [Unisciti alla community su Telegram](https://t.me/+Cxbg24Az1S0wMjI0) per confronto e supporto allo sviluppo
- [GitHub Issues](https://github.com/carbogninalberto/assozeta/issues) per bug e richieste tracciabili
- [Manuale Bakney](https://manuale.bakney.com) come riferimento funzionale
- [support@bakney.com](mailto:support@bakney.com) per contatti diretti

## Licenza

Assozeta è distribuito sotto licenza [GNU Affero General Public License v3.0](LICENSE) (`AGPL-3.0-only`). Se modifichi il software e lo rendi disponibile agli utenti attraverso una rete, la licenza richiede che anche il codice sorgente della versione modificata sia messo a loro disposizione.

Le dipendenze e gli asset di terze parti mantengono le rispettive licenze; consulta [`UI/THIRD-PARTY-LICENSES.md`](UI/THIRD-PARTY-LICENSES.md).

---

<div align="center">
  Sviluppato dalla community a partire dall'esperienza Bakney.
</div>
