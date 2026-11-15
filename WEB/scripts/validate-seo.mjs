#!/usr/bin/env node
/**
 * SEO validation for the prerendered Assozeta marketing site.
 *
 * The indexable route list comes from `build/sitemap.xml` (generated from
 * `src/lib/data/routes.ts`); `/404` is checked too, but it must never appear
 * in the sitemap. Run after `npm run build`:
 *
 *   node scripts/validate-seo.mjs [buildDir]
 */

import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const BUILD_DIR = resolve(process.argv[2] || join(ROOT, 'build'));
const ORIGIN = 'https://assozeta.it';
const NOT_FOUND_ROUTE = '/404';
const SOCIAL_CARD = join(BUILD_DIR, 'social-card.png');

let errors = 0;
let warnings = 0;
const titles = new Map();
const descriptions = new Map();

function fail(message) {
	console.error(`ERROR: ${message}`);
	errors += 1;
}

function warn(message) {
	console.warn(`WARN: ${message}`);
	warnings += 1;
}

function attribute(tag, name) {
	return tag?.match(new RegExp(`\\b${name}=["']([^"']*)["']`, 'i'))?.[1];
}

function count(html, pattern) {
	return [...html.matchAll(pattern)].length;
}

function escapeRegExp(value) {
	return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** `build/foo.html`, `build/foo/index.html` or `build/index.html` for `/`. */
function routeFile(route) {
	if (route === '/') return existsSync(join(BUILD_DIR, 'index.html')) ? join(BUILD_DIR, 'index.html') : undefined;
	const clean = route.replace(/^\/+|\/+$/g, '');
	const candidates = [join(BUILD_DIR, `${clean}.html`), join(BUILD_DIR, clean, 'index.html')];
	return candidates.find((candidate) => existsSync(candidate) && statSync(candidate).isFile());
}

function htmlFor(route) {
	const file = routeFile(route);
	if (!file) {
		fail(`${route}: build output is missing`);
		return '';
	}
	return readFileSync(file, 'utf8');
}

function plainText(html) {
	return html
		.replace(/<script\b[\s\S]*?<\/script>/gi, ' ')
		.replace(/<style\b[\s\S]*?<\/style>/gi, ' ')
		.replace(/<[^>]+>/g, ' ')
		.replace(/&amp;/g, '&')
		.replace(/&quot;/g, '"')
		.replace(/&#39;|&apos;/g, "'")
		.replace(/&lt;/g, '<')
		.replace(/&gt;/g, '>')
		.replace(/&nbsp;/g, ' ')
		.replace(/\s+/g, ' ')
		.trim();
}

function jsonLdItems(html, route) {
	const items = [];
	for (const block of html.matchAll(/<script\s+type=["']application\/ld\+json["']>([\s\S]*?)<\/script>/gi)) {
		try {
			const parsed = JSON.parse(block[1]);
			items.push(...(Array.isArray(parsed) ? parsed : [parsed]));
		} catch (error) {
			fail(`${route}: invalid JSON-LD (${error.message})`);
		}
	}
	return items;
}

/** Resolve a site path to an existing file in the build output. */
function resolveBuildPath(pathname) {
	const decoded = decodeURIComponent(pathname);
	const candidates = [
		join(BUILD_DIR, decoded),
		join(BUILD_DIR, `${decoded.replace(/\/+$/, '')}.html`),
		join(BUILD_DIR, decoded.replace(/\/+$/, ''), 'index.html')
	];
	return candidates.find((candidate) => existsSync(candidate) && statSync(candidate).isFile());
}

// ---- Route list -------------------------------------------------------------

const sitemapFile = join(BUILD_DIR, 'sitemap.xml');
if (!existsSync(sitemapFile)) {
	fail('sitemap.xml is missing, run npm run build first');
}

const sitemapHtml = existsSync(sitemapFile) ? readFileSync(sitemapFile, 'utf8') : '';
const sitemapLocations = [...sitemapHtml.matchAll(/<loc>([^<]+)<\/loc>/g)].map((match) => match[1]);
const sitemapPaths = [];

for (const location of sitemapLocations) {
	let parsed;
	try {
		parsed = new URL(location);
	} catch {
		fail(`sitemap.xml contains an invalid URL: ${location}`);
		continue;
	}
	if (parsed.origin !== ORIGIN) {
		fail(`sitemap.xml URL must use ${ORIGIN}: ${location}`);
		continue;
	}
	const path = parsed.pathname.replace(/\/+$/, '') || '/';
	if (path === NOT_FOUND_ROUTE) fail('sitemap.xml must not contain the 404 page');
	if (sitemapPaths.includes(path)) fail(`sitemap.xml contains duplicate URL ${location}`);
	sitemapPaths.push(path);
}

if (sitemapPaths.length === 0) fail('sitemap.xml contains no routes');
if (!sitemapPaths.includes('/')) fail('sitemap.xml is missing the homepage');

const routes = [...sitemapPaths, NOT_FOUND_ROUTE];
const routeHtml = new Map(routes.map((route) => [route, htmlFor(route)]));

// Every prerendered page must be either indexable (in the sitemap) or the 404 page.
function builtHtmlRoutes(directory) {
	const found = [];
	for (const entry of readdirSync(directory, { withFileTypes: true })) {
		if (entry.name === '_app' || entry.name.startsWith('.')) continue;
		const full = join(directory, entry.name);
		if (entry.isDirectory()) {
			found.push(...builtHtmlRoutes(full));
		} else if (entry.name.endsWith('.html')) {
			const relativePath = relative(BUILD_DIR, full).replace(/\\/g, '/');
			const route = relativePath === 'index.html' ? '/' : `/${relativePath.replace(/\.html$/, '').replace(/\/index$/, '')}`;
			found.push(route);
		}
	}
	return found;
}

if (existsSync(BUILD_DIR)) {
	for (const route of builtHtmlRoutes(BUILD_DIR)) {
		if (route === NOT_FOUND_ROUTE) continue;
		if (!sitemapPaths.includes(route)) warn(`${route}: prerendered page is not in sitemap.xml`);
	}
}

// ---- Per-route checks -------------------------------------------------------

for (const [route, html] of routeHtml) {
	if (!html) continue;
	const isNotFound = route === NOT_FOUND_ROUTE;
	const text = plainText(html);

	if (!/<html\b[^>]*\blang=["']it["']/i.test(html)) fail(`${route}: <html> must declare lang="it"`);

	const title = html.match(/<title>([\s\S]*?)<\/title>/i)?.[1]?.trim();
	const descriptionTag = html.match(/<meta\s+[^>]*name=["']description["'][^>]*>/i)?.[0];
	const canonicalTag = html.match(/<link\s+[^>]*rel=["']canonical["'][^>]*>/i)?.[0];
	const robotsTag = html.match(/<meta\s+[^>]*name=["']robots["'][^>]*>/i)?.[0];
	const h1Count = count(html, /<h1\b[^>]*>/gi);

	const description = descriptionTag ? attribute(descriptionTag, 'content') : undefined;
	const canonical = canonicalTag ? attribute(canonicalTag, 'href') : undefined;
	const robots = robotsTag ? attribute(robotsTag, 'content') : undefined;

	if (!title) fail(`${route}: missing title`);
	if (!description) fail(`${route}: missing meta description`);
	if (!canonical) {
		fail(`${route}: missing canonical link`);
	} else if (!canonical.startsWith(ORIGIN)) {
		fail(`${route}: canonical must start with ${ORIGIN}`);
	} else if (!isNotFound && canonical !== `${ORIGIN}${route}`) {
		fail(`${route}: canonical must be ${ORIGIN}${route}`);
	}

	if (!robots) {
		fail(`${route}: missing robots directive`);
	} else if (isNotFound && !robots.includes('noindex')) {
		fail(`${route}: the 404 page must be noindex`);
	} else if (!isNotFound && !robots.includes('index')) {
		fail(`${route}: missing index robots directive`);
	}

	if (h1Count !== 1) fail(`${route}: expected exactly one H1, found ${h1Count}`);

	if (title) {
		if (titles.has(title)) fail(`${route}: duplicate title also used by ${titles.get(title)}`);
		else titles.set(title, route);
	}
	if (description) {
		if (descriptions.has(description)) fail(`${route}: duplicate description also used by ${descriptions.get(description)}`);
		else descriptions.set(description, route);
	}

	for (const required of ['og:title', 'og:description', 'og:url', 'og:image', 'og:image:alt', 'og:image:width', 'og:image:height']) {
		if (!new RegExp(`<meta\\s+[^>]*property=["']${required}["']`, 'i').test(html)) fail(`${route}: missing ${required}`);
	}
	for (const required of ['twitter:card', 'twitter:title', 'twitter:description', 'twitter:image', 'twitter:image:alt']) {
		if (!new RegExp(`<meta\\s+[^>]*name=["']${required}["']`, 'i').test(html)) fail(`${route}: missing ${required}`);
	}

	const items = jsonLdItems(html, route);
	if (items.length === 0 && !isNotFound) fail(`${route}: missing JSON-LD structured data`);
	if (/aggregateRating/i.test(html)) fail(`${route}: aggregateRating must not be published`);

	for (const match of html.matchAll(/<img\b[^>]*>/gi)) {
		const tag = match[0];
		if (attribute(tag, 'alt') === undefined) fail(`${route}: image missing alt attribute`);
		if (!attribute(tag, 'width') || !attribute(tag, 'height')) fail(`${route}: image missing intrinsic width/height`);
	}

	for (const match of html.matchAll(/<a\b[^>]*href=["']([^"']*)["'][^>]*>/gi)) {
		const href = match[1].trim();
		if (!href || href.startsWith('mailto:') || href.startsWith('tel:') || href.startsWith('javascript:')) continue;

		if (href.startsWith('#')) {
			const id = decodeURIComponent(href.slice(1));
			if (id && !new RegExp(`\\bid=["']${escapeRegExp(id)}["']`).test(html)) fail(`${route}: broken fragment ${href}`);
			continue;
		}
		if (!href.startsWith('/') && !href.startsWith(ORIGIN)) continue;

		let parsed;
		try {
			parsed = new URL(href, ORIGIN);
		} catch {
			fail(`${route}: invalid internal link ${href}`);
			continue;
		}
		if (parsed.origin !== ORIGIN) continue;

		const targetFile = resolveBuildPath(parsed.pathname);
		if (!targetFile) {
			fail(`${route}: broken internal link ${href}`);
			continue;
		}
		if (parsed.hash) {
			const id = decodeURIComponent(parsed.hash.slice(1));
			const targetHtml = readFileSync(targetFile, 'utf8');
			if (id && !new RegExp(`\\bid=["']${escapeRegExp(id)}["']`).test(targetHtml)) {
				fail(`${route}: broken fragment ${href} (no #${id} on ${parsed.pathname || '/'})`);
			}
		}
	}

	const faq = items.find((item) => item && typeof item === 'object' && item['@type'] === 'FAQPage');
	if (faq) {
		if (!Array.isArray(faq.mainEntity) || faq.mainEntity.length === 0) {
			fail(`${route}: FAQPage JSON-LD has no questions`);
		} else {
			if (faq.mainEntity.length < 4) warn(`${route}: FAQPage has only ${faq.mainEntity.length} questions`);
			for (const question of faq.mainEntity) {
				if (!question?.name || !question?.acceptedAnswer?.text) {
					fail(`${route}: FAQ entry is incomplete`);
				} else if (!text.includes(question.name.replace(/\s+/g, ' ').trim())) {
					fail(`${route}: FAQ question is not visible on the page: ${question.name}`);
				}
			}
		}
	}
}

// ---- Sitemap and robots -----------------------------------------------------

const robotsFile = join(BUILD_DIR, 'robots.txt');
if (!existsSync(robotsFile)) fail('robots.txt is missing');
else if (!readFileSync(robotsFile, 'utf8').includes(`Sitemap: ${ORIGIN}/sitemap.xml`))
	fail(`robots.txt must declare Sitemap: ${ORIGIN}/sitemap.xml`);

// ---- Shared assets ----------------------------------------------------------

if (!existsSync(SOCIAL_CARD)) {
	fail('social-card.png is missing');
} else {
	const buffer = readFileSync(SOCIAL_CARD);
	const isPng = buffer.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]));
	if (!isPng) fail('social-card.png is not a PNG file');
	else {
		const width = buffer.readUInt32BE(16);
		const height = buffer.readUInt32BE(20);
		if (width !== 1200 || height !== 630) fail(`social-card.png must be 1200x630, found ${width}x${height}`);
	}
	if (buffer.byteLength > 200_000) warn(`social-card.png exceeds 200 KB (${Math.round(buffer.byteLength / 1024)} KB)`);
}

const cssDirectory = join(BUILD_DIR, '_app/immutable/assets');
const builtCss = existsSync(cssDirectory)
	? readdirSync(cssDirectory)
			.filter((file) => file.endsWith('.css'))
			.map((file) => readFileSync(join(cssDirectory, file), 'utf8'))
			.join('\n')
	: '';
if (!builtCss) warn('could not read the built CSS bundle');
else if (!/@media\s*\(forced-colors:\s*active\)/i.test(builtCss)) warn('production CSS is missing the forced-colors media query');

console.log(`SEO validation: ${errors} error(s), ${warnings} warning(s), ${routes.length} routes checked.`);
if (errors > 0) process.exit(1);
