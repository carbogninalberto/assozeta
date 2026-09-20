import {defineConfig} from '@playwright/test';

export default defineConfig({
    testDir: '.',
    testMatch: 'self-instance.spec.js',
    workers: 1,
    fullyParallel: false,
    forbidOnly: true,
    retries: 0, // An upgrade must never be repeated automatically by the test runner.
    timeout: 90000,
    expect: {timeout: 30000},
    reporter: [['list']],
    outputDir: process.env.ASSOZETA_BROWSER_OUTPUT,
    // Exercise normal HTTP browser behavior; production TLS is a separate check.
    use: {browserName: 'chromium', headless: true, trace: 'off', screenshot: 'off', video: 'off',
        // Docker Desktop resolves this name inside containers, not on the Mac host.
        // Map only the disposable fixture hostname; retain normal browser security.
        launchOptions: process.platform === 'darwin' ? {args: ['--host-resolver-rules=MAP host.docker.internal 127.0.0.1']} : {},
    },
});
