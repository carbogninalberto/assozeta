import {defineConfig} from '@playwright/test';
export default defineConfig({
    testDir: '.', testMatch: 'self-instance-reload.spec.js', workers: 1, retries: 0,
    timeout: 60000, expect: {timeout: 15000},
    use: {baseURL: 'http://127.0.0.1:5194', headless: true},
    outputDir: process.env.ASSOZETA_RELOAD_BROWSER_OUTPUT || '/tmp/assozeta-reload-browser-results',
    webServer: {command: 'node ../../../UI/scripts/self-instance-reload-fixture.mjs', url: 'http://127.0.0.1:5194/healthz', reuseExistingServer: false, timeout: 120000, gracefulShutdown: {signal: 'SIGTERM', timeout: 15000}},
});
