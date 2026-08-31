import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';


const source = readFileSync(new URL('./SharedCalendar.svelte', import.meta.url), 'utf8');


test('shared calendar loader gives SVG a numeric viewBox size', () => {
    const loader = source.match(/<ContentLoader[\s\S]*?>/)?.[0] || '';

    assert.match(loader, /width=\{\d+\}/);
    assert.match(loader, /height=\{\d+\}/);
    assert.doesNotMatch(loader, /width="\d+%"/);
});
