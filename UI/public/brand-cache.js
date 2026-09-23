// Restore the last computed palette before paint, including standalone error/offline pages.
// Palette computation and validation remain in src/utils/BrandTheme.js.
(() => {
    try {
        const cache = JSON.parse(localStorage.getItem('assozeta_brand_palette') || 'null');
        if (!cache) return;
        const user = JSON.parse(localStorage.getItem('userData') || '{}');
        const dark = user?.dark_mode ?? window.matchMedia('(prefers-color-scheme: dark)').matches;
        if (dark) document.documentElement.setAttribute('data-theme', 'dark');
        const values = cache[dark ? 'dark' : 'light'];
        const validValue = /^(#[0-9a-f]{6}|[0-9]+, [0-9]+, [0-9]+|rgba\([0-9]+, [0-9]+, [0-9]+, 0\.[0-9]+\))$/i;
        for (const [key, value] of Object.entries(values || {})) {
            if (/^--(brand-color|primary(?:-hover|-active|-rgb|-text|-subtle-text|-border|-light)?|on-primary(?:-hover|-active)?|main-color|light-primary|text-link(?:-hover)?|border-input-focus|focus-color)$/.test(key) && validValue.test(value)) {
                document.documentElement.style.setProperty(key, value);
            }
        }
        if (/^#[0-9a-f]{6}$/i.test(values?.['--brand-color'] || '')) {
            document.querySelector('meta[name="theme-color"]')?.setAttribute('content', values['--brand-color']);
        }
    } catch { /* A missing or malformed cache falls back to the default stylesheet. */ }
})();
