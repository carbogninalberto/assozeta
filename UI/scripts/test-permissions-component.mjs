// Real compiled component events and bound updates; no API or persisted state.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {compile} from '../node_modules/svelte/src/compiler/index.js';
import {build} from '../node_modules/esbuild/lib/main.js';
import {chromium, expect} from '../../selfhost/tests/browser/node_modules/@playwright/test/index.mjs';
const ui = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const source = process.argv[2] ? path.resolve(process.argv[2]) : path.join(ui, 'src/components/PermissionsComponent.svelte');
const compiled = await build({
    stdin: {contents: `import Component from ${JSON.stringify(source)}; window.Component = Component;`, resolveDir: ui},
    bundle: true, write: false, format: 'iife', platform: 'browser',
    nodePaths: [ui + '/node_modules'], mainFields: ['svelte', 'browser', 'module', 'main'], conditions: ['svelte', 'browser'],
    plugins: [{name: 'svelte-dom', setup(b) {
        b.onLoad({filter: /\.svelte$/}, ({path: filename}) => ({
            contents: compile(fs.readFileSync(filename, 'utf8'), {filename, generate: 'dom', css: 'injected'}).js.code,
            resolveDir: path.dirname(filename),
        }));
    }}],
});
const browser = await chromium.launch({headless: true});
try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.name));
    await page.setContent('<main id="app"></main>');
    await page.addScriptTag({content: compiled.outputFiles[0].text});
    await page.evaluate(() => {
        window.initial = {'association.dashboard.read': true, nested: [true, 1, null]};
        window.component = new window.Component({target: document.querySelector('#app'), props: {currentCollaboratorPermissions: window.initial}});
    });
    const checked = () => page.locator('#app input:checked').evaluateAll(inputs => inputs.map(input => input.name).sort());
    const expectChecked = names => expect.poll(checked).toEqual([...names].sort());
    await expectChecked([]);
    assert.deepEqual(await page.evaluate(() => window.initial), {'association.dashboard.read': true, nested: [true, 1, null]});
    await page.evaluate(() => window.component.$set({currentCollaboratorPermissions: ['association.calendar.read']}));
    await expectChecked(['association.calendar.read']);
    const group = page.locator('#app .form-group').filter({has: page.getByRole('heading', {name: 'Dashboard', exact: true})});
    await expect(group).toHaveCount(1);
    await group.locator('.d-flex.justify-content-end a').nth(1).click();
    await expectChecked(['association.calendar.read', 'association.dashboard.read']);
    await page.getByText('Seleziona tutto', {exact: true}).click();
    const all = await page.locator('#app input[type="checkbox"]').evaluateAll(inputs => inputs.map(input => input.name));
    assert(all.length > 1);
    await expectChecked(all);
    await group.locator('.d-flex.justify-content-end a').nth(0).click();
    await expectChecked(all.filter(name => name !== 'association.dashboard.read'));
    await page.getByText('Rimuovi tutto', {exact: true}).click();
    await expectChecked([]);
    await page.locator('input[name="association.dashboard.read"]').click();
    await expectChecked(['association.dashboard.read']);
    await page.evaluate(() => window.component.$set({currentCollaboratorPermissions: {'association.calendar.read': true}}));
    await expectChecked([]);
    await group.locator('.d-flex.justify-content-end a').nth(1).click();
    await expectChecked(['association.dashboard.read']);
    assert.deepEqual(errors, []);
    console.log('PASS: actual permission component preserves invalid input and updates individual, group, bulk and bound selections');
} finally {
    await browser.close();
}
