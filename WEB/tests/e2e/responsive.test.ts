import { expect, test } from '@playwright/test';

type Overflow = { route: string; theme: string; overflow: number; offenders: unknown[] };

for (const theme of ['light', 'dark'] as const) {
	test(`every route fits a 320px viewport in ${theme} mode`, async ({ page, request }) => {
		const sitemap = await request.get('/sitemap.xml');
		expect(sitemap.ok()).toBe(true);
		const xml = await sitemap.text();
		const routes = [...xml.matchAll(/<loc>([^<]+)<\/loc>/g)].map((match) => new URL(match[1]).pathname);
		expect(routes.length).toBeGreaterThan(0);

		await page.addInitScript((value) => localStorage.setItem('assozeta-theme', value), theme);
		await page.goto(routes[0], { waitUntil: 'networkidle' });
		if (theme === 'dark') await expect(page.locator('html')).toHaveClass(/dark/);
		else await expect(page.locator('html')).not.toHaveClass(/dark/);

		const overflowingRoutes: Overflow[] = [];

		for (const route of routes) {
			await page.goto(route, { waitUntil: 'networkidle' });
			const layout = await page.evaluate(() => {
				const viewportWidth = window.innerWidth;
				const scrollWidth = Math.max(document.documentElement.scrollWidth, document.body.scrollWidth);
				const offenders = [...document.body.querySelectorAll<HTMLElement>('*')]
					.map((element) => ({ element, rect: element.getBoundingClientRect() }))
					.filter(({ rect }) => rect.right > viewportWidth + 0.5 || rect.left < -0.5)
					.slice(0, 5)
					.map(({ element, rect }) => ({
						tag: element.tagName.toLowerCase(),
						className: element.className,
						left: Math.round(rect.left),
						right: Math.round(rect.right)
					}));
				return { viewportWidth, scrollWidth, offenders };
			});

			if (layout.scrollWidth > layout.viewportWidth) {
				overflowingRoutes.push({
					route,
					theme,
					overflow: layout.scrollWidth - layout.viewportWidth,
					offenders: layout.offenders
				});
			}
		}

		expect(overflowingRoutes).toEqual([]);
	});
}
