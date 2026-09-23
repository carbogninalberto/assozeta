import test from 'node:test';
import assert from 'node:assert/strict';
import {createBrandPalette, contrastRatio, paletteProperties, applyBrandColor, getBrandPalette, stripeAppearance} from './BrandTheme.js';

test('brand palette retains the configured hue and provides readable foregrounds in both modes', () => {
    for (const color of ['#087e54', '#f9e300', '#ffffff', '#000000', '#fa82bc', '#143882']) {
        for (const dark of [false, true]) {
            const p = createBrandPalette(color, dark);
            assert.equal(p.brand, color);
            if (!dark) assert.equal(p.primary, color);
            for (const [bg, fg] of [['primary', 'foreground'], ['hover', 'hoverForeground'], ['active', 'activeForeground']]) {
                assert.ok(contrastRatio(p[bg], p[fg]) >= 4.5, `${color} ${dark} ${bg}`);
            }
            assert.ok(contrastRatio(p.text, p.surface) >= 4.5);
            assert.ok(contrastRatio(p.subtleText, p.subtle) >= 4.5);
            const css = paletteProperties(p);
            assert.equal(css['--main-color'], css['--primary']);
            assert.equal(css['--text-link'], p.text);
            assert.equal(stripeAppearance(p).variables.colorPrimary, p.primary);
        }
    }
});
test('invalid configuration cannot inject CSS; changing color updates subscribers', () => {
    applyBrandColor('#087e54');
    assert.equal(getBrandPalette().primary, '#087e54');
    applyBrandColor('red; background:url(invalid)');
    assert.equal(getBrandPalette().brand, '#351dc2');
});
