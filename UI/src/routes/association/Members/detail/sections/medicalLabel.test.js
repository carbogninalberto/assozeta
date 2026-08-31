import { readFileSync } from 'node:fs';
import test from 'node:test';
import assert from 'node:assert/strict';

const source = readFileSync(new URL('./Medical.svelte', import.meta.url), 'utf8');

test('uses the backend countdown consistently when the certificate or its expiry is missing', () => {
    assert.match(source, /info\.plain_medical_label/);
    assert.doesNotMatch(source, /scadenza non presente/);
    assert.doesNotMatch(source, /nessun certificato medico presente/);
    assert.doesNotMatch(source, /Mancante da 0 giorni/);
});

test('shows the missing-certificate countdown prominently before the image', () => {
    const sectionStart = source.indexOf('<div class="row pt-4 pb-4">');
    const section = source.slice(sectionStart);
    const labelIndex = section.indexOf('info.plain_medical_label');
    const imageIndex = section.indexOf('alt="no medical certificate"');

    assert.ok(sectionStart >= 0);
    assert.ok(labelIndex >= 0);
    assert.ok(imageIndex >= 0);
    assert.ok(labelIndex < imageIndex);
    assert.match(
        section,
        /medical-missing-status[^"]*border border-warning bg-light-warning rounded-xl/,
    );
    assert.match(section, /Certificato medico[\s\S]*info\.plain_medical_label/);
});
