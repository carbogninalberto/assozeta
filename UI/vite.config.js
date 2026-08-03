/* eslint-disable no-undef */
import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import sveltePreprocess from 'svelte-preprocess';
// import basicSsl from '@vitejs/plugin-basic-ssl';
import mkcert from 'vite-plugin-mkcert';
import * as path from 'path';
import * as fs from 'fs';
import { buildConfig } from './endpoints';
import oems from './oems.json';

const CURRENT_OEM_ENV = process.env.OEM_ENV || "assozeta";
const OEM = oems[CURRENT_OEM_ENV];
const CLIENT_ID = process.env.CLIENT_ID || "";
const APPLE_CLIENT_ID = process.env.APPLE_CLIENT_ID || "";
const DEV_HTTPS = process.env.VITE_DEV_HTTPS !== 'false';
const API_PROXY_TARGET = process.env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000';
const WS_PROXY_TARGET = process.env.VITE_WS_PROXY_TARGET || 'ws://127.0.0.1:8000';

console.log("OEM: ", JSON.stringify(OEM, null, 2));

const DEPLOY_ENV = process.env.DEPLOY_ENV;
const production = DEPLOY_ENV == 'production';//!process.env.ROLLUP_WATCH;
const ignoredWorkspaceDirs = [
    '.github/**',
    '.claude/**',
    '.idea/**',
    '.vscode/**',
    '.vs/**',
    'android/**',
    'ios/**',
    'testing/**',
    'tests/**',
    'resources/**',
    'docs/**',
];

const projectRootDir = path.resolve(__dirname);
let apiHost; //"/api/v1";
let domain;

// TODO: manage url for production env, feed flag from build command
if (DEPLOY_ENV == 'development') {
    apiHost = OEM?.hosts?.staging?.api || "/api";
    domain = OEM?.hosts?.staging?.frontend || "";
} else if (DEPLOY_ENV == "staging") {
    apiHost = OEM?.hosts?.staging?.api || "/api";
    domain = OEM?.hosts?.staging?.frontend || "";
} else if (DEPLOY_ENV == "production") {
    apiHost =  OEM?.hosts?.prod?.api || "/api";
    domain = OEM?.hosts?.prod?.frontend || "";
} else {
    throw new Error("DEPLOY ENVIRONMENT MUST BE SPECIFIED: ['development', 'staging', 'production']");
}

const lines = fs.readFileSync('build.versions', 'utf-8').replaceAll("\r", "").split('\n').filter(Boolean);
const releaseNotesUI = fs.readFileSync('release_notes.txt', 'utf-8');



// UI VERSION
const d = new Date();
const versionUI = String(lines[lines.length-1].split(";")[0]).match(/v\d+.\d+.\d+/gm) 
					+ `-${String(d.getHours()).padStart(2, '0')}${String(d.getMinutes()).padStart(2, '0')}`;

// vX.Y.Z -> 0X00Y000Z | ex. v1.30.245 -> 010300245
const strVersion = String(lines[lines.length-1].split(";")[0]).match(/\d+.\d+.\d+/gm)[0].split(".");
const version = `${strVersion[0].padStart(2, '0')}${strVersion[1].padStart(3, '0')}${strVersion[2].padStart(4, '0')}`;


function setFileVersion(filename, ver){
    fs.readFile(filename, 'utf-8', function(err, data){
        if (err) throw err;
        var newValue = data.replace(/\?v=[0-9]*/gim, `?v=${ver}`);
        fs.writeFile(filename, newValue, 'utf-8', function (err) {
            if (err) throw err;
        });
    });

}

if (DEPLOY_ENV == "production" || DEPLOY_ENV == "staging") setFileVersion('index.html', version);
if (DEPLOY_ENV == "production" || DEPLOY_ENV == "staging") setFileVersion('public/offline.html', version);


let oemConfig = buildConfig({
    apiHost,
    domain,
    version,
    versionUI,
    releaseNotesUI,
    DEPLOY_ENV,
    CLIENT_ID,
    APPLE_CLIENT_ID,
}, OEM);

// https://vitejs.dev/config/
export default defineConfig({
    optimizeDeps: {
        include: ['jose', 'uuid'],
    },
    resolve:{
        alias:{
            'utils': path.resolve(projectRootDir, 'src/utils'),
            'components': path.resolve(projectRootDir, 'src/components'),
            'store': path.resolve(projectRootDir, 'src/store'),
            'routes': path.resolve(projectRootDir, 'src/routes'),
            'ui': path.resolve(projectRootDir, 'src/ui'),
            'shim': path.resolve(projectRootDir, 'src/shim'),
        },
    },
    build: {
        outDir: './dist/public',
        sourcemap: "hidden",
        rollupOptions: {
            treeshake: true,
            output: {
                assetFileNames: 'build-assets/[name]-[hash][extname]',
                chunkFileNames: 'build-assets/[name]-[hash].js',
                entryFileNames: 'build-assets/[name]-[hash].js',
            }
        },
    },
    server: {
        host: "0.0.0.0",
        port: 5001,
        https: DEV_HTTPS,
        watch: {
            ignored: ignoredWorkspaceDirs,
            usePolling: process.env.VITE_USE_POLLING === 'true',
        },
        proxy: {
            '/api': {
                target: API_PROXY_TARGET,
                // changeOrigin: true,
                // secure: true,
                rewrite: (path) => path.replace(/^\/api/, ''),
				configure: (proxy) => {
					// Keep connections alive to avoid reconnection overhead
					proxy.on('proxyReq', (proxyReq) => {
						proxyReq.setHeader('Connection', 'keep-alive');
					});
				}
            },
            '/ws': {
                target: WS_PROXY_TARGET,
                ws: true,
                changeOrigin: true,
                secure: false,
            }
        }
    },
    define: {
        ...oemConfig,
        ...(OEM.selfHosted ? { '__bakney.env.DOMAIN': 'globalThis.location.origin' } : {})
    },
    plugins: [
        // basicSsl(),
        DEV_HTTPS && mkcert(),
        svelte({
        configFile: false,
        compilerOptions: {
            dev: !production,
            enableSourcemap: true,
        },
        hot: {
            preserveLocalState: true
        },
        preprocess: sveltePreprocess({
            typescript: true,
            postcss: true,
            sourceMap: true,
        }),
    })].filter(Boolean),
});
