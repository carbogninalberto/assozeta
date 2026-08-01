#!/usr/bin/env node
/**
 * Copies the official Bootstrap 4 CSS (MIT licensed) from the npm package
 * into public/static/css/ so that index.html, error.html and offline.html
 * can load it as a static asset.
 *
 * Run after `npm install` (or whenever the bootstrap package is updated):
 *   npm run css:vendor-bootstrap
 */
import { copyFileSync, mkdirSync, existsSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const srcDir = resolve(root, 'node_modules/bootstrap/dist/css');
const outDir = resolve(root, 'public/static/css');

if (!existsSync(resolve(srcDir, 'bootstrap.min.css'))) {
    console.error('bootstrap package not found in node_modules. Run `npm install` first.');
    process.exit(1);
}

mkdirSync(outDir, { recursive: true });
for (const file of ['bootstrap.min.css', 'bootstrap.min.css.map']) {
    copyFileSync(resolve(srcDir, file), resolve(outDir, file));
    console.log(`Copied ${file} -> public/static/css/${file}`);
}
