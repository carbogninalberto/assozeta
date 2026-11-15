/**
 * Assozeta's feature groups and optional integrations, strictly from section 5 of the brief
 * (verified against README.md and selfhost/README.md). The homepage feature grid and the
 * integrations row read from here; no invented features.
 */

export const ASSOZETA_FEATURE_GROUPS: { group: string; blurb: string; items: { label: string; href?: string }[] }[] = [
	{
		group: 'Soci e tesserati',
		blurb: 'Anagrafiche, tesseramenti e documenti in un solo archivio.',
		items: [
			{ label: 'Anagrafiche di soci, atleti, minori e tutori', href: '/funzionalita/soci-e-tesserati' },
			{ label: 'Iscrizioni, rinnovi e tesseramenti con tessera digitale', href: '/funzionalita/soci-e-tesserati' },
			{ label: 'Importazione degli associati da CSV e XLSX', href: '/funzionalita/soci-e-tesserati' },
			{ label: 'Documenti, certificati medici e firme', href: '/funzionalita/certificati-e-documenti' },
			{ label: 'Libro soci, tag, archivio e storico delle attività' },
			{ label: 'Ricerca e filtri sugli atleti' },
			{ label: 'Calcolo del codice fiscale dalle anagrafiche' }
		]
	},
	{
		group: 'Iscrizioni e moduli online',
		blurb: 'Raccogli iscrizioni e documenti senza passare dalla carta.',
		items: [
			{ label: 'Moduli pubblici personalizzabili', href: '/funzionalita/iscrizioni-online' },
			{ label: 'Form builder con campi, allegati e firme', href: '/funzionalita/iscrizioni-online' },
			{ label: 'Iscrizioni individuali, multiple e familiari', href: '/funzionalita/iscrizioni-online' },
			{ label: 'Raccolta digitale di dati e documenti' },
			{ label: 'Link di iscrizione condivisibile' }
		]
	},
	{
		group: 'Corsi e attività',
		blurb: 'Calendari, presenze e carnet per ogni disciplina.',
		items: [
			{ label: 'Corsi, calendari, lezioni e rilevazione presenze', href: '/funzionalita/corsi-e-presenze' },
			{ label: 'Istruttori, collaboratori e sedi' },
			{ label: 'Carnet e pacchetti di ingressi' },
			{ label: 'Camp e ritiri sportivi' },
			{ label: 'Calendari pubblici con sincronizzazione Google Calendar' },
			{ label: 'Check-in tramite QR eseguito da un operatore autenticato' },
			{ label: 'Pagamenti rateizzati dei corsi', href: '/funzionalita/pagamenti-e-ricevute' }
		]
	},
	{
		group: 'Pagamenti e contabilità',
		blurb: 'Quote, ricevute e bilancio dello stesso anno sportivo.',
		items: [
			{ label: 'Quote, scadenze, pagamenti e ricevute', href: '/funzionalita/pagamenti-e-ricevute' },
			{ label: 'Entrate, uscite, conti e trasferimenti', href: '/funzionalita/contabilita' },
			{ label: 'Riepiloghi di bilancio per anno sportivo', href: '/funzionalita/contabilita' },
			{ label: 'Ricevute e documenti contabili per clienti e fornitori', href: '/funzionalita/contabilita' },
			{ label: 'Anagrafiche di fornitori e clienti', href: '/funzionalita/contabilita' },
			{ label: 'Stripe Connect e pagamenti online per i flussi supportati', href: '/funzionalita/pagamenti-e-ricevute' }
		]
	},
	{
		group: 'Comunicazioni e bacheca',
		blurb: 'Email, automazioni e report sui dati dell’associazione.',
		items: [
			{ label: 'Invio email tramite SMTP', href: '/funzionalita/comunicazioni-e-automazioni' },
			{ label: 'Automazioni su eventi e pianificazione con azioni email', href: '/funzionalita/comunicazioni-e-automazioni' },
			{ label: 'Notifiche in tempo reale tramite WebSocket', href: '/funzionalita/comunicazioni-e-automazioni' },
			{ label: 'Bacheca con widget, report e grafici', href: '/funzionalita/bacheca-e-report' },
			{ label: 'Esportazioni CSV e XLSX', href: '/funzionalita/bacheca-e-report' }
		]
	},
	{
		group: 'Sicurezza e controllo',
		blurb: 'Accessi, tracciamento e dati che restano tuoi.',
		items: [
			{ label: 'Autenticazione a due fattori TOTP', href: '/sicurezza' },
			{ label: 'Audit log delle attività', href: '/sicurezza' },
			{ label: 'Collaboratori invitati con ruoli e permessi', href: '/sicurezza' },
			{ label: 'Export e import completo dei dati dell’associazione' },
			{ label: 'Aggiornamenti e backup dalla pagina Istanza', href: '/self-hosting/aggiornamenti' },
			{ label: 'Backup completi con ripristino documentato', href: '/self-hosting/backup-e-ripristino' }
		]
	}
];

/** Optional external services: each needs its own credentials and is disabled when left empty. */
export const INTEGRATIONS: { name: string; note: string }[] = [
	{ name: 'SMTP email', note: 'Facoltativo: usa il tuo server di posta' },
	{ name: 'Stripe', note: 'Facoltativo: pagamenti online con le tue chiavi' },
	{ name: 'Google Calendar', note: 'Facoltativo: sincronizza i calendari dei corsi' },
	{ name: 'Accesso con Google e Apple', note: 'Facoltativo: accesso con le tue credenziali' },
	{ name: 'Agente AI compatibile OpenAI', note: 'Facoltativo: collega il modello che preferisci' }
];
