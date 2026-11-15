/**
 * Route and content registry for the Assozeta marketing site.
 * All routes are statically prerenderable.
 *
 * This file serves as the single source of truth for:
 *  - which routes exist (used for sitemap generation and SEO validation)
 *  - content types for shared dynamic-route pages (features, self-hosting)
 *  - breadcrumb data
 *  - hub/silo relationships
 *  - structured data for the homepage
 */

import { LINKS, url, type PageMeta } from '$lib/config/site';

// Re-export url for convenience
export { url };

// ---- Types ----

export interface ContentPage {
	slug: string;
	title: string;
	description: string;
	heading: string;
	intro: string;
	sections: ContentSection[];
	/** Optional plain-text footnote rendered in a box below the sections. */
	footnotes?: string;
}

export interface ContentSection {
	heading: string;
	body: string;
	list?: string[];
	links?: { label: string; href: string }[];
	/** Optional shell/terminal snippet rendered as a code block. */
	code?: string;
}

export interface ContentRegistry {
	/** Route path within parent (e.g. 'soci-e-tesserati') */
	[key: string]: ContentPage;
}

// ---- Breadcrumb utilities ----

export interface Crumb {
	label: string;
	href: string;
}

/**
 * Build a breadcrumb trail for any page.
 * `parentSlug` is the hub root (e.g. 'funzionalita' or 'self-hosting').
 * `parentLabel` is the display label for that hub.
 */
export function breadcrumbsFrom(
	parentSlug: string,
	parentLabel: string,
	pageTitle?: string,
	pageSlug?: string
): Crumb[] {
	const crumbs: Crumb[] = [{ label: 'Home', href: '/' }];
	if (parentSlug) {
		crumbs.push({ label: parentLabel, href: `/${parentSlug}` });
	}
	if (pageTitle && pageSlug) {
		crumbs.push({ label: pageTitle, href: `/${parentSlug}/${pageSlug}` });
	}
	return crumbs;
}

// ---- Hub definitions ----

export interface Hub {
	slug: string;
	label: string;
	href: string;
	children: { slug: string; label: string; href: string; description?: string }[];
}

export const HUBS = {
	funzionalita: {
		slug: 'funzionalita',
		label: 'Funzionalità',
		href: '/funzionalita',
		children: [
			{
				slug: 'soci-e-tesserati',
				label: 'Soci e tesserati',
				href: '/funzionalita/soci-e-tesserati',
				description:
					'Anagrafiche di soci, atleti, minori e tutori, tesseramenti, tessere digitali, documenti e libro soci.'
			},
			{
				slug: 'iscrizioni-online',
				label: 'Iscrizioni e moduli online',
				href: '/funzionalita/iscrizioni-online',
				description:
					'Moduli pubblici personalizzabili, iscrizioni individuali o familiari e raccolta di documenti e firme.'
			},
			{
				slug: 'corsi-e-presenze',
				label: 'Corsi e presenze',
				href: '/funzionalita/corsi-e-presenze',
				description: 'Corsi, calendari, lezioni e rilevazione presenze, con carnet, camp e check-in tramite QR.'
			},
			{
				slug: 'certificati-e-documenti',
				label: 'Certificati e documenti',
				href: '/funzionalita/certificati-e-documenti',
				description: 'Certificati medici, documenti e firme dei soci, con archivio e storico delle attività.'
			},
			{
				slug: 'pagamenti-e-ricevute',
				label: 'Pagamenti e ricevute',
				href: '/funzionalita/pagamenti-e-ricevute',
				description: 'Quote, scadenze, pagamenti e ricevute, con pagamenti online Stripe per i flussi supportati.'
			},
			{
				slug: 'contabilita',
				label: 'Contabilità e bilancio',
				href: '/funzionalita/contabilita',
				description: 'Entrate, uscite, conti, trasferimenti e riepiloghi di bilancio per l’anno sportivo.'
			},
			{
				slug: 'comunicazioni-e-automazioni',
				label: 'Comunicazioni e automazioni',
				href: '/funzionalita/comunicazioni-e-automazioni',
				description: 'Email tramite SMTP, automazioni su eventi e pianificazione, notifiche in tempo reale.'
			},
			{
				slug: 'bacheca-e-report',
				label: 'Bacheca e report',
				href: '/funzionalita/bacheca-e-report',
				description: 'Bacheca con widget, report, grafici ed esportazioni CSV e XLSX sui dati dell’associazione.'
			}
		]
	},
	selfHosting: {
		slug: 'self-hosting',
		label: 'Self-hosting',
		href: '/self-hosting',
		children: [
			{
				slug: 'requisiti',
				label: 'Requisiti',
				href: '/self-hosting/requisiti',
				description: 'Server Linux con Docker e Docker Compose 2.20 o superiore, DNS pubblico e almeno 4 GB di RAM.'
			},
			{
				slug: 'installazione',
				label: 'Installazione',
				href: '/self-hosting/installazione',
				description: 'Installazione da bundle di release o checkout con tag, poi procedura guidata al primo avvio.'
			},
			{
				slug: 'configurazione',
				label: 'Configurazione e integrazioni',
				href: '/self-hosting/configurazione',
				description: 'Variabili in selfhost/.env e servizi opzionali come SMTP, Stripe e Google Calendar.'
			},
			{
				slug: 'aggiornamenti',
				label: 'Aggiornamenti',
				href: '/self-hosting/aggiornamenti',
				description: 'Aggiornamento a una release stabile dalla pagina Istanza o da riga di comando, con backup.'
			},
			{
				slug: 'backup-e-ripristino',
				label: 'Backup e ripristino',
				href: '/self-hosting/backup-e-ripristino',
				description: 'Backup di database, archivio e configurazione, con rotazione degli archivi e ripristino documentato.'
			},
			{
				slug: 'manutenzione',
				label: 'Diagnostica e manutenzione',
				href: '/self-hosting/manutenzione',
				description: 'Stato, log e diagnostica dell’installazione dalla pagina Istanza o dai comandi CLI.'
			}
		]
	}
} satisfies Record<string, Hub>;

// ---- Standalone page definitions ----

export interface StandalonePage {
	path: string;
	label: string;
	description: string;
}

export const STANDALONE_PAGES: StandalonePage[] = [
	{
		path: '/open-source',
		label: 'Open source',
		description:
			'Come Assozeta viene sviluppato e distribuito: un solo repository pubblico, nessuna edizione a pagamento.'
	},
	{
		path: '/licenza',
		label: 'Licenza AGPL-3.0',
		description:
			'Cosa permette la GNU AGPL v3.0, cosa richiede se modifichi il codice e come restano le licenze di terze parti.'
	},
	{
		path: '/sicurezza',
		label: 'Sicurezza',
		description: 'Autenticazione a due fattori, audit log, aggiornamenti e responsabilità di chi installa Assozeta.'
	},
	{
		path: '/chi-siamo',
		label: 'Chi siamo',
		description: 'Perché Assozeta esiste, il legame con Bakney e chi mantiene il progetto.'
	},
	{
		path: '/privacy',
		label: 'Privacy',
		description: 'Quali dati tratta questo sito, l’unica preferenza tecnica salvata e come scrivere al titolare.'
	}
];

// ---- All indexable routes (for sitemap + validation) ----

export function allRoutes(): { path: string; priority: number; changefreq: string }[] {
	const routes = new Map<string, { path: string; priority: number; changefreq: string }>();
	const add = (path: string, priority: number, changefreq: string) => {
		routes.set(path, { path, priority, changefreq });
	};

	add('/', 1.0, 'weekly');

	for (const hub of Object.values(HUBS)) {
		add(hub.href, 0.9, 'monthly');
		for (const child of hub.children) {
			add(child.href, 0.7, 'monthly');
		}
	}

	for (const page of STANDALONE_PAGES) {
		add(page.path, page.path === '/privacy' ? 0.3 : 0.6, 'monthly');
	}

	return [...routes.values()];
}

// ---- Static metadata for the homepage JSON-LD ----

export const ORGANIZATION_LD = {
	'@context': 'https://schema.org',
	'@type': 'Organization',
	name: 'Bakney srl',
	url: LINKS.bakney,
	email: LINKS.email
};

export const WEBSITE_LD = {
	'@context': 'https://schema.org',
	'@type': 'WebSite',
	name: 'Assozeta',
	url: url('/'),
	inLanguage: 'it-IT'
};

export const SOFTWARE_APP_LD = {
	'@context': 'https://schema.org',
	'@type': 'SoftwareApplication',
	name: 'Assozeta',
	url: LINKS.github,
	description:
		'Gestionale open source per associazioni e società sportive dilettantistiche: soci, iscrizioni online, corsi, presenze, pagamenti e contabilità.',
	applicationCategory: 'BusinessApplication',
	operatingSystem: 'Linux (Docker)',
	inLanguage: 'it-IT',
	offers: {
		'@type': 'Offer',
		price: '0',
		priceCurrency: 'EUR'
	},
	license: 'https://www.gnu.org/licenses/agpl-3.0.html',
	publisher: {
		'@type': 'Organization',
		name: 'Bakney srl',
		url: LINKS.bakney
	}
};

export function webPageLd(
	name: string,
	description: string,
	pageUrl: string,
	breadcrumbs?: Crumb[],
	modifiedAt?: string
) {
	const ld: Record<string, unknown> = {
		'@context': 'https://schema.org',
		'@type': 'WebPage',
		name,
		description,
		url: pageUrl,
		inLanguage: 'it-IT'
	};
	if (modifiedAt) ld.dateModified = modifiedAt;
	if (breadcrumbs && breadcrumbs.length > 0) {
		ld.breadcrumb = {
			'@type': 'BreadcrumbList',
			itemListElement: breadcrumbs.map((crumb, i) => ({
				'@type': 'ListItem',
				position: i + 1,
				name: crumb.label,
				item: crumb.href.startsWith('http') ? crumb.href : url(crumb.href)
			}))
		};
	}
	return ld;
}

export function faqPageLd(items: { question: string; answer: string }[]) {
	return {
		'@context': 'https://schema.org',
		'@type': 'FAQPage',
		mainEntity: items.map((item) => ({
			'@type': 'Question',
			name: item.question,
			acceptedAnswer: { '@type': 'Answer', text: item.answer }
		}))
	};
}

// ---- Metadata helpers for standalone pages ----

export function metaForStandalone(path: string): PageMeta | undefined {
	const page = STANDALONE_PAGES.find((p) => p.path === path);
	if (!page) return undefined;
	return {
		title: page.label,
		description: page.description,
		canonical: url(page.path)
	};
}
