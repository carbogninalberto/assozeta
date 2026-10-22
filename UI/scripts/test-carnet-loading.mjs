// Exercise the actual Usage and BKNDatatable mount while parent info is pending.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium, expect} from '../../selfhost/tests/browser/node_modules/@playwright/test/index.mjs';
import {compile} from '../node_modules/svelte/src/compiler/index.js';
import {build} from '../node_modules/esbuild/lib/main.js';
const ui=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const source=process.argv[2] ? path.resolve(process.argv[2]) : ui;
let browser;
try {
 const result=await build({stdin:{contents:`import Usage from ${JSON.stringify(source+'/src/routes/association/course/carnet/detail/sections/Usage.svelte')};window.usage=new Usage({target:document.body,props:{info:{}}});`,resolveDir:ui},bundle:true,write:false,format:'iife',nodePaths:[ui+'/node_modules'],mainFields:['svelte','browser','module','main'],conditions:['svelte','browser'],alias:{components:source+'/src/components',utils:source+'/src/utils',store:source+'/src/store',routes:source+'/src/routes',shim:source+'/src/shim'},plugins:[{name:'isolated-fixture',setup(b){
  b.onResolve({filter:/ApiMiddleware(?:\.js)?$/},()=>({path:'api',namespace:'fixture'}));
  b.onResolve({filter:/utils\/Functions(?:\.js)?$/},()=>({path:'functions',namespace:'fixture'}));
  b.onResolve({filter:/utils\/Permissions(?:\.js)?$/},()=>({path:'permissions',namespace:'fixture'}));
  b.onResolve({filter:/stores\.js$/},()=>({path:'stores',namespace:'fixture'}));
  b.onResolve({filter:/shim\/(tooltip|popover|modal|ui)\.js$/},()=>({path:'shim',namespace:'fixture'}));
  b.onResolve({filter:/svelte-sonner$/},()=>({path:'toast',namespace:'fixture'}));
  b.onResolve({filter:/(?:DetailDrawer|RepeatOnce|DeleteButton|AddModal)\.svelte$/},()=>({path:'unused',namespace:'fixture'}));
  b.onLoad({filter:/.*/,namespace:'fixture'},({path:p})=>({contents:({api:`export async function apiFetch(url,options){const r=await fetch(url,options);return {status:r.status,error:!r.ok,response:await r.json()};}export const replaceUID=(url,uid,placeholder='<uid>')=>url.replace(placeholder,uid);`,functions:`export const waitForElementAndExecute=()=>{};`,permissions:`export const canPerformAction=()=>false;`,stores:`import {writable} from 'svelte/store';export const sessionToken=Object.assign(writable('fixture'),{useLocalStorage(){}});`,shim:`export const initTooltips=()=>{},destroyTooltips=()=>{},initPopovers=()=>{},destroyPopovers=()=>{},hideModal=()=>{};export const UiApp={},UiUtil={isMobileDevice:()=>false};`,toast:`export const toast={success(){},error(){}};`,unused:`export default class Unused {}`})[p],resolveDir:ui}));
 }},{name:'svelte',setup(b){b.onLoad({filter:/\.svelte$/},a=>({contents:compile(fs.readFileSync(a.path,'utf8'),{filename:a.path,generate:'dom',css:'injected'}).js.code,resolveDir:path.dirname(a.path)}));}}]});
 browser=await chromium.launch({headless:true});const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));const requests=[];
 await page.route('https://component.example.test/**',async r=>{const pathname=new URL(r.request().url()).pathname;if(pathname==='/')return r.fulfill({contentType:'text/html',body:'<html><body></body></html>'});requests.push(pathname);await r.fulfill({json:{data:[]}});});
 await page.goto('https://component.example.test/');await page.evaluate(()=>window.__bakney={env:{API:{SUBSCRIPTION:{LIST:'https://component.example.test/subscription/list'},COURSE:{LIST:'https://component.example.test/course/list'},CARNET:{INFO:'https://component.example.test/carnet/<uid>/info'}}}});
 await page.addScriptTag({content:result.outputFiles[0].text});await expect(page.getByRole('heading',{name:'Utilizzo del Carnet'})).toBeVisible();
 await expect.poll(()=>requests.includes('/subscription/list')&&requests.includes('/course/list')).toBe(true);
 assert.deepEqual(requests.filter(p=>p.startsWith('/carnet/')),[],'pending parent info must never request undefined carnet');
 await page.evaluate(()=>window.usage.$set({info:{carnet_id:'5c7c2a61-58e0-4d32-9c84-eed93038981d',subscriptions:[]}}));
 await expect.poll(()=>requests.filter(p=>p.startsWith('/carnet/'))).toEqual(['/carnet/5c7c2a61-58e0-4d32-9c84-eed93038981d/info']);assert.deepEqual(errors,[]);
 await page.evaluate(()=>window.usage.$set({info:{carnet_id:'5c7c2a61-58e0-4d32-9c84-eed93038981d',subscriptions:[]}}));
 await expect.poll(()=>requests.filter(p=>p.startsWith('/carnet/')).length).toBe(2);
 assert.deepEqual(errors,[]);
 console.log('PASS: actual Usage/table waits for parent carnet UUID, fetches once on mount, and refreshes changed assignments.');
} finally {await browser?.close();}
