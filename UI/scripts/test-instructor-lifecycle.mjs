// Exercise the actual Instructor, table and portal editor lifecycle under delayed HTTP.
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
 const result=await build({stdin:{contents:`import Instructor from ${JSON.stringify(source+'/src/routes/association/course/instructor/info/Instructor.svelte')};import moment from 'moment';window.moment=moment;window.instructor=new Instructor({target:document.body,props:{params:{id:'5c7c2a61-58e0-4d32-9c84-eed93038981d'}}});`,resolveDir:ui},bundle:true,write:false,format:'iife',nodePaths:[ui+'/node_modules'],mainFields:['svelte','browser','module','main'],conditions:['svelte','browser'],alias:{components:source+'/src/components',utils:source+'/src/utils',store:source+'/src/store',routes:source+'/src/routes',shim:source+'/src/shim'},plugins:[{name:'isolated-fixture',setup(b){
  b.onResolve({filter:/ApiMiddleware(?:\.js)?$/},()=>({path:'api',namespace:'fixture'}));
  b.onResolve({filter:/utils\/Functions(?:\.js)?$/},()=>({path:'functions',namespace:'fixture'}));
  b.onResolve({filter:/utils\/Permissions(?:\.js)?$/},()=>({path:'permissions',namespace:'fixture'}));
  b.onResolve({filter:/stores(?:\.js)?$/},()=>({path:'stores',namespace:'fixture'}));
  b.onResolve({filter:/shim\/(tooltip|popover|ui)\.js$/},()=>({path:'shim',namespace:'fixture'}));
  b.onResolve({filter:/loadingStore\.js$/},()=>({path:'loading',namespace:'fixture'}));
  b.onResolve({filter:/svelte-sonner$/},()=>({path:'toast',namespace:'fixture'}));
  b.onResolve({filter:/(?:LessonsHoursCard|DocumentPreviewModal|DocumentSignatureModal|DocumentButton|BackButton)\.svelte$/},()=>({path:'unused',namespace:'fixture'}));
  b.onLoad({filter:/.*/,namespace:'fixture'},({path:p})=>({contents:({api:`export async function apiFetch(url,options){const r=await fetch(url,options);return {status:r.status,error:!r.ok,response:await r.json()};}export const replaceUID=(url,uid,placeholder='<uid>')=>url.replace(placeholder,uid);`,functions:`export const getDataFromForm=e=>Object.fromEntries(new FormData(e.target));export function waitForElementAndExecute(selector,callback){if(document.querySelector(selector))return callback();const observer=new MutationObserver(()=>{if(document.querySelector(selector)){observer.disconnect();callback();}});observer.observe(document.body,{subtree:true,childList:true});}`,permissions:`export const canPerformAction=()=>window.allowChanges;`,stores:`import {writable} from 'svelte/store';const store=value=>Object.assign(writable(value),{useLocalStorage(){}});export const sessionToken=store('fixture'),userData=store({});`,shim:`export const initTooltips=()=>{},destroyTooltips=()=>{},initPopovers=()=>{},destroyPopovers=()=>{},hideModal=()=>{};export const UiApp={blockPage(){},unblockPage(){}},UiUtil={isMobileDevice:()=>false};`,loading:`export const blockPage=()=>{},unblockPage=()=>{};`,toast:`export const toast={success(){},error(){}};`,unused:compile('<div></div>',{generate:'dom'}).js.code})[p],resolveDir:ui}));
 }},{name:'svelte',setup(b){b.onLoad({filter:/\.svelte$/},a=>({contents:compile(fs.readFileSync(a.path,'utf8'),{filename:a.path,generate:'dom',css:'injected'}).js.code,resolveDir:path.dirname(a.path)}));}}]});
 browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1920,height:1080}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const infoRequests=[],patches=[];let holdInfo=true;
 const instructorId='5c7c2a61-58e0-4d32-9c84-eed93038981d',hoursId='d9423dc8-5d01-4475-a95d-08d4d8e508dd';
 let row={instructor_hours_id:hoursId,instructor:instructorId,date:'2026-09-30',hours:'2.00',hourly_billing:'15.00',amount:'30.00',paid:false,notes:'Original hours',compensation_type:'hourly',courses:[],payment:null,document:null,calculation_data:[]};
 await page.route('https://component.example.test/**',async route=>{
  const url=new URL(route.request().url());
  if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:'<html><body></body></html>'});
  if(url.pathname==='/instructor/'+instructorId+'/info'){if(holdInfo){infoRequests.push(route);return;}return route.fulfill({json:{data:{first_name:'Elena',last_name:'Moretti',default_hourly_billing:15,default_percentage_billing:20},stats:{}}});}
  if(route.request().method()==='PATCH'){const payload=route.request().postDataJSON();patches.push(payload);row={...row,...payload,hours:'3.00',hourly_billing:'18.00',amount:'54.00'};return route.fulfill({status:200,json:{}});}
  if(url.pathname==='/hours/'+instructorId+'/list')return route.fulfill({json:{data:[{...row}]}});
  return route.fulfill({json:{data:[]}});
 });
 await page.goto('https://component.example.test/');
 const css=['static/css/bootstrap.min.css','static/css/app-bundle.css','global.css'].map(f=>fs.readFileSync(ui+'/public/'+f,'utf8')).join('\n');await page.addStyleTag({content:css});
 await page.evaluate(()=>{window.allowChanges=true;window.FormValidation={formValidation:()=>({destroy(){},validate:async()=>'Valid'}),plugins:{Trigger:class{},Bootstrap:class{},Excluded:class{}}};window.swal={fire:async()=>({})};window.__bakney={env:{API:{INSTRUCTOR:{INFO:'https://component.example.test/instructor/<uid>/info',LESSONS_HOURS:'https://component.example.test/lessons/<uid>',HOURS:{LIST:'https://component.example.test/hours/<uid>/list',UPDATE:'https://component.example.test/hours/<uid>/<hour_uid>/update',ADD:'https://component.example.test/hours/<uid>/add'}},COURSE:{LIST:'https://component.example.test/course/list'},DOCUMENT:{RETRIEVE:'https://component.example.test/document/retrieve'}}}};});
 await page.addScriptTag({content:result.outputFiles[0].text});
 const add=page.getByRole('button',{name:'Aggiungi',exact:true});
 await expect.poll(()=>infoRequests.length).toBe(1);if(process.argv[3]!=='skip-readiness'){await expect(add).toBeDisabled();await expect(page.locator('#modal-'+instructorId)).toHaveCount(0);}
 holdInfo=false;await infoRequests.shift().fulfill({json:{data:{first_name:'Elena',last_name:'Moretti',default_hourly_billing:15,default_percentage_billing:20},stats:{}}});
 await expect(add).toBeEnabled();await add.click();await expect(page.locator('#modal-'+instructorId)).toBeVisible();
 await page.locator('#modal-'+instructorId).getByRole('button',{name:'Chiudi',exact:true}).click();await expect(page.locator('#modal-'+instructorId)).not.toBeVisible();
 const edit=()=>page.locator('#action-col-'+hoursId+' button[title="Modifica"]');await expect(edit()).toHaveCount(1);await edit().click();
 const modal=page.locator('#modal-'+hoursId);await expect(modal).toBeVisible();await modal.locator('[name="hours"]').fill('3');await modal.locator('[name="hourly_billing"]').fill('18');await modal.locator('[name="notes"]').fill('Saved hours');await expect(modal.locator('[name="amount"]')).toHaveValue('54');
 await modal.getByRole('button',{name:'Salva',exact:true}).click();await expect.poll(()=>patches.length).toBe(1);assert.equal(patches[0].notes,'Saved hours');assert.equal(Number(patches[0].hours),3);assert.equal(Number(patches[0].hourly_billing),18);
 await expect(modal).toHaveCount(1);await expect(modal).not.toBeVisible();await edit().click();await expect(modal).toBeVisible();await expect(modal.locator('[name="hours"]')).toHaveValue('3.00');await expect(modal.locator('[name="hourly_billing"]')).toHaveValue('18.00');await expect(modal.locator('[name="notes"]')).toHaveValue('Saved hours');
 await modal.getByRole('button',{name:'Chiudi',exact:true}).click();await expect(modal).not.toBeVisible();
 await page.evaluate(()=>window.instructor.$destroy());await expect(page.locator('[id^="modal-"]')).toHaveCount(0);
 await page.evaluate(()=>window.allowChanges=false);await page.addScriptTag({content:result.outputFiles[0].text});await expect(page.locator('#modal-'+instructorId)).toHaveCount(1);await expect(add).toBeDisabled();await expect(edit()).toBeDisabled();assert.deepEqual(errors,[]);
 console.log('PASS: actual Instructor waits for loaded create modal, replaces portal editor on saved-row reload, preserves54/hours/rate/notes, destroys all editors on disposal, and keeps reader controls disabled.');
} finally {await browser?.close();}
