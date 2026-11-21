import {defineConfig} from '@playwright/test';
import {join} from 'node:path';
import {tmpdir} from 'node:os';

const baseURL = 'http://127.0.0.1:' + (process.env.P5UI_PORT || 5237);
export default defineConfig({
    testDir: '.', testMatch: 'runtime-settings.spec.js', workers: 1, fullyParallel: false, retries: 0, forbidOnly: true,
    timeout: 60000, expect: {timeout: 10000},
    use: {baseURL, browserName: 'chromium', serviceWorkers: 'block'},
    outputDir: join(process.env.P5UI_OUTPUT || tmpdir(), 'assozeta-p5-runtime-results'),
    webServer: {command: 'node ../../../UI/scripts/p5-runtime-server.mjs', url: baseURL,
        reuseExistingServer: false, timeout: 180000, stdout: 'ignore', stderr: 'pipe'},
});
