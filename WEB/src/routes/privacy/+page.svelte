<script lang="ts">
	import Breadcrumbs from '$lib/components/Breadcrumbs.svelte';
	import CtaSection from '$lib/components/CtaSection.svelte';
	import Footer from '$lib/components/Footer.svelte';
	import Nav from '$lib/components/Nav.svelte';
	import Seo from '$lib/components/Seo.svelte';
	import { LINKS, url } from '$lib/config/site';
	import { breadcrumbsFrom, webPageLd } from '$lib/data/routes';

	const title = 'Privacy: quali dati tratta questo sito · Assozeta';
	const description =
		'Informativa privacy del sito assozeta.it: nessun cookie di profilazione, solo la preferenza del tema salvata nel browser, log tecnici del server e diritti GDPR.';
	const updated = '15 novembre 2026';
	const canonical = url('/privacy');
	const crumbs = breadcrumbsFrom('privacy', 'Privacy');
	const jsonLd = webPageLd(title, description, canonical, crumbs);
</script>

<Seo meta={{ title, description, canonical, ogType: 'article', jsonLd }} />

<Nav />

<main id="main" tabindex="-1" class="outline-none mx-auto max-w-3xl px-6 pt-28 pb-20">
	<div class="mb-8">
		<Breadcrumbs breadcrumbs={crumbs} />
	</div>

	<article class="min-w-0 [overflow-wrap:anywhere]">
		<p class="font-mono text-xs tracking-widest text-brand uppercase">Informativa</p>
		<h1 class="mt-2 text-4xl font-bold tracking-tight sm:text-5xl">
			<span class="gradient-text">Privacy</span>
		</h1>
		<p class="mt-3 text-sm text-muted-foreground">Ultimo aggiornamento: {updated}</p>

		<div class="mt-10 space-y-10 text-sm leading-relaxed text-muted-foreground">
			<section>
				<h2 class="text-lg font-semibold tracking-tight text-foreground">1. Titolare del trattamento</h2>
				<p class="mt-2">
					Il titolare del trattamento è <strong class="text-foreground">Bakney srl</strong>, P. IVA 04921790236,
					con sede legale in Q.re Aldo Moro 49, 37032 Monteforte d’Alpone (VR), Italia, N. REA VR-456410,
					Registro delle Imprese di Verona, capitale sociale € 1.000,00 i.v.
				</p>
				<p class="mt-2">
					Per qualsiasi questione relativa ai dati personali puoi scrivere a
					<a href="mailto:{LINKS.email}" class="text-brand hover:text-brand-strong hover:underline">{LINKS.email}</a
					>.
				</p>
			</section>

			<section>
				<h2 class="text-lg font-semibold tracking-tight text-foreground">2. Ambito dell’informativa</h2>
				<p class="mt-2">
					Questa informativa riguarda soltanto questo sito, assozeta.it, che è un sito statico di presentazione.
					Non raccoglie iscrizioni, non offre account e non contiene moduli per inserire dati.
				</p>
				<p class="mt-2">
					Un’installazione di Assozeta è un’altra cosa: l’associazione che la installa e la gestisce è titolare
					del trattamento dei dati dei propri soci, atleti e collaboratori. Questa informativa non copre i dati
					inseriti in un’istanza di Assozeta.
				</p>
			</section>

			<section>
				<h2 class="text-lg font-semibold tracking-tight text-foreground">3. Dati trattati</h2>
				<ul class="mt-2 ml-5 list-disc space-y-1.5">
					<li>
						<strong class="text-foreground">Messaggi che ci invii:</strong> se scrivi a
						{LINKS.email}, trattiamo l’indirizzo mittente e il contenuto del messaggio per rispondere.
					</li>
					<li>
						<strong class="text-foreground">Log tecnici del server web:</strong> l’indirizzo IP, lo user agent,
						l’URL richiesto e la data della richiesta possono essere registrati dal server o dal fornitore di
						hosting per sicurezza, diagnosi e prevenzione degli abusi.
					</li>
					<li>
						<strong class="text-foreground">Preferenza del tema:</strong> il sito salva nel tuo browser la scelta
						tra tema chiaro e tema scuro, senza inviarla a nessuno.
					</li>
				</ul>
			</section>

			<section>
				<h2 class="text-lg font-semibold tracking-tight text-foreground">4. Cookie e memoria locale</h2>
				<p class="mt-2">
					Il sito non usa cookie di profilazione, non installa strumenti di analytics e non ospita pubblicità o
					script di misurazione delle campagne.
				</p>
				<p class="mt-2">
					Nel browser viene salvata in modo persistente una sola voce di <code class="rounded bg-subtle px-1 py-0.5 font-mono text-xs">localStorage</code>
					con chiave <code class="rounded bg-subtle px-1 py-0.5 font-mono text-xs">assozeta-theme</code> e valore
					<code class="rounded bg-subtle px-1 py-0.5 font-mono text-xs">light</code> oppure
					<code class="rounded bg-subtle px-1 py-0.5 font-mono text-xs">dark</code>. Serve a ricordare la tua
					preferenza tra una visita e l’altra: è una preferenza tecnica, non viene inviata a nessuno e puoi
					cancellarla svuotando i dati del sito dalle impostazioni del browser.
				</p>
				<p class="mt-2">
					Durante la navigazione il framework del sito usa anche il
					<code class="rounded bg-subtle px-1 py-0.5 font-mono text-xs">sessionStorage</code> della scheda per
					ricordare la posizione di scorrimento quando torni indietro. Sono dati tecnici, restano nel browser e
					vengono cancellati alla chiusura della scheda.
				</p>
				<p class="mt-2">
					Caratteri, immagini e script sono serviti dal sito stesso: le pagine non caricano risorse da servizi di
					terze parti.
				</p>
			</section>

			<section>
				<h2 class="text-lg font-semibold tracking-tight text-foreground">5. Base giuridica e conservazione</h2>
				<ul class="mt-2 ml-5 list-disc space-y-1.5">
					<li>
						<strong class="text-foreground">Legittimo interesse (art. 6, par. 1, lett. f):</strong> per la
						sicurezza del sito, la prevenzione degli abusi e i log tecnici necessari a erogare il servizio.
					</li>
					<li>
						<strong class="text-foreground">Riscontro a una tua richiesta (art. 6, par. 1, lett. b):</strong> per
						gestire la corrispondenza che ci invii.
					</li>
				</ul>
				<p class="mt-2">
					I log tecnici sono conservati per il tempo necessario alla sicurezza e secondo la configurazione del
					server e del fornitore di hosting. La corrispondenza è conservata per il tempo necessario a gestire la
					richiesta e a rispettare gli obblighi di legge. La preferenza del tema resta nel tuo browser finché non
					la cancelli.
				</p>
			</section>

			<section>
				<h2 class="text-lg font-semibold tracking-tight text-foreground">6. Destinatari e link esterni</h2>
				<p class="mt-2">
					I dati sono trattati da Bakney srl e, per la parte tecnica, dal fornitore che ospita il sito, nei limiti
					necessari a erogare il servizio.
				</p>
				<p class="mt-2">
					Il sito contiene link a servizi esterni, tra cui GitHub, Telegram, il sito della Free Software Foundation
					e bakney.com. Quei servizi applicano le proprie informative privacy, indipendenti da questa: ti
					consigliamo di leggerle prima di usarli.
				</p>
			</section>

			<section>
				<h2 class="text-lg font-semibold tracking-tight text-foreground">7. I tuoi diritti</h2>
				<p class="mt-2">
					Nei casi previsti dal GDPR (artt. 15-22) puoi chiedere l’accesso ai tuoi dati personali, la rettifica,
					la cancellazione, la limitazione del trattamento, la portabilità e puoi opporti al trattamento.
				</p>
				<p class="mt-2">
					Per esercitarli scrivi a
					<a href="mailto:{LINKS.email}" class="text-brand hover:text-brand-strong hover:underline">{LINKS.email}</a
					>. Se ritieni che il trattamento non rispetti la normativa puoi proporre reclamo al
					<a
						href="https://www.garanteprivacy.it"
						target="_blank"
						rel="noopener noreferrer"
						class="text-brand hover:text-brand-strong hover:underline">Garante per la protezione dei dati personali</a
					>.
				</p>
			</section>

			<section>
				<h2 class="text-lg font-semibold tracking-tight text-foreground">8. Modifiche all’informativa</h2>
				<p class="mt-2">
					Questa informativa può essere aggiornata se cambiano il sito, i fornitori o gli obblighi di legge. La
					data di ultimo aggiornamento in alto identifica la versione in vigore.
				</p>
			</section>
		</div>

		<CtaSection />
	</article>
</main>

<Footer />
