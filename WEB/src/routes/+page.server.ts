import { FALLBACK_RELEASE as FALLBACK_SITE_RELEASE } from '$lib/config/site';

export const prerender = true;

/**
 * Real GitHub data, fetched once at build time. Any failure (offline build, rate limit,
 * unexpected payload) falls back to no star count and to the last known release, so the
 * homepage always renders.
 */

export interface ReleaseInfo {
	version: string;
	url: string;
	publishedAt: string | null;
}

export interface HomeData {
	stars: number | null;
	release: ReleaseInfo;
}

const REPO_URL = 'https://api.github.com/repos/carbogninalberto/assozeta';
const RELEASE_URL = `${REPO_URL}/releases/latest`;
const FALLBACK_RELEASE: ReleaseInfo = { ...FALLBACK_SITE_RELEASE };
const TAG_PATTERN = /^v?\d+\.\d+\.\d+$/;

type Fetch = typeof fetch;

async function getJson(fetchFn: Fetch, url: string): Promise<unknown> {
	const response = await fetchFn(url, {
		headers: { Accept: 'application/vnd.github+json', 'User-Agent': 'assozeta-web-build' },
		signal: AbortSignal.timeout(5000)
	});
	if (!response.ok) return null;
	return (await response.json()) as unknown;
}

async function fetchStars(fetchFn: Fetch): Promise<number | null> {
	try {
		const body = await getJson(fetchFn, REPO_URL);
		if (!body || typeof body !== 'object') return null;
		const stars = (body as { stargazers_count?: unknown }).stargazers_count;
		return typeof stars === 'number' && Number.isFinite(stars) && stars >= 0 ? stars : null;
	} catch {
		return null;
	}
}

async function fetchRelease(fetchFn: Fetch): Promise<ReleaseInfo> {
	try {
		const body = await getJson(fetchFn, RELEASE_URL);
		if (!body || typeof body !== 'object') return FALLBACK_RELEASE;

		const { tag_name, html_url, published_at } = body as {
			tag_name?: unknown;
			html_url?: unknown;
			published_at?: unknown;
		};

		const tag = typeof tag_name === 'string' ? tag_name.trim() : '';
		if (!TAG_PATTERN.test(tag)) return FALLBACK_RELEASE;
		const version = tag.startsWith('v') ? tag : `v${tag}`;

		const releaseUrl =
			typeof html_url === 'string' && /^https:\/\/github\.com\/[\w./-]+$/.test(html_url)
				? html_url
				: `https://github.com/carbogninalberto/assozeta/releases/tag/${version}`;

		const publishedAt =
			typeof published_at === 'string' && !Number.isNaN(Date.parse(published_at))
				? published_at.slice(0, 10)
				: null;

		return { version, url: releaseUrl, publishedAt };
	} catch {
		return FALLBACK_RELEASE;
	}
}

export async function load({ fetch }: { fetch: Fetch }): Promise<HomeData> {
	const [stars, release] = await Promise.all([fetchStars(fetch), fetchRelease(fetch)]);
	return { stars, release };
}
