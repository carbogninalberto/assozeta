/**
 * Navigation menus (navbar dropdowns, mobile sheet and footer columns) built from the route
 * registry, so new pages appear in navigation without a second list to maintain.
 */
import { LINKS } from '$lib/config/site';
import { HUBS } from '$lib/data/routes';

export interface NavLink {
	label: string;
	href: string;
	description?: string;
	external?: boolean;
}

export interface NavGroup {
	label: string;
	/** Overview page for the group. */
	href: string;
	links: NavLink[];
	footer?: NavLink;
}

const featureLinks: NavLink[] = HUBS.funzionalita.children.map((child) => ({
	label: child.label,
	href: child.href,
	description: child.description
}));

const selfHostingLinks: NavLink[] = HUBS.selfHosting.children.map((child) => ({
	label: child.label,
	href: child.href,
	description: child.description
}));

export const NAV_GROUPS: NavGroup[] = [
	{
		label: 'Funzionalità',
		href: '/funzionalita',
		links: featureLinks,
		footer: { label: 'Tutte le funzionalità', href: '/funzionalita' }
	},
	{
		label: 'Self-hosting',
		href: '/self-hosting',
		links: [
			{
				label: 'Guida al self-hosting',
				href: '/self-hosting',
				description: 'Installa Assozeta su un tuo server Linux con Docker Compose.'
			},
			...selfHostingLinks
		],
		footer: { label: 'Codice e README su GitHub', href: LINKS.github, external: true }
	},
	{
		label: 'Progetto',
		href: '/open-source',
		links: [
			{
				label: 'Open source',
				href: '/open-source',
				description: 'Un solo repository pubblico, nessuna edizione a pagamento.'
			},
			{
				label: 'Licenza AGPL-3.0',
				href: '/licenza',
				description: 'Cosa permette la licenza e cosa richiede se modifichi il codice.'
			},
			{
				label: 'Sicurezza',
				href: '/sicurezza',
				description: 'Autenticazione, aggiornamenti e responsabilità di chi installa.'
			},
			{
				label: 'Chi siamo',
				href: '/chi-siamo',
				description: 'Il progetto, il legame con Bakney e chi lo mantiene.'
			},
			{
				label: 'Community su Telegram',
				href: LINKS.telegram,
				description: 'Domande, esperienze e segnalazioni con chi usa Assozeta.',
				external: true
			}
		],
		footer: { label: 'Segnala un problema', href: LINKS.issues, external: true }
	}
];

export const FOOTER_COLUMNS: { title: string; links: NavLink[] }[] = [
	{
		title: 'Prodotto',
		links: [
			{ label: 'Funzionalità', href: '/funzionalita' },
			...featureLinks.map(({ label, href }) => ({ label, href }))
		]
	},
	{
		title: 'Self-hosting',
		links: [
			{ label: 'Panoramica', href: '/self-hosting' },
			...selfHostingLinks.map(({ label, href }) => ({ label, href }))
		]
	},
	{
		title: 'Progetto',
		links: [
			{ label: 'Open source', href: '/open-source' },
			{ label: 'Licenza', href: '/licenza' },
			{ label: 'Sicurezza', href: '/sicurezza' },
			{ label: 'Chi siamo', href: '/chi-siamo' },
			{ label: 'Privacy', href: '/privacy' },
			{ label: 'GitHub', href: LINKS.github, external: true },
			{ label: 'Telegram', href: LINKS.telegram, external: true }
		]
	}
];
