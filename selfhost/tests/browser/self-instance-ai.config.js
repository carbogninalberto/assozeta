import {defineConfig} from '@playwright/test';
export default defineConfig({
    testDir: '.', testMatch: 'self-instance-ai.spec.js', workers: 1, retries: 0,
    timeout: 60000, expect: {timeout: 15000},
    use: {baseURL: 'http://127.0.0.1:5193', headless: true},
    outputDir: '/tmp/assozeta-ai-browser-results',
    webServer: {command: 'node ../../../UI/scripts/self-instance-fixture.mjs', url: 'http://127.0.0.1:5193', reuseExistingServer: false, timeout: 60000},
});
