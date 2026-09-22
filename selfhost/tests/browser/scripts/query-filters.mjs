// Run: node selfhost/tests/browser/scripts/query-filters.mjs
import fs from "node:fs";
import assert from "node:assert/strict";
import { compile } from "../../../../UI/node_modules/svelte/src/compiler/index.js";
import { build } from "../../../../UI/node_modules/esbuild/lib/main.js";
import { chromium } from "@playwright/test";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
const root = fileURLToPath(new URL("../../../../", import.meta.url));
const temp = fs.mkdtempSync(path.join(os.tmpdir(), "assozeta-query-test-"));
const harnessPath = path.join(temp, "App.svelte");

const harness = `<script>
import QueryFilterTag from '${root}/UI/src/components/filters/QueryFilterTag.svelte';
import MobileFilterSheet from '${root}/UI/src/components/filters/MobileFilterSheet.svelte';
let age={name:'Età',type:'age',active:false,data:{from_age:null,to_age:null}};
let events=0;
let radio={name:'Choice',type:'radio',value:'',active:false,data:{options:[{label:'Zero',value:0},{label:'False',value:false}]}};
let radio2={...radio,name:'Second choice'};
</script>
<div class="datatable-filters"><div class="datatable-filter-controls"><MobileFilterSheet><QueryFilterTag bind:props={age} on:filter-applied={() => events++}/><QueryFilterTag bind:props={radio}/><QueryFilterTag bind:props={radio2}/></MobileFilterSheet></div></div>
<output id="state">{JSON.stringify(age)}</output><output id="events">{events}</output><output id="radio-state">{JSON.stringify(radio)}</output>`;
fs.writeFileSync(harnessPath, harness);
const result = await build({
  stdin: {
    contents: `import App from ${JSON.stringify(
      harnessPath
    )};new App({target:document.body});`,
    resolveDir: root + "/UI",
  },
  bundle: true,
  write: false,
  format: "iife",
  nodePaths: [root + "/UI/node_modules"],
  alias: { components: root + "/UI/src/components" },
  mainFields: ["svelte", "browser", "module", "main"],
  conditions: ["svelte", "browser"],
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
          resolveDir: args.path.substring(0, args.path.lastIndexOf("/")),
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
const browser = await chromium.launch();
try {
  for (const width of [390, 1440]) {
    const page = await browser.newPage({ viewport: { width, height: 850 } });
    await page.setContent(
      `<!doctype html><style>:root{--bg-surface:white}${css}</style>`
    );
    await page.addScriptTag({ content: result.outputFiles[0].text });
    if (width < 768) {
      await page.getByRole("button", { name: "Filtri", exact: true }).click();
      await page.waitForTimeout(400);
    }
    const trigger = page.locator(".query-filter > .dropdown-toggle").first();
    await trigger.click();
    await page.getByLabel("Da anni").fill("0");
    await page.getByLabel("A anni", { exact: true }).fill("12");
    assert.equal(
      JSON.parse(await page.locator("#state").textContent()).data.from_age,
      null
    );
    await page.getByRole("button", { name: "Annulla", exact: true }).click();
    assert.equal(await page.locator("#events").textContent(), "0");
    await trigger.click();
    assert.equal(await page.getByLabel("Da anni").inputValue(), "");
    await page.getByLabel("Da anni").fill("0");
    await page.getByLabel("A anni", { exact: true }).fill("0");
    await page.getByRole("button", { name: "Applica", exact: true }).click();
    assert.equal(await page.locator("#events").textContent(), "1");
    assert.equal(
      JSON.parse(await page.locator("#state").textContent()).data.to_age,
      0
    );
    assert.match(await trigger.textContent(), /0-0/);
    await page.getByRole("button", { name: "Rimuovi filtro Età" }).click();
    assert.equal(await page.locator("#events").textContent(), "2");
    assert.equal(await trigger.getAttribute("aria-expanded"), "false");
    assert.equal(
      JSON.parse(await page.locator("#state").textContent()).data.from_age,
      null
    );
    await trigger.click();
    await page.getByLabel("Da anni").fill("12");
    await page.getByLabel("A anni", { exact: true }).fill("0");
    await page.getByRole("button", { name: "Applica", exact: true }).click();
    assert.equal(await page.getByRole("alert").count(), 1);
    assert.equal(await page.locator("#events").textContent(), "2");
    await page.keyboard.press("Escape");
    assert.equal(await trigger.getAttribute("aria-expanded"), "false");
    await page.getByRole("button", { name: "Choice", exact: true }).click();
    const firstName = await page
      .locator(".query-filter.show")
      .getByLabel("Zero", { exact: true })
      .getAttribute("name");
    await page
      .locator(".query-filter.show")
      .getByLabel("Zero", { exact: true })
      .locator("..")
      .click();
    await page.getByRole("button", { name: "Applica", exact: true }).click();
    assert.equal(
      JSON.parse(await page.locator("#radio-state").textContent()).value,
      0
    );
    await page
      .getByRole("button", { name: "Second choice", exact: true })
      .click();
    assert.notEqual(
      await page
        .locator(".query-filter.show")
        .getByLabel("Zero", { exact: true })
        .getAttribute("name"),
      firstName
    );
    await page.getByRole("button", { name: "Annulla", exact: true }).click();
    await page
      .getByRole("button", { name: "Rimuovi filtro Choice", exact: true })
      .click();
    assert.equal(
      JSON.parse(await page.locator("#radio-state").textContent()).value,
      ""
    );
    console.log(
      "PASS drafts/remove/zero/validation/Escape and isolated radio groups",
      width
    );
    await page.close();
  }
} finally {
  await browser.close();
  fs.rmSync(temp, { recursive: true, force: true });
}
