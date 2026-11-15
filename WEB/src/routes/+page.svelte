<script lang="ts">
	import type { PageData } from './$types';
	import CtaSection from '$lib/components/CtaSection.svelte';
	import Footer from '$lib/components/Footer.svelte';
	import Hero from '$lib/components/Hero.svelte';
	import Nav from '$lib/components/Nav.svelte';
	import Seo from '$lib/components/Seo.svelte';
	import Chapter from '$lib/components/home/Chapter.svelte';
	import AttendanceMock from '$lib/components/home/AttendanceMock.svelte';
	import ControlMock from '$lib/components/home/ControlMock.svelte';
	import MembersMock from '$lib/components/home/MembersMock.svelte';
	import PaymentsMock from '$lib/components/home/PaymentsMock.svelte';
	import SignupFormMock from '$lib/components/home/SignupFormMock.svelte';
	import Deploy from '$lib/components/Deploy.svelte';
	import Pricing from '$lib/components/Pricing.svelte';
	import Faq from '$lib/components/home/Faq.svelte';
	import FeatureGrid from '$lib/components/home/FeatureGrid.svelte';
	import FromBakney from '$lib/components/home/FromBakney.svelte';
	import SportsStrip from '$lib/components/home/SportsStrip.svelte';
	import UseCases from '$lib/components/home/UseCases.svelte';
	import Users from '@lucide/svelte/icons/users';
	import ClipboardList from '@lucide/svelte/icons/clipboard-list';
	import CalendarCheck from '@lucide/svelte/icons/calendar-check';
	import Receipt from '@lucide/svelte/icons/receipt';
	import ShieldCheck from '@lucide/svelte/icons/shield-check';
	import { HOME_FAQ } from '$lib/data/faq';
	import { SITE, url } from '$lib/config/site';
	import { ORGANIZATION_LD, SOFTWARE_APP_LD, WEBSITE_LD, faqPageLd, webPageLd } from '$lib/data/routes';

	let { data }: { data: PageData } = $props();

	const title = 'Assozeta: gestionale open source per ASD e SSD, gratuito e self-hosted';
	const description = SITE.description;
	const canonical = url('/');

	const jsonLd = [ORGANIZATION_LD, WEBSITE_LD, SOFTWARE_APP_LD, webPageLd(title, description, canonical), faqPageLd(HOME_FAQ)];
</script>

<Seo meta={{ title, description, canonical, jsonLd }} />

<Nav />

<main id="main" tabindex="-1" class="outline-none">
	<Hero stars={data.stars} release={data.release} />

	<SportsStrip />

	<Chapter
		id="soci"
		number="01"
		eyebrow="Soci"
		title="Tutti i soci in un unico archivio"
		body="Anagrafiche di soci, atleti, minori e tutori, con tesseramenti, tessere digitali e libro soci. Importa l’elenco esistente da un file CSV o Excel e ritrova chiunque con ricerca, filtri e tag."
		points={['Minori collegati ai loro tutori', 'Importazione da CSV e XLSX', 'Tag, archivio e storico delle attività']}
		link={{ label: 'Soci e tesserati', href: '/funzionalita/soci-e-tesserati' }}
		icon={Users}
	>
		{#snippet visual()}<MembersMock />{/snippet}
	</Chapter>

	<Chapter
		id="iscrizioni"
		number="02"
		eyebrow="Iscrizioni"
		title="Iscrizioni online, anche per tutta la famiglia"
		body="Pubblica un modulo d’iscrizione con i campi che ti servono, allegati e firma. I genitori iscrivono più figli in una volta sola e tu approvi le richieste dalla bacheca."
		points={['Form builder con campi, allegati e firme', 'Iscrizioni individuali, multiple e familiari', 'Link da condividere su sito e social']}
		link={{ label: 'Iscrizioni e moduli online', href: '/funzionalita/iscrizioni-online' }}
		icon={ClipboardList}
		tinted
		reverse
	>
		{#snippet visual()}<SignupFormMock />{/snippet}
	</Chapter>

	<Chapter
		id="corsi"
		number="03"
		eyebrow="Corsi e presenze"
		title="Corsi, calendario e presenze senza fogli Excel"
		body="Organizza corsi, lezioni, istruttori e sedi, poi segna le presenze dal telefono o con il check-in tramite QR. Carnet e pacchetti di ingressi si scalano da soli."
		points={[
			'Calendari pubblici e sincronizzazione con Google Calendar',
			'Carnet e pacchetti di ingressi',
			'Camp e ritiri sportivi'
		]}
		link={{ label: 'Corsi e presenze', href: '/funzionalita/corsi-e-presenze' }}
		icon={CalendarCheck}
	>
		{#snippet visual()}<AttendanceMock />{/snippet}
	</Chapter>

	<Chapter
		id="pagamenti"
		number="04"
		eyebrow="Pagamenti"
		title="Quote, rate e ricevute sempre in ordine"
		body="Definisci quote e rate, controlla chi è in regola e chi è in ritardo, emetti le ricevute in PDF. Con Stripe Connect i soci possono pagare online nei flussi supportati."
		points={[
			'Scadenze e rate per ogni corso',
			'Ricevute in PDF per soci, clienti e fornitori',
			'Entrate, uscite, conti e riepiloghi di bilancio'
		]}
		link={{ label: 'Pagamenti e ricevute', href: '/funzionalita/pagamenti-e-ricevute' }}
		icon={Receipt}
		tinted
		reverse
	>
		{#snippet visual()}<PaymentsMock />{/snippet}
	</Chapter>

	<Chapter
		id="controllo"
		number="05"
		eyebrow="Controllo"
		title="I dati restano sul tuo server"
		body="Assozeta gira sulla tua infrastruttura: database, documenti e firme non lasciano il tuo server. Il proprietario aggiorna l’istanza, controlla i backup e la diagnostica da una pagina dedicata."
		points={[
			'Autenticazione a due fattori e audit log',
			'Aggiornamenti con backup automatico prima di ogni modifica',
			'Export completo dei dati dell’associazione'
		]}
		link={{ label: 'Sicurezza', href: '/sicurezza' }}
		icon={ShieldCheck}
	>
		{#snippet visual()}<ControlMock version={data.release.version} />{/snippet}
	</Chapter>

	<Deploy version={data.release.version} />

	<UseCases />

	<Pricing />

	<FeatureGrid />

	<FromBakney />

	<Faq />

	<div class="mx-auto max-w-4xl px-6 pt-4 pb-24">
		<CtaSection />
	</div>
</main>

<Footer />
