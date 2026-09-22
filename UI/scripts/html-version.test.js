import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';

const config = fs.readFileSync(new URL('../vite.config.js', import.meta.url), 'utf8');
const start = config.indexOf('function setFileVersion(');
const end = config.indexOf('\nif (DEPLOY_ENV', start);
const setFileVersion = vm.runInNewContext(`(${config.slice(start, end).trim()})`, {fs});

test('HTML version updates are complete before the build can read the entry', () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'assozeta-html-version-'));
    try {
        const original = fs.readFileSync(new URL('../index.html', import.meta.url), 'utf8');
        for (const name of ['index.html', 'offline.html']) {
            const filename = path.join(directory, name);
            fs.writeFileSync(filename, original);
            setFileVersion(filename, '123456789');
            const expected = original.replace(/\?v=[0-9]*/gim, '?v=123456789');
            assert.equal(fs.readFileSync(filename, 'utf8'), expected);
            setFileVersion(filename, '123456789');
            assert.equal(fs.readFileSync(filename, 'utf8'), expected);
        }
    } finally {
        fs.rmSync(directory, {recursive: true, force: true});
    }
});
