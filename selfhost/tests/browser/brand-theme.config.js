import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'.',testMatch:'brand-theme.spec.js',workers:1,retries:0,timeout:60000,
    use:{baseURL:'http://127.0.0.1:5194',headless:true},outputDir:'/tmp/assozeta-brand-browser-results',
    webServer:{command:'node ../../../UI/scripts/brand-theme-fixture.mjs',url:'http://127.0.0.1:5194',reuseExistingServer:false,timeout:60000}});
