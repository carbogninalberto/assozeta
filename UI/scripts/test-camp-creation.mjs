// Compile the actual camp modal; intercepted HTTP stays inside this browser fixture.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium, expect} from '../../selfhost/tests/browser/node_modules/@playwright/test/index.mjs';
import {compile} from '../node_modules/svelte/src/compiler/index.js';
import {build} from '../node_modules/esbuild/lib/main.js';
const ui=path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const source=process.argv[2] ? path.resolve(process.argv[2]) : ui;
const temp=fs.mkdtempSync(path.join(os.tmpdir(),'assozeta-camp-lifecycle-'));
let browser;
try {
 const bundled=await build({stdin:{contents:`import Camp from ${JSON.stringify(source+'/src/routes/association/course/campsAndRetreats/modals/AddModal.svelte')};window.openCamp=()=>new Camp({target:document.body,props:{show:true,datatableHandle:{reload(){window.reloads++}}}});window.openCamp();`,resolveDir:ui},bundle:true,write:false,format:'iife',nodePaths:[ui+'/node_modules'],mainFields:['svelte','browser','module','main'],conditions:['svelte','browser'],alias:{components:source+'/src/components',utils:source+'/src/utils',store:source+'/src/store'},plugins:[{name:'isolated-fixture',setup(b){
  b.onResolve({filter:/ApiMiddleware\.js$/},()=>({path:'api',namespace:'fixture'}));
  b.onResolve({filter:/utils\/Functions\.js$/},()=>({path:'form',namespace:'fixture'}));
  b.onResolve({filter:/loadingStore\.js$/},()=>({path:'loading',namespace:'fixture'}));
  b.onResolve({filter:/svelte-sonner$/},()=>({path:'toast',namespace:'fixture'}));
  b.onResolve({filter:/preview-blocks\/index\.js$/},()=>({path:'fields',namespace:'fixture'}));
  b.onLoad({filter:/.*/,namespace:'fixture'},({path:p})=>({contents:({api:`export async function apiFetch(url,options){const r=await fetch(url,options);return {status:r.status,error:!r.ok,response:await r.json()};}`,form:`export const getDataFromForm=e=>Object.fromEntries(new FormData(e.target));`,loading:`export const blockPage=()=>{};export const unblockPage=()=>{};`,toast:`export const toast={success:m=>window.toasts.push(m)};`,fields:`export {default as TextInput} from ${JSON.stringify(source+'/src/components/formBuilder/preview-blocks/text-input.svelte')};export {default as TextArea} from ${JSON.stringify(source+'/src/components/formBuilder/preview-blocks/textarea-input.svelte')};`})[p],resolveDir:ui}));
 }},{name:'svelte',setup(b){b.onLoad({filter:/\.svelte$/},a=>({contents:compile(fs.readFileSync(a.path,'utf8'),{filename:a.path,generate:'dom',css:'injected'}).js.code,resolveDir:path.dirname(a.path)}));}}]});
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const pending=[];await page.route('https://component.example.test/camps/add',route=>pending.push(route));
 await page.setContent('<html><body></body></html>');
 await page.evaluate(()=>{window.reloads=0;window.toasts=[];window.alerts=[];window.swal={fire:data=>{window.alerts.push(data);return Promise.resolve({});}};window.__bakney={env:{API:{CAMPS_AND_RETREATS:{ADD:'https://component.example.test/camps/add'}}}};window.FormValidation={formValidation:form=>({destroy(){},async validate(){return form.elements.title.value&&form.elements.description.value?'Valid':'Invalid';}}),plugins:{Trigger:class{},Bootstrap:class{}}};});
 await page.addScriptTag({content:bundled.outputFiles[0].text});
 const form=page.locator('#camps_and_retreats_form');
 const fill=async(title,description)=>{await form.locator('[name="title"]').fill(title);await form.locator('[name="description"]').fill(description);};
 await fill('Ritiro Aurora 2026','Due giornate Aurora.');
 await form.getByRole('button',{name:'Crea',exact:true}).click();
 await expect.poll(()=>pending.length).toBe(1);
 const success=pending.shift();assert.deepEqual(success.request().postDataJSON(),{title:'Ritiro Aurora 2026',description:'Due giornate Aurora.'});
 await expect(form).toBeVisible();await expect(form.locator('[name="title"]')).toHaveValue('Ritiro Aurora 2026');
 assert.equal(await page.evaluate(()=>window.reloads),0);
 await success.fulfill({status:201,json:{camp_and_retreat:{camps_and_retreats_id:'f1977011-9af1-47db-b93a-10d0fc1b446a'}}});
 await expect(page).toHaveURL(/#\/course\/camps-and-retreats\/overview\/f1977011-9af1-47db-b93a-10d0fc1b446a$/);
 await expect(form).toHaveCount(0);assert.equal(await page.evaluate(()=>window.reloads),1);assert.equal(await page.evaluate(()=>window.toasts.length),1);
 await page.evaluate(()=>window.openCamp());await fill('Preserved title','Preserved description');
 await form.getByRole('button',{name:'Crea',exact:true}).click();await expect.poll(()=>pending.length).toBe(1);
 await pending.shift().fulfill({status:400,json:{msg:'Invalid camp'}});
 await expect.poll(()=>page.evaluate(()=>window.alerts.length)).toBe(1);
 await expect(form).toBeVisible();await expect(form.locator('[name="title"]')).toHaveValue('Preserved title');await expect(form.locator('[name="description"]')).toHaveValue('Preserved description');
 assert.equal(await page.evaluate(()=>window.reloads),1);assert.equal(await page.evaluate(()=>window.toasts.length),1);assert.deepEqual(errors,[]);
 console.log('PASS: actual camp modal keeps form through delayed response, navigates only on201, and preserves inputs on400.');
} finally {await browser?.close();fs.rmSync(temp,{recursive:true,force:true});}
