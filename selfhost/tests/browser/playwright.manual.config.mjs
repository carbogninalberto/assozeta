import {defineConfig} from '@playwright/test';

export default defineConfig({
    testDir: './manuale',
    workers: 1,
    retries: 0,
    timeout: 240000,
    use: {
        viewport: {width: 1920, height: 1080},
        locale: 'it-IT',
        timezoneId: 'Europe/Rome',
        colorScheme: 'light',
        deviceScaleFactor: 1,
    },
});
