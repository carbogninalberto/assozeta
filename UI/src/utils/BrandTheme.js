import {writable, get} from 'svelte/store';

export const DEFAULT_BRAND_COLOR = '#351dc2';
export const validBrandColor = value => /^#[0-9a-f]{6}$/i.test(value || '');
const rgb = color => color.slice(1).match(/../g).map(value => parseInt(value, 16));
export function mixColor(color, target, amount) {
    const other = rgb(target);
    return '#' + rgb(color).map((value, i) => Math.round(value + (other[i] - value) * amount).toString(16).padStart(2, '0')).join('');
}
export function luminance(color) {
    return rgb(color).map(value => {
        value /= 255;
        return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
    }).reduce((sum, value, i) => sum + value * [0.2126, 0.7152, 0.0722][i], 0);
}
export function contrastRatio(a, b) {
    const values = [luminance(a), luminance(b)].sort((a, b) => b - a);
    return (values[0] + 0.05) / (values[1] + 0.05);
}
export function foregroundFor(color) {
    return contrastRatio(color, '#ffffff') >= contrastRatio(color, '#000000') ? '#ffffff' : '#000000';
}
function readableColor(color, surface) {
    const target = foregroundFor(surface);
    for (let step = 0; step <= 100; step++) {
        const candidate = mixColor(color, target, step / 100);
        if (contrastRatio(candidate, surface) >= 4.5) return candidate;
    }
    return target;
}
export function createBrandPalette(color = DEFAULT_BRAND_COLOR, dark = false) {
    const brand = validBrandColor(color) ? color.toLowerCase() : DEFAULT_BRAND_COLOR;
    const surface = dark ? '#1b1b2d' : '#ffffff';
    const primary = dark ? readableColor(brand, surface) : brand;
    const hover = mixColor(primary, foregroundFor(primary) === '#ffffff' ? '#000000' : '#ffffff', 0.12);
    const active = mixColor(primary, foregroundFor(primary) === '#ffffff' ? '#000000' : '#ffffff', 0.22);
    const subtle = mixColor(surface, primary, dark ? 0.18 : 0.10);
    return {brand, dark, primary, hover, active, surface, subtle,
        foreground: foregroundFor(primary), hoverForeground: foregroundFor(hover), activeForeground: foregroundFor(active),
        text: readableColor(primary, surface), subtleText: readableColor(primary, subtle),
        border: mixColor(surface, primary, 0.4), rgb: rgb(primary).join(', '),
        focus: `rgba(${rgb(primary).join(', ')}, 0.35)`};
}
export function paletteProperties(palette) {
    return {
        '--brand-color': palette.brand, '--primary': palette.primary, '--main-color': palette.primary,
        '--primary-hover': palette.hover, '--primary-active': palette.active,
        '--on-primary': palette.foreground, '--on-primary-hover': palette.hoverForeground,
        '--on-primary-active': palette.activeForeground, '--primary-rgb': palette.rgb,
        '--primary-text': palette.text, '--primary-subtle-text': palette.subtleText,
        '--primary-border': palette.border, '--light-primary': palette.subtle, '--primary-light': palette.subtle,
        '--text-link': palette.text, '--text-link-hover': readableColor(palette.hover, palette.surface),
        '--border-input-focus': palette.text, '--focus-color': palette.focus,
    };
}
export const brandPalette = writable(createBrandPalette());
let brandColor = DEFAULT_BRAND_COLOR;
let observer;
function refreshPalette() {
    const root = typeof document !== 'undefined' ? document.documentElement : null;
    const palette = createBrandPalette(brandColor, root?.getAttribute('data-theme') === 'dark');
    if (root) {
        for (const [key, value] of Object.entries(paletteProperties(palette))) root.style.setProperty(key, value);
        // Match the manifest's configured brand, independently of UI dark adaptation.
        document.querySelector('meta[name="theme-color"]')?.setAttribute('content', palette.brand);
    }
    brandPalette.set(palette);
}
export function applyBrandColor(color) {
    brandColor = validBrandColor(color) ? color.toLowerCase() : DEFAULT_BRAND_COLOR;
    refreshPalette();
    try {
        localStorage.setItem('assozeta_brand_palette', JSON.stringify({
            light: paletteProperties(createBrandPalette(brandColor, false)),
            dark: paletteProperties(createBrandPalette(brandColor, true)),
        }));
    } catch { /* Storage may be unavailable; the live theme still applies. */ }
    if (!observer && typeof MutationObserver !== 'undefined' && typeof document !== 'undefined') {
        observer = new MutationObserver(refreshPalette);
        observer.observe(document.documentElement, {attributes: true, attributeFilter: ['data-theme']});
    }
}
export function getBrandPalette() { return get(brandPalette); }
export function brandScopeStyle(color, dark = false) {
    return Object.entries(paletteProperties(createBrandPalette(color, dark))).map(([key, value]) => `${key}:${value}`).join(';');
}
export function stripeAppearance(palette = getBrandPalette()) {
    return {theme: 'flat', variables: {colorPrimary: palette.primary,
        colorBackground: palette.dark ? '#252540' : '#f3f6f9', colorText: palette.dark ? '#e1e2e8' : '#181c32',
        colorTextPlaceholder: palette.dark ? '#a0a3bd' : '#626777'},
        rules: {'.Tab--selected': {color: palette.text}, '.Input:focus': {boxShadow: `0 0 0 2px ${palette.focus}`}}};
}
