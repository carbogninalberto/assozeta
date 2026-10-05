import {chromium, expect} from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

const run = path.resolve(process.env.ASSOZETA_MANUAL_RUN);
const state = JSON.parse(fs.readFileSync(path.join(run, 'run.json')));
const sha = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const escape = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
const renderName = (page, section, checkpoint = 'heading') =>
    crypto.createHash('sha256').update(JSON.stringify([page, section, checkpoint])).digest('hex').slice(0, 24) + '.png';
const report = {status: 'running', cli: 'mint@4.2.955', preview_url: state.manual_preview_url, checks: []};
const browser = await chromium.launch();
const page = await browser.newPage({viewport: {width: 1920, height: 1080}, deviceScaleFactor: 1});
try {
    const manifest = JSON.parse(fs.readFileSync(path.join(run, 'manifest.json')));
    report.pages = [];
    fs.mkdirSync(path.join(run, 'renders'), {recursive: true});
    for (const entry of manifest.pages) {
        const sections = entry.sections.filter(section => section.status === 'verified');
        if (!sections.length) continue;
        const relative = entry.path.replace(/\.mdx$/, '');
        await page.goto(state.manual_preview_url + '/' + relative, {timeout: 90000});
        const result = {page: relative, anchors: [], images: [], layouts: [], source_sha256:
            crypto.createHash('sha256').update(fs.readFileSync(path.join(state.manual, entry.path))).digest('hex')};
        for (const section of sections) {
            // Mintlify applies smart punctuation before deriving IDs. Keep our
            // stable section identity, but bind citations to the actual heading.
            const matches = section.implicit_heading ? ['page-title'] : await page.locator('h1[id],h2[id],h3[id],h4[id],h5[id],h6[id]').evaluateAll((headings, title) => {
                const normalize = text => text.normalize('NFKC').replace(/[\u200b-\u200d\ufeff]/g, '')
                    .replace(/[‘’]/g, "'").replace(/[“”]/g, '"').replace(/\s+/g, ' ').trim();
                return headings.filter(node => node.id !== 'page-title' && !node.id.startsWith('_R_') &&
                    normalize(node.textContent) === normalize(title)).map(node => node.id);
            }, section.title || section.id);
            expect(matches, 'Unique rendered heading for ' + relative + '#' + section.id).toHaveLength(1);
            section.citation_anchor = matches[0];
            const anchor = page.locator('[id=' + JSON.stringify(section.citation_anchor) + ']').first();
            await anchor.scrollIntoViewIfNeeded();
            await expect(anchor).toBeVisible();
            result.anchors.push(section.id);
            const headingRender = renderName(relative, section.id);
            await page.screenshot({path: path.join(run, 'renders', headingRender), fullPage: false, animations: 'disabled'});
            result.layouts.push({section: section.id, kind: 'heading', path: 'renders/' + headingRender,
                width: 1920, height: 1080, sha256: sha(path.join(run, 'renders', headingRender))});
            for (const capture of section.screenshots.filter(image =>
                !section.displayed_screenshots || section.displayed_screenshots.includes(image.path))) {
                const images = page.locator('img[src*="' + capture.path + '"]');
                await expect(images.first()).toBeAttached();
                await images.first().scrollIntoViewIfNeeded();
                await expect.poll(() => images.evaluateAll(items => items.every(image => image.complete && image.naturalWidth > 0))).toBeTruthy();
                result.images.push(capture.path);
                const screenshotRender = renderName(relative, section.id, capture.checkpoint);
                await page.screenshot({path: path.join(run, 'renders', screenshotRender), fullPage: false, animations: 'disabled'});
                result.layouts.push({section: section.id, kind: 'image', checkpoint: capture.checkpoint,
                    image: capture.path, caption: capture.caption, master: capture.master?.path,
                    path: 'renders/' + screenshotRender, width: 1920, height: 1080,
                    sha256: sha(path.join(run, 'renders', screenshotRender))});
            }
        }
        await page.screenshot({path: path.join(run, 'renders', relative.replaceAll('/', '-') + '.png'), fullPage: false, animations: 'disabled'});
        if (relative.startsWith('tutorials/come-assegnare')) await page.screenshot({path: path.join(run, 'manual-preview.png'), fullPage: false});
        report.pages.push(result);
    }
    if (!report.pages.length) throw new Error('No verified pages to render');
    report.checks = ['real Mintlify renders updated MDX', 'all verified citation anchors exist',
        'every newly referenced capture loads', 'every verified section and image layout retained at Full HD for visual QA'];
    report.status = 'passed';
    // Only publish the mappings after every page, heading and image passed.
    const manifestFile = path.join(run, 'manifest.json');
    fs.writeFileSync(manifestFile + '.rendered.tmp', JSON.stringify(manifest, null, 2) + '\n');
    fs.renameSync(manifestFile + '.rendered.tmp', manifestFile);
} catch (error) {
    report.status = 'failed'; report.error = error.message; process.exitCode = 1;
} finally {
    await browser.close();
    fs.writeFileSync(path.join(run, 'render.json'), JSON.stringify(report, null, 2) + '\n');
    const cards = (report.pages || []).map(entry => '<section><h2>' + escape(entry.page) + '</h2><div class="grid">' +
        entry.layouts.map(layout => '<article><a href="' + escape(path.basename(layout.path)) + '"><img src="' +
            escape(path.basename(layout.path)) + '" loading="lazy" width="1920" height="1080" alt="' + escape(layout.section) + '"/></a><h3>' +
            escape(layout.section) + '</h3><p>' + escape(layout.caption || 'Intestazione della sezione') + '</p>' +
            (layout.image ? '<a href="../manual/' + escape(layout.image) + '">Immagine della guida</a> ' : '') +
            (layout.master ? '<a href="../' + escape(layout.master) + '">Originale Full HD</a>' : '') + '</article>').join('') + '</div></section>').join('');
    fs.mkdirSync(path.join(run, 'renders'), {recursive: true});
    fs.writeFileSync(path.join(run, 'renders', 'index.html'), '<!doctype html><html lang="it"><meta charset="utf-8">' +
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>Revisione delle guide</title>' +
        '<style>body{margin:32px;background:#f7f8fc;color:#253145;font:16px/1.6 system-ui}main{max-width:1440px;margin:auto}' +
        '.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,440px),1fr));gap:20px}' +
        'article{background:white;border:1px solid #e4e7ef;border-radius:12px;padding:16px}img{width:100%;height:auto}h3{overflow-wrap:anywhere}' +
        'a{color:#6250b5}</style><main><h1>Revisione delle guide</h1><p>Rendering automatico: ' + escape(report.status) +
        '. Queste immagini consentono la revisione visiva; il caricamento riuscito non attesta da solo la qualità del layout.</p>' + cards + '</main></html>\n');
}
console.log('Manual render verification: ' + report.status);
