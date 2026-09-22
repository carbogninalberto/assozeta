// Actual BKNDatatable with a deterministic HTTP adapter; no app server or login required.
// Run: node selfhost/tests/browser/scripts/table-search.mjs
import fs from "node:fs";
import assert from "node:assert/strict";
import { compile } from "../../../../UI/node_modules/svelte/src/compiler/index.js";
import { build } from "../../../../UI/node_modules/esbuild/lib/main.js";
import { chromium } from "@playwright/test";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
const root = fileURLToPath(new URL("../../../../", import.meta.url));
const temp = fs.mkdtempSync(path.join(os.tmpdir(), "assozeta-table-test-"));
const harnessPath = path.join(temp, "App.svelte");

const harness = `<script>import BKNDatatable from '${root}/UI/src/components/tables/BKNDatatable.svelte';let datatable;</script><BKNDatatable bind:datatable columns={[{field:'name',title:'Name'}]} url="http://localhost/api/rows"/><button on:click={()=>datatable.search('old')}>Old</button><button on:click={()=>datatable.search('new')}>New</button><BKNDatatable columns={[{field:"name",title:"Name"}]} localData={[{name:"other instance"}]}/>`;
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
      name: "mocks",
      setup(b) {
        b.onResolve(
          { filter: /^(utils\/ApiMiddleware|shim\/ui.js)$/ },
          (a) => ({ path: a.path, namespace: "mock" })
        );
        b.onLoad({ filter: /.*/, namespace: "mock" }, (a) => ({
          contents: a.path.startsWith("shim")
            ? "export const UiUtil={isMobileDevice:()=>false};"
            : `window.requests=[];export async function apiFetch(url){const q=new URL(url).searchParams.get('query[generalSearch]')||'';window.requests.push(q);await new Promise(r=>setTimeout(r,q==='old'?600:20));return {response:{data:[{name:q||'empty'}]},error:false};}`,
        }));
      },
    },
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
const browser = await chromium.launch();
try {
  const page = await browser.newPage();
  await page.route("http://filter-test.local/**", (route) =>
    route.fulfill({ contentType: "text/html", body: "<!doctype html>" })
  );
  await page.goto("http://filter-test.local/");
  await page.addScriptTag({ content: result.outputFiles[0].text });
  await page.waitForTimeout(100);
  await page.getByRole("button", { name: "Old", exact: true }).click();
  await page.getByRole("button", { name: "New", exact: true }).click();
  await page.waitForTimeout(750);
  assert.match(
    await page.locator(".datatable-body").first().innerText(),
    /new/
  );
  assert.doesNotMatch(
    await page.locator(".datatable-body").first().innerText(),
    /old/
  );
  await page
    .getByRole("textbox", { name: "Cerca nella tabella" })
    .first()
    .fill("pending");
  await page.getByRole("button", { name: "Cancella ricerca" }).first().click();
  await page.waitForTimeout(500);
  assert.equal(
    await page
      .getByRole("textbox", { name: "Cerca nella tabella" })
      .first()
      .inputValue(),
    ""
  );
  assert.equal(
    await page.evaluate(() => window.requests.includes("pending")),
    false
  );
  assert.match(
    await page.locator(".datatable-body").first().innerText(),
    /empty/
  );
  const ids = await page
    .getByRole("textbox", { name: "Cerca nella tabella" })
    .evaluateAll((nodes) => nodes.map((n) => n.id));
  assert.equal(ids.length, 2);
  assert.equal(new Set(ids).size, 2);
  assert.match(
    await page.locator(".datatable-body").last().innerText(),
    /other instance/
  );
  console.log(
    "PASS BKNDatatable: old response ignored, debounce cancelled, two independent table IDs/results"
  );
} finally {
  await browser.close();
  fs.rmSync(temp, { recursive: true, force: true });
}
