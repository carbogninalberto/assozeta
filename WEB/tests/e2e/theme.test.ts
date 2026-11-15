import { expect, test } from '@playwright/test';

const visible = { visible: true } as const;

test('light is the default theme and the layout uses the brand logo', async ({ page }) => {
	await page.goto('/');

	const html = page.locator('html');
	await expect(html).not.toHaveClass(/dark/);
	await expect(page.locator('img[src="/logo.svg"]').filter(visible).first()).toBeVisible();
	await expect(page.locator('img[src="/logo-white.svg"]').filter(visible)).toHaveCount(0);
	expect(await page.evaluate(() => localStorage.getItem('assozeta-theme'))).not.toBe('dark');
});

test('the toggle switches to dark, swaps the logo and persists after reload', async ({ page }) => {
	await page.goto('/');

	const html = page.locator('html');
	await page.getByRole('button', { name: 'Attiva il tema scuro' }).filter(visible).first().click();

	await expect(html).toHaveClass(/dark/);
	await expect(page.locator('img[src="/logo-white.svg"]').filter(visible).first()).toBeVisible();
	await expect(page.locator('img[src="/logo.svg"]').filter(visible)).toHaveCount(0);
	expect(await page.evaluate(() => localStorage.getItem('assozeta-theme'))).toBe('dark');

	await expect(page.locator('meta[name="theme-color"]')).toHaveAttribute('content', '#131320');

	await page.reload();
	await expect(html).toHaveClass(/dark/);
	await expect(page.locator('img[src="/logo-white.svg"]').filter(visible).first()).toBeVisible();
	await expect(page.locator('meta[name="theme-color"]')).toHaveAttribute('content', '#131320');

	await page.getByRole('button', { name: 'Attiva il tema chiaro' }).filter(visible).first().click();
	await expect(html).not.toHaveClass(/dark/);
	await expect(page.locator('img[src="/logo.svg"]').filter(visible).first()).toBeVisible();
	await expect(page.locator('meta[name="theme-color"]')).toHaveAttribute('content', '#ffffff');
	expect(await page.evaluate(() => localStorage.getItem('assozeta-theme'))).toBe('light');
});

test('a stored dark theme is applied before the first paint', async ({ page }) => {
	await page.addInitScript(() => localStorage.setItem('assozeta-theme', 'dark'));
	// Block the app bundle: only the inline <head> script can add the class.
	await page.route('**/_app/**/*.js', (route) => route.abort());
	await page.goto('/licenza');
	await expect(page.locator('html')).toHaveClass(/dark/);
	await expect(page.locator('img[src="/logo-white.svg"]').filter(visible).first()).toBeVisible();
});

test('the skip link is the first focus stop and moves focus to the main content', async ({ page }) => {
	await page.goto('/sicurezza');
	await page.keyboard.press('Tab');
	const skip = page.getByRole('link', { name: 'Vai al contenuto' });
	await expect(skip).toBeFocused();
	await expect(skip).toBeVisible();
	await page.keyboard.press('Enter');
	await expect(page.locator('main#main')).toBeFocused();
});

test('the mobile menu opens at 320px', async ({ page }) => {
	await page.goto('/');

	await page.getByRole('button', { name: 'Apri il menu' }).click();
	const sheet = page.getByRole('dialog', { name: 'Menu' });
	await expect(sheet).toBeVisible();
	await expect(sheet.getByRole('button', { name: 'Funzionalità' })).toBeVisible();
	expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
