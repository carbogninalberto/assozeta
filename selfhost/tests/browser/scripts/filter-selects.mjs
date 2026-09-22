// Run after installing UI and selfhost/tests/browser dependencies and Playwright Chromium:
// node selfhost/tests/browser/scripts/filter-selects.mjs
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium, expect } from "@playwright/test";
import { compile } from "../../../../UI/node_modules/svelte/src/compiler/index.js";
import { build } from "../../../../UI/node_modules/esbuild/lib/main.js";

const root = fileURLToPath(new URL("../../../../", import.meta.url));
const temp = fs.mkdtempSync(
  path.join(os.tmpdir(), "assozeta-filter-selects-")
);
const app = path.join(temp, "App.svelte");
fs.writeFileSync(
  app,
  `<script>
import MobileFilterSheet from '${root}/UI/src/components/filters/MobileFilterSheet.svelte';
import FilterSelect from '${root}/UI/src/components/filters/FilterSelect.svelte';
import SmartSelect from '${root}/UI/src/components/formBuilder/preview-blocks/smart-select-input.svelte';
let personaMethod='';
let subject = ''; let paid = ''; let other = ''; let changes = 0;
let options = [{value:'',label:'Attività'},{value:0,label:'Altro'},{value:1,label:'Iscrizione'},{value:2,label:'Corso'},{value:3,label:'Giroconto'}];
function reset() {subject='';paid='';}
</script>
<div class="datatable-filters"><MobileFilterSheet onReset={reset}>
<FilterSelect label="Attività" bind:value={subject} {options} on:change={() => changes++}/>
<FilterSelect label="Stato" bind:value={paid} options={[{value:'',label:'Tutti'},{value:false,label:'In attesa'},{value:true,label:'Pagato'}]}/>
<FilterSelect label="Seconda attività" bind:value={other} {options}/>
<SmartSelect customClasses="m-0 p-0 filter-select min-w-7" editable={false} bind:value={personaMethod}
selectClasses={personaMethod ? 'query-filter-select border border-secondary border-2 bg-light' : 'query-filter-select border border-secondary border-dashed bg-white'}
props={{placeholder:'Metodo persona',value:personaMethod,clearable:false,showChevron:true,options:[{value:'',label:'Metodo persona'},{value:'transfer',label:'Bonifico Bancario'},{value:'sepa',label:'Bonifico SEPA'},{value:'cash',label:'Contanti'}]}}/>
</MobileFilterSheet></div>
<button on:click={() => options=[...options.map(option=>({...option})),{value:'literal',label:'<b>Corso & attività</b>'}]}>Reload options</button>
<output id="state">{JSON.stringify({subject,paid,other,changes})}</output>`
);
let browser;
try {
  const result = await build({
    stdin: {
      contents: `import App from ${JSON.stringify(
        app
      )};new App({target:document.body});`,
      resolveDir: root + "/UI",
    },
    bundle: true,
    write: false,
    format: "iife",
    nodePaths: [root + "/UI/node_modules"],
    mainFields: ["svelte", "browser", "module", "main"],
    conditions: ["svelte", "browser"],
    alias: { components: root + "/UI/src/components" },
    plugins: [
      {
        name: "svelte",
        setup(b) {
          b.onLoad({ filter: /\.svelte$/ }, (args) => ({
            contents: compile(fs.readFileSync(args.path, "utf8"), {
              filename: args.path,
              generate: "dom",
              css: "injected",
            }).js.code,
            resolveDir: path.dirname(args.path),
          }));
        },
      },
    ],
  });
  const css = [
    "static/css/bootstrap.min.css",
    "static/css/app-bundle.css",
    "global.css",
  ]
    .map((p) => fs.readFileSync(root + "/UI/public/" + p, "utf8"))
    .join("\n");
  browser = await chromium.launch();
  for (const width of [390, 667, 768, 1440]) {
    const page = await browser.newPage({viewport:{width,height:width===667?375:900}});
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.setContent(`<style>${css}</style>`);
    await page.addScriptTag({content:result.outputFiles[0].text});
    const state=async()=>JSON.parse(await page.locator('#state').textContent());
    const selected=label=>page.getByLabel(label,{exact:true}).locator('xpath=ancestor::div[contains(@class,"svelte-select")][1]').locator('.selected-item');
    const choose=async(label,option)=>{
      await page.getByLabel(label,{exact:true}).click();
      const optionLabel=page.locator('.svelte-select-list').getByText(option,{exact:true});
      const alignment=await optionLabel.evaluate(label=>{
        const item=label.closest('.item').getBoundingClientRect();
        const content=label.getBoundingClientRect();
        return Math.abs((content.top+content.bottom)/2-(item.top+item.bottom)/2);
      });
      assert.ok(alignment<=1,`option vertically centered: ${option} (${alignment}px)`);
      await optionLabel.click();
      assert.equal((await selected(label).textContent()).trim(),option);
    };
    const open=async()=>{if(width<768)await page.getByRole('button',{name:'Filtri',exact:true}).click();};
    const close=async()=>{if(width<768)await page.getByRole('button',{name:'Mostra risultati'}).click();};
    await open();
    assert.equal(await page.locator('select').count(),0);
    for(const option of ['Bonifico Bancario','Bonifico SEPA','Contanti','Metodo persona']) {
      await choose('Metodo persona',option);
      await expect.poll(async()=>selected('Metodo persona').evaluate(el=>{
        const range=document.createRange();range.selectNodeContents(el);
        const css=getComputedStyle(el);
        return range.getBoundingClientRect().width <= el.clientWidth-parseFloat(css.paddingLeft)-parseFloat(css.paddingRight)+1;
      })).toBe(true);
    }
    let changes=0;
    for(const [value,label] of [[0,'Altro'],[1,'Iscrizione'],[2,'Corso'],[3,'Giroconto'],['','Attività']]) {
      await choose('Attività',label);
      await expect.poll(state).toEqual({subject:value,paid:'',other:'',changes:++changes});
    }
    await choose('Attività','Altro');changes++;
    await choose('Stato','In attesa');
    await expect.poll(async()=>(await state()).paid).toBe(false);
    await close();
    await page.getByRole('button',{name:'Reload options'}).click();
    await open();
    assert.equal((await selected('Attività').textContent()).trim(),'Altro');
    assert.equal((await selected('Stato').textContent()).trim(),'In attesa');
    const input=page.getByLabel('Seconda attività',{exact:true});
    const control=input.locator('xpath=ancestor::div[contains(@class,"svelte-select")][1]');
    await input.focus();
    assert.equal(await input.evaluate(el=>getComputedStyle(el).outlineStyle),'none');
    assert.notEqual(await control.evaluate(el=>getComputedStyle(el).outlineStyle),'none');
    await choose('Seconda attività','Corso');
    if(width>=768){
      const measure=input.locator('xpath=ancestor::div[contains(@class,"auto-filter-select")][1]').locator('.filter-label-measure');
      await expect(measure).toHaveText('Corso');
      await expect.poll(async()=>control.evaluate(el=>Math.abs(el.getBoundingClientRect().width-parseFloat(getComputedStyle(el.closest('.auto-filter-select')).getPropertyValue('--filter-control-width'))))).toBeLessThan(1);
    }
    const shortWidth=(await control.boundingBox()).width;
    await input.click();
    const menu=await page.locator('.svelte-select-list').boundingBox();
    assert.ok(menu.x>=0 && menu.x+menu.width<=width+1,'menu stays in viewport');
    if(width>=768)assert.ok(menu.width>shortWidth,'menu expands for longer options');
    await page.keyboard.press('Escape');
    await choose('Seconda attività','<b>Corso & attività</b>');
    if(width>=768)await expect.poll(async()=>(await control.boundingBox()).width).toBeGreaterThan(shortWidth);

    assert.equal(await selected('Seconda attività').locator('b').count(),0);
    await choose('Seconda attività','Corso');
    if(width>=768)await expect.poll(async()=>(await control.boundingBox()).width).toBe(shortWidth);
    const ids=await page.locator('.filter-select-control input[type=text]').evaluateAll(inputs=>inputs.map(e=>e.id));
    assert.equal(new Set(ids).size,ids.length);
    const reset=page.getByRole('button',{name:'Ripristina filtri',exact:true}).filter({visible:true});
    const colors=await reset.evaluate(el=>({text:getComputedStyle(el).color,background:getComputedStyle(el).backgroundColor}));
    const luminance=color=>color.match(/[\d.]+/g).slice(0,3).map(Number).map(c=>c/255).map(c=>c<=0.04045?c/12.92:((c+0.055)/1.055)**2.4).reduce((sum,c,i)=>sum+c*[0.2126,0.7152,0.0722][i],0);
    const textLight=luminance(colors.text),backgroundLight=luminance(colors.background);
    assert.ok((Math.max(textLight,backgroundLight)+0.05)/(Math.min(textLight,backgroundLight)+0.05)>=4.5,'reset contrast');
    await reset.click();
    await expect.poll(state).toEqual({subject:'',paid:'',other:2,changes});
    assert.equal((await selected('Attività').textContent()).trim(),'Attività');
    assert.equal((await selected('Stato').textContent()).trim(),'Tutti');
    assert.equal((await selected('Seconda attività').textContent()).trim(),'Corso');
    assert.deepEqual(errors,[]);
    console.log('PASS filter dropdowns',width,'typed zero/false, labels, rebuilt options, reset, independent instances');
    await page.close();
  }
} finally {
  await browser?.close();
  fs.rmSync(temp, {recursive:true,force:true});
}
