import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {compile} from 'svelte/compiler';
import {build} from 'esbuild';

const source = fileURLToPath(new URL('./PermissionsComponent.svelte', import.meta.url));
const compiled = await build({
    stdin: {contents: `export {default} from ${JSON.stringify(source)}`, resolveDir: path.dirname(source)},
    bundle: true, write: false, format: 'esm', platform: 'node',
    mainFields: ['svelte', 'module', 'main'], conditions: ['svelte'],
    plugins: [{name: 'svelte-ssr', setup(b) {
        b.onLoad({filter: /\.svelte$/}, ({path: filename}) => ({
            contents: compile(fs.readFileSync(filename, 'utf8'), {filename, generate: 'ssr'}).js.code,
            resolveDir: path.dirname(filename),
        }));
    }}],
});
const component = (await import('data:text/javascript;base64,' + Buffer.from(compiled.outputFiles[0].text).toString('base64'))).default;
const checkedNames = html => [...html.matchAll(/<input\b[^>]*>/g)]
    .map(([tag]) => tag).filter(tag => /\bchecked(?:\s|=|>)/.test(tag))
    .map(tag => tag.match(/\bname="([^"]+)"/)[1]);

for (const value of [{nested: [true, 1, null], 'association.dashboard.read': true}, null,
    'association.dashboard.read', 42, ['association.dashboard.read', 'association.calendar.read']]) {
    test(`renders ${JSON.stringify(value)} without mutating its bound value`, () => {
        const original = structuredClone(value);
        const props = {currentCollaboratorPermissions: value};
        const {html} = component.render(props);
        assert.equal(props.currentCollaboratorPermissions, value);
        assert.deepEqual(value, original);
        assert.deepEqual(checkedNames(html), Array.isArray(value) ? value : []);
    });
}
