import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const read = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');

// The registry is TypeScript, so the checks parse it as text instead of importing it.
// Keep these lists in sync with BRIEF section 6 when the information architecture changes.
const FEATURE_PATHS = [
	'/funzionalita/soci-e-tesserati',
	'/funzionalita/iscrizioni-online',
	'/funzionalita/corsi-e-presenze',
	'/funzionalita/certificati-e-documenti',
	'/funzionalita/pagamenti-e-ricevute',
	'/funzionalita/contabilita',
	'/funzionalita/comunicazioni-e-automazioni',
	'/funzionalita/bacheca-e-report'
];

const SELF_HOSTING_PATHS = [
	'/self-hosting/requisiti',
	'/self-hosting/installazione',
	'/self-hosting/configurazione',
	'/self-hosting/aggiornamenti',
	'/self-hosting/backup-e-ripristino',
	'/self-hosting/manutenzione'
];

const STANDALONE_PATHS = ['/open-source', '/licenza', '/sicurezza', '/chi-siamo', '/privacy'];

const routesSource = read('src/lib/data/routes.ts');
const sitemapSource = read('src/routes/sitemap.xml/+server.ts');
const caddyfile = read('deploy/Caddyfile');
const robots = read('static/robots.txt');

function between(source, startMarker, endMarker) {
	const start = source.indexOf(startMarker);
	assert.notEqual(start, -1, `missing marker ${startMarker}`);
	const end = source.indexOf(endMarker, start + startMarker.length);
	assert.notEqual(end, -1, `missing marker ${endMarker}`);
	return source.slice(start, end);
}

const featureRegistry = between(routesSource, 'funzionalita: {', 'selfHosting: {');
const selfHostingRegistry = between(routesSource, 'selfHosting: {', '} satisfies Record<string, Hub>');
const standaloneRegistry = between(routesSource, 'export const STANDALONE_PAGES', '// ---- All indexable routes');

const hrefs = (source) => [...source.matchAll(/href:\s*'([^']+)'/g)].map((match) => match[1]);
const paths = (source) => [...source.matchAll(/path:\s*'([^']+)'/g)].map((match) => match[1]);

test('the feature hub registers exactly the eight feature slugs', () => {
	const children = hrefs(featureRegistry).filter((href) => href.startsWith('/funzionalita/'));
	assert.deepEqual(children.sort(), [...FEATURE_PATHS].sort());
	assert.ok(hrefs(featureRegistry).includes('/funzionalita'), 'feature hub root is missing');
});

test('the self-hosting hub registers exactly the six guide slugs', () => {
	const children = hrefs(selfHostingRegistry).filter((href) => href.startsWith('/self-hosting/'));
	assert.deepEqual(children.sort(), [...SELF_HOSTING_PATHS].sort());
	assert.ok(hrefs(selfHostingRegistry).includes('/self-hosting'), 'self-hosting hub root is missing');
});

test('the standalone pages are the five project pages', () => {
	assert.deepEqual(paths(standaloneRegistry).sort(), [...STANDALONE_PATHS].sort());
});

test('allRoutes covers the homepage, the two hubs, their children and the standalone pages', () => {
	assert.match(routesSource, /add\('\/',/);
	assert.match(routesSource, /for \(const hub of Object\.values\(HUBS\)\)/);
	assert.match(routesSource, /for \(const child of hub\.children\)/);
	assert.match(routesSource, /for \(const page of STANDALONE_PAGES\)/);
	// The 404 page is not indexable and must never enter the sitemap.
	assert.doesNotMatch(routesSource, /['"]\/404['"]/);
});

test('the sitemap is generated from the route registry and the canonical origin', () => {
	assert.match(sitemapSource, /import \{ ORIGIN \} from '\$lib\/config\/site'/);
	assert.match(sitemapSource, /import \{ allRoutes \} from '\$lib\/data\/routes'/);
	assert.match(sitemapSource, /allRoutes\(\)/);
	assert.match(sitemapSource, /prerender = true/);
});

test('the web server serves prerendered files and returns a real 404 page', () => {
	assert.match(caddyfile, /try_files \{path\} \{path\}\.html \{path\}\/index\.html/);
	assert.match(caddyfile, /handle_errors 404 \{[\s\S]*?rewrite \* \/404\.html/);
	assert.doesNotMatch(caddyfile, /try_files[^\n]*\s\/index\.html\b/, 'no catch-all to /index.html');
	assert.doesNotMatch(caddyfile, /^\s*redir\s/m, 'no moved-page redirects on this site');
	assert.match(caddyfile, /assozeta\.it/);
});

test('the web server sets the documented security headers', () => {
	assert.match(caddyfile, /X-Content-Type-Options nosniff/);
	assert.match(caddyfile, /Referrer-Policy strict-origin-when-cross-origin/);
	assert.match(caddyfile, /X-Frame-Options DENY/);
	assert.match(caddyfile, /Permissions-Policy "camera=\(\), microphone=\(\), geolocation=\(\)"/);
	assert.match(caddyfile, /@immutable path \/_app\/immutable\/\*[\s\S]*?max-age=31536000, immutable/);
});

test('a stored dark theme is applied before first paint', () => {
	const appHtml = read('src/app.html');
	const head = between(appHtml, '<head>', '%sveltekit.head%');
	// The inline script must run in <head>, before the body renders, and read the same key the toggle writes.
	assert.match(head, /<script>[\s\S]*localStorage\.getItem\('assozeta-theme'\) === 'dark'[\s\S]*classList\.add\('dark'\)[\s\S]*<\/script>/);
	assert.match(read('src/lib/theme.svelte.ts'), /STORAGE_KEY = 'assozeta-theme'/);
});

test('robots.txt allows crawling and points to the canonical sitemap', () => {
	assert.match(robots, /^User-agent: \*$/m);
	assert.match(robots, /^Disallow:\s*$/m);
	assert.match(robots, /^Sitemap: https:\/\/assozeta\.it\/sitemap\.xml$/m);
});
