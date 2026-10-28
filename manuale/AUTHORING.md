# Bozza del manuale Assozeta

Questa copia conserva le pagine, i titoli e i componenti MDX del manuale originale.
Le immagini SVG contrassegnate come segnaposto sono provvisorie e misurano **1920 × 1080**.
Non rappresentano l’applicazione e non sono evidenze di uno scenario eseguito.

Per ogni sezione: controllare UI, backend e permessi; correggere le affermazioni;
eseguire il flusso con la fixture isolata; sostituire gli SVG con PNG reali;
conservare l’originale Full HD e le coordinate degli eventuali ritagli.
La verifica finale richiede manifest, codice, scenari, rendering, MCP e risposte.

Il file `.manuale-authoring.json` elenca tutte le pagine e le immagini da completare.
Non pubblicare questa bozza e non installarla come corpus verificato.

## Trasferire la stesura

Il ramo locale `add/manuale` contiene le 43 pagine e i binding delle 507 sezioni.
Il bundle `manuale-draft.bundle` permette di clonarlo senza accesso GitHub:

```sh
git clone /percorso/manuale-draft.bundle ../manuale-revisionato
git -C ../manuale-revisionato switch add/manuale
```

Dal checkout Assozeta con le modifiche del manuale, eseguire:

```sh
python3 docs/scripts/manuale.py run --manual-repo ../manuale-revisionato --allow-dirty --standalone-checkouts
```

Il runner controlla i binding prima di avviare i servizi e usa gli scenari
registrati per acquisire le immagini. Servono Docker e il browser configurato.
Solo i flussi completati con prove reali diventano guide verificate; le altre
procedure e le integrazioni esterne restano lacune esplicite. Il commit della
stesura e il bundle non attestano esecuzione, screenshot o pubblicazione.
