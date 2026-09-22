// Run after installing UI and selfhost/tests/browser dependencies and Playwright Chromium:
// node selfhost/tests/browser/scripts/filter-overlays.mjs
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";
import { compile } from "../../../../UI/node_modules/svelte/src/compiler/index.js";
import { build } from "../../../../UI/node_modules/esbuild/lib/main.js";

const root = fileURLToPath(new URL("../../../../", import.meta.url));
const temp = fs.mkdtempSync(
  path.join(os.tmpdir(), "assozeta-filter-overlays-")
);
const app = path.join(temp, "App.svelte");
fs.writeFileSync(
  app,
  `<script>
import BasicDrawer from '${root}/UI/src/components/drawer/basic-drawer.svelte';
import MobileFilterSheet from '${root}/UI/src/components/filters/MobileFilterSheet.svelte';
import DateRangePicker from '${root}/UI/src/components/inputs/DateRangePicker.svelte';
import SmartSelect from '${root}/UI/src/components/formBuilder/preview-blocks/smart-select-input.svelte';
let open = false; let status = ''; let start = ''; let end = '';let choice='';
</script>
<button on:click={() => open = true}>Open detail</button>
<BasicDrawer bind:isOpen={open} title="Member detail">
<div slot="content" class="datatable-filters"><MobileFilterSheet>
<select aria-label="Status" bind:value={status}><option value="">All</option><option value="paid">Paid</option></select>
<SmartSelect bind:value={choice} editable={false} props={{id:"long-options",placeholder:"Options",value:choice,options:Array.from({length:50},(_,i)=>({label:"Option "+i,value:i}))}}/><DateRangePicker bind:startValue={start} bind:endValue={end}/>
</MobileFilterSheet></div>
</BasicDrawer>`
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
  for (const viewport of [
    { width: 390, height: 844 },
    { width: 667, height: 375 },
  ]) {
    const page = await browser.newPage({ viewport });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.setContent(`<style>${css}</style>`);
    await page.addScriptTag({ content: result.outputFiles[0].text });
    async function checkRangeFocus() {
      // Wait for the drawer entrance focus trap before moving focus between controls.
      await page.waitForTimeout(550);
      const trigger=page.locator('.drp-trigger');
      const input=page.getByRole('textbox',{name:'Periodo',exact:true});
      const button=page.getByRole('button',{name:'Seleziona periodo',exact:true});
      for(const control of [input,button]) {
        await control.focus();
        assert.equal(await trigger.evaluate(el=>getComputedStyle(el).outlineStyle),'solid');
        assert.equal(await trigger.evaluate(el=>getComputedStyle(el).outlineWidth),'2px');
        for(const child of [input,button]) {
          assert.equal(await child.evaluate(el=>getComputedStyle(el).outlineStyle),'none');
          assert.equal(await child.evaluate(el=>getComputedStyle(el).boxShadow),'none');
        }
      }
    }

    await page.getByRole("button", { name: "Open detail" }).click();
    await page.getByRole("button", { name: "Filtri", exact: true }).click();
    await page.getByLabel("Status").selectOption("paid");
    await page.getByLabel("Options", { exact: true }).click();
    await page.waitForTimeout(150);
    const menu = await page.locator(".svelte-select-list").boundingBox();
    const fields = await page.locator(".filter-sheet-fields").boundingBox();
    assert.ok(
      menu.y >= fields.y - 1 &&
        menu.y + menu.height <= fields.y + fields.height + 1,
      JSON.stringify({ menu, fields })
    );
    await page.getByText("Option 49", { exact: true }).click();
    await page.getByLabel('Options',{exact:true}).click();
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('.mobile-filter-drawer').count(),1);
    await page.keyboard.press('Escape');
    await page.waitForTimeout(550);
    assert.equal(await page.locator('.mobile-filter-drawer').count(),0);
    await page.getByRole('button',{name:'Filtri',exact:true}).click();


    await checkRangeFocus();
    await page
      .getByRole("textbox", { name: "Periodo", exact: true })
      .press("Enter");
    await page.waitForTimeout(550);
    assert.equal(await page.locator(".drawer").count(), 3);
    assert.equal(await page.getByRole('dialog',{name:'Seleziona periodo',exact:true}).count(),1);
    await page.keyboard.press("Escape");
    await page.waitForTimeout(550);
    assert.equal(
      await page.locator(".drawer").count(),
      2,
      "Escape must close only the date picker"
    );
    assert.equal(
      await page.evaluate(() => document.body.style.overflow),
      "hidden"
    );
    await page.keyboard.press("Escape");
    await page.waitForTimeout(550);
    assert.equal(
      await page.locator(".drawer").count(),
      1,
      "Escape must preserve member detail"
    );
    assert.equal(await page.getByRole('button',{name:'Filtri',exact:true}).evaluate(el=>getComputedStyle(el).outlineStyle),'solid');
    assert.equal(
      await page.evaluate(() => document.body.style.overflow),
      "hidden"
    );
    await page.getByRole("button", { name: "Filtri", exact: true }).click();
    assert.equal(await page.getByLabel("Status").inputValue(), "paid");
    await page.setViewportSize({ width: 1024, height: viewport.height });
    await page.waitForTimeout(550);
    assert.equal(await page.locator(".drawer").count(), 1);
    assert.equal(await page.getByLabel("Status").inputValue(), "paid");
    await checkRangeFocus();
    await page.setViewportSize(viewport);
    await page.getByRole("button", { name: "Filtri", exact: true }).click();
    await page.waitForTimeout(550);
    if (viewport.width === 390) {
      await page.evaluate(() => {
        window.savedVisualViewport = window.visualViewport;
        const fake = new EventTarget();
        Object.assign(fake, { height: 300, offsetTop: 0 });
        Object.defineProperty(window, "visualViewport", {
          value: fake,
          configurable: true,
        });
        window.dispatchEvent(new Event("resize"));
      });
      await page.waitForTimeout(150);
      const footer = await page
        .getByRole("button", { name: "Mostra risultati" })
        .boundingBox();
      assert.ok(
        footer.y + footer.height <= 301,
        JSON.stringify({
          footer,
          drawer: await page
            .locator(".mobile-filter-drawer")
            .evaluate((e) => ({
              style: e.getAttribute("style"),
              rect: e.getBoundingClientRect().toJSON(),
              bottom: getComputedStyle(e).bottom,
              transform: getComputedStyle(e).transform,
            })),
        })
      );
      await page.evaluate(() => {
        Object.defineProperty(window, "visualViewport", {
          value: window.savedVisualViewport,
          configurable: true,
        });
        window.dispatchEvent(new Event("resize"));
      });
    }

    await page.getByRole("button", { name: "Mostra risultati" }).click();
    await page.waitForTimeout(550);
    await page.keyboard.press("Escape");
    await page.waitForTimeout(550);
    assert.equal(await page.locator(".drawer").count(), 0);
    assert.notEqual(
      await page.evaluate(() => document.body.style.overflow),
      "hidden"
    );
    assert.equal(
      await page
        .getByRole("button", { name: "Open detail" })
        .evaluate((el) => el === document.activeElement),
      true
    );
    assert.deepEqual(errors, []);
    console.log(
      "PASS nested overlays, long menu bounds, resize, keyboard height, scroll locks, focus and values",
      viewport
    );
    await page.close();
  }
} finally {
  await browser?.close();
  fs.rmSync(temp, { recursive: true, force: true });
}
