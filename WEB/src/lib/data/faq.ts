/**
 * Homepage FAQ, plain text, strictly from section 5 of the brief.
 * Rendered by `$lib/components/home/Faq.svelte` and reused for the FAQPage JSON-LD.
 */

export const HOME_FAQ: { question: string; answer: string }[] = [
	{
		question: 'Assozeta è davvero gratuito?',
		answer:
			'Sì. Assozeta è distribuito sotto licenza GNU AGPL v3.0: non c’è nessun costo di licenza, nessun costo per socio o per utente e nessuna versione a pagamento con funzioni riservate. Paghi solo il server su cui lo installi e gli eventuali servizi esterni che scegli di collegare, come il server SMTP per le email o le commissioni di Stripe sui pagamenti online.'
	},
	{
		question: 'Che cosa serve per installarlo?',
		answer:
			'Un server Linux con Docker Engine e Docker Compose 2.20.0 o superiore, almeno 4 GB di RAM (8 GB sono consigliati quando generi molti PDF) e un record DNS pubblico che punti al server. Le porte 80 e 443 devono essere raggiungibili per ottenere HTTPS in automatico, e sul server servono openssl, curl, tar e flock.'
	},
	{
		question: 'Posso gestire più associazioni con una sola installazione?',
		answer:
			'No: ogni installazione gestisce una sola associazione. La procedura guidata del primo avvio crea l’account proprietario e l’associazione e può essere completata una volta sola. Per una seconda associazione serve quindi una seconda installazione.'
	},
	{
		question: 'Posso importare i soci che ho già?',
		answer:
			'Sì. Gli associati si importano da un file CSV o XLSX, e l’applicazione offre anche un export e un import completi nel proprio formato, con tutti i dati dell’associazione. Gli export contengono dati sensibili, compresi gli hash delle password degli account, e vanno protetti.'
	},
	{
		question: 'Dove sono salvati i dati?',
		answer:
			'Sul tuo server: i dati stanno in PostgreSQL e gli allegati in uno storage MinIO privato, che non è esposto a internet. L’unico servizio che pubblica porte è Caddy, sulle 80 e 443. I backup si creano con un comando o dalla pagina Istanza e vanno copiati fuori dal server.'
	},
	{
		question: 'Come si aggiorna Assozeta?',
		answer:
			'Il proprietario dell’istanza sceglie una release stabile dalla pagina Istanza e conferma l’aggiornamento, oppure usa il comando ./selfhost/bin/assozeta upgrade con la versione desiderata. Prima di ogni aggiornamento viene creato un backup automatico, che conviene conservare.'
	},
	{
		question: 'Funziona da smartphone?',
		answer:
			'Sì. Assozeta è un’interfaccia web in italiano che si usa dal browser di un PC, di un tablet o di uno smartphone, senza installare nulla. Le stesse pagine si adattano allo schermo del dispositivo.'
	},
	{
		question: 'Vengo da Bakney Sport: posso passare ad Assozeta?',
		answer:
			'Sì. Assozeta nasce dall’esperienza di Bakney Sport, il cui servizio in cloud è stato annunciato in chiusura dal 30 settembre 2026. Un export di Bakney Sport nel formato bakney_sport_export_v1 si importa in Assozeta dalla pagina Istanza (Profilo → Istanza → Backup → Trasferisci dati). Prima di migrare verifica la compatibilità dell’export: l’importazione è un’operazione diversa dal ripristino di un backup.'
	}
];
