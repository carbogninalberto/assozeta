// Build the normal application entry and serve it locally for the handoff Playwright project.
import {build, preview} from 'vite';
import {mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';

process.chdir(fileURLToPath(new URL('../', import.meta.url)));
process.env.DEPLOY_ENV = 'development'; // same-origin selfhost OEM, no release stamping
process.env.VITE_DEV_HTTPS = 'false';
const outDir = await mkdtemp(join(tmpdir(), 'p5-runtime-ui-'));
await build({logLevel: 'error', build: {outDir, sourcemap: false, minify: false, reportCompressedSize: false}});
await preview({build: {outDir}, preview: {
    host: '127.0.0.1', port: Number(process.env.P5UI_PORT || 5237), strictPort: true, proxy: {},
}});
