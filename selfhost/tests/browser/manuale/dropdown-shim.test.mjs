// Browser regression for the real dropdown implementation; no manual evidence.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {chromium} from '@playwright/test';

const source = new URL('../../../../UI/src/shim/dropdown.js', import.meta.url);
test('overflow dropdown stays usable through layout and internal list scrolling', async () => {
    const browser = await chromium.launch();
    try {
        const page = await browser.newPage({viewport: {width: 800, height: 600}});
        await page.setContent(`<style>
            #panel{overflow:auto;height:180px;width:400px;margin-top:80px}
            .dropdown-menu{display:none;background:white;width:240px}
            .dropdown-menu.show{display:block}
            #choices{height:80px;overflow:auto}
            #spacer{height:600px}
        </style><div id="panel"><button data-toggle="dropdown">Assegna Tag</button>
          <div class="dropdown-menu"><input placeholder="Nome tag...">
            <div id="choices"><label><input type="checkbox">Principianti</label><div id="spacer"></div></div>
          </div><div style="height:400px"></div></div>`);
        await page.addScriptTag({type: 'module', content: fs.readFileSync(source, 'utf8')});
        await page.getByRole('button', {name: 'Assegna Tag'}).click();
        await page.waitForFunction(() => document.querySelector('.dropdown-menu').classList.contains('show'));
        const initial = await page.locator('.dropdown-menu').boundingBox();
        await page.locator('#panel').evaluate(el => { el.scrollTop = 25; });
        await page.waitForFunction(() => document.querySelector('.dropdown-menu').style.top === Math.round(document.querySelector('button').getBoundingClientRect().bottom + 2) + 'px');
        assert.equal(await page.locator('.dropdown-menu').isVisible(), true);
        const moved = await page.locator('.dropdown-menu').boundingBox();
        assert.ok(moved.y < initial.y, 'menu follows its anchor during parent scrolling');
        await page.getByPlaceholder('Nome tag...').fill('Principianti');
        await page.getByRole('checkbox').check();
        await page.locator('#choices').evaluate(el => {el.scrollTop = 40; el.dispatchEvent(new Event('scroll'));});
        assert.equal(await page.locator('.dropdown-menu').isVisible(), true);
        assert.equal(await page.getByRole('checkbox').isChecked(), true);
        await page.setViewportSize({width: 700, height: 500});
        assert.equal(await page.locator('.dropdown-menu').isVisible(), true);
        await page.keyboard.press('Escape');
        assert.equal(await page.locator('.dropdown-menu').isVisible(), false);
        assert.equal(await page.locator('button').getAttribute('aria-expanded'), 'false');
        await page.getByRole('button', {name: 'Assegna Tag'}).click();
        await page.mouse.click(680, 480);
        assert.equal(await page.locator('.dropdown-menu').isVisible(), false);
    } finally { await browser.close(); }
});
