import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import {compileManual} from './compile.mjs';

function fixture(t) {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'manual-compile-'));
    t.after(() => fs.rmSync(root, {recursive: true, force: true}));
    const manual = path.join(root, 'manual'), output = path.join(root, 'output');
    fs.mkdirSync(path.join(manual, 'docs'), {recursive: true});
    fs.mkdirSync(output);
    fs.writeFileSync(path.join(manual, 'mint.json'), JSON.stringify({navigation: [{pages: ['docs/guida', 'docs/altro']}]}));
    fs.writeFileSync(path.join(manual, 'docs/guida.mdx'), '---\ntitle: Guida\n---\n\n## Introduzione\n\n<Card href="altro#caffe" title="Leggi" />');
    fs.writeFileSync(path.join(manual, 'docs/altro.mdx'), '## Caffè\n\n<Steps><Step title="Salva">Premi **Salva**.</Step></Steps>');
    return {manual, output};
}

test('compiles real MDX and checks relative links and accented section IDs without evaluating code', async t => {
    const {manual, output} = fixture(t);
    fs.appendFileSync(path.join(manual, 'docs/altro.mdx'), '\n\nexport const unused = (() => { throw new Error("must not execute"); })()\n');
    const result = await compileManual(manual, output);
    assert.equal(result.status, 'passed');
    assert.equal(result.pages.length, 2);
    assert.equal(result.pages[0].references, 1);
    assert.match(fs.readFileSync(path.join(output, 'compiled/docs/altro.js'), 'utf8'), /must not execute/);
});

test('invalid MDX or local references cannot replace a successful compiled artifact', async t => {
    const {manual, output} = fixture(t);
    await compileManual(manual, output);
    const artifact = path.join(output, 'compiled/docs/guida.js');
    const before = fs.readFileSync(artifact);
    for (const content of [
        '## Introduzione\n\n<Step>Unclosed',
        '## Introduzione\n\n[Altro](altro#assente)',
        '## Introduzione\n\n![Assente](/images/missing.png)',
        '## Introduzione\n\n<Card href="javascript:alert(1)" />',
        '## Introduzione\n\n<Card href={"altro"} />',
    ]) {
        fs.writeFileSync(path.join(manual, 'docs/guida.mdx'), content);
        await assert.rejects(compileManual(manual, output));
        assert.deepEqual(fs.readFileSync(artifact), before);
    }
});

test('reference links, images and nested navigation are validated', async t => {
    const {manual, output} = fixture(t);
    fs.writeFileSync(path.join(manual, 'mint.json'), JSON.stringify({navigation: [{pages: [
        'docs/guida', {group: 'Nested', pages: ['docs/altro']},
    ]}]}));
    fs.mkdirSync(path.join(manual, 'images'));
    // This test verifies reference resolution; image integrity belongs to the evidence validator.
    fs.writeFileSync(path.join(manual, 'images/example.png'), 'unit fixture');
    fs.writeFileSync(path.join(manual, 'docs/guida.mdx'), '## Guida\n\n[Leggi][other]\n\n![Immagine](/images/example.png)\n\n[other]: altro#caffe\n');
    const first = await compileManual(manual, output);
    assert.equal(first.pages[0].references, 2);
    assert.deepEqual(first.pages[0].assets, {'images/example.png': crypto.createHash('sha256').update('unit fixture').digest('hex')});
    assert.deepEqual(first.pages[1].assets, {});
    fs.writeFileSync(path.join(manual, 'images/example.png'), 'changed unit asset');
    const second = await compileManual(manual, output);
    assert.notEqual(second.pages[0].assets['images/example.png'], first.pages[0].assets['images/example.png']);
    fs.unlinkSync(path.join(manual, 'images/example.png'));
    await assert.rejects(compileManual(manual, output));
});

test('navigation cannot escape the manual through symlinks or parent paths', async t => {
    const {manual, output} = fixture(t);
    fs.writeFileSync(path.join(output, 'private.mdx'), '## Private');
    fs.symlinkSync(path.join(output, 'private.mdx'), path.join(manual, 'docs/private.mdx'));
    for (const entry of ['docs/private', '../output/private']) {
        fs.writeFileSync(path.join(manual, 'mint.json'), JSON.stringify({navigation: [{pages: [entry]}]}));
        await assert.rejects(compileManual(manual, output), /escapes/);
    }
});
