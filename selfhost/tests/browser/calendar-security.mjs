// Render the real calendar event callback in Chromium, including the HTML-based
// popover boundary, to catch stored markup execution in every calendar view.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {chromium} from '@playwright/test';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const {parse} = await import(pathToFileURL(path.join(root, 'UI/node_modules/svelte/src/compiler/index.js')));
const source = await fs.readFile(path.join(root, 'UI/src/routes/calendar/Calendar.svelte'), 'utf8');
function callback(node) {
    if (!node || typeof node !== 'object') return null;
    if (node.type === 'Property' && node.key?.name === 'eventDidMount') return node.value;
    for (const value of Object.values(node)) {
        const found = callback(value);
        if (found) return found;
    }
    return null;
}
const node = callback(parse(source).instance);
assert.ok(node, 'Calendar mount callback must exist');
const body = source.slice(node.start, node.end);
const browser = await chromium.launch({headless: true});
try {
    const page = await browser.newPage();
    for (const type of ['dayGridMonth', 'timeGridWeek', 'listWeek']) {
        const result = await page.evaluate(({body, type}) => {
            document.body.innerHTML = '<div id="event"><div class="ec-event-title"></div></div>';
            const el = document.getElementById('event');
            const payload = '<img src=x onerror="window.calendarInjected=true"> & <script>bad()</script>';
            const UiApp = {initPopover(element, options) {
                const popover = document.createElement('div');
                popover.className = 'test-popover';
                popover.innerHTML = options.content;
                element.appendChild(popover);
            }};
            new Function('UiApp', `return (${body})`)(UiApp)({el, view: {type},
                event: {title: 'Test', extendedProps: {description: payload}}});
            return {text: el.querySelector('.test-popover, .ec-description').textContent,
                markup: el.querySelectorAll('img, script').length, payload};
        }, {body, type});
        assert.equal(result.text, result.payload);
        assert.equal(result.markup, 0);
        assert.equal(await page.evaluate(() => !!window.calendarInjected), false);
    }
    console.log('PASS: literal descriptions in month popovers, week and list views');
} finally {
    await browser.close();
}
