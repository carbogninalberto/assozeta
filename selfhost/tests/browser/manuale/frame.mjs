// Keep a real Full HD master before taking any focused crop.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

export const FULL_HD = Object.freeze({width: 1920, height: 1080});
export function viewportCrop(box, viewport = FULL_HD) {
    if (!box || !['x', 'y', 'width', 'height'].every(key => Number.isFinite(box[key]))
        || box.width <= 0 || box.height <= 0) throw new Error('Invalid screenshot crop');
    const x = Math.max(0, Math.floor(box.x)), y = Math.max(0, Math.floor(box.y));
    const right = Math.min(viewport.width, Math.ceil(box.x + box.width));
    const bottom = Math.min(viewport.height, Math.ceil(box.y + box.height));
    if (right <= x || bottom <= y) throw new Error('Screenshot crop is outside the Full HD frame');
    return {x, y, width: right - x, height: bottom - y};
}
export function pngDimensions(contents) {
    if (contents.length < 24 || !contents.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))
        || contents.subarray(12, 16).toString() !== 'IHDR') throw new Error('Screenshot is not a PNG');
    return {width: contents.readUInt32BE(16), height: contents.readUInt32BE(20)};
}
const hash = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
async function settleRequests(page) {
    const instrumented = await page.evaluate(() => Boolean(window.__manualeRequestActivity));
    if (instrumented) {
        await page.waitForFunction(() => {
            const state = window.__manualeRequestActivity;
            return state && state.pending === 0 && performance.now() - state.changed >= 100;
        }, null, {timeout: 15000});
    } else {
        // Standalone frame unit tests do not load the fixture session observer.
        await page.waitForLoadState('networkidle', {timeout: 15000});
    }
}
export async function captureFrame({page, run, relative, checkpoint, locator, clip, mask = [],
    redactionReason = '', animations = 'disabled', blurEditableFocus = false}) {
    const viewport = page.viewportSize();
    if (viewport?.width !== FULL_HD.width || viewport?.height !== FULL_HD.height
        || await page.evaluate(() => window.devicePixelRatio) !== 1) {
        throw new Error('Manual screenshots require a 1920 × 1080 viewport at scale 1');
    }
    if (path.isAbsolute(relative) || relative.split('/').includes('..') || !relative.startsWith('images/')) {
        throw new Error('Unowned screenshot path');
    }
    // Wait for real reads and their dependent requests before documenting the UI.
    // A visible table can precede its totals/cards; that intermediate state is not
    // a completed manual step. Completed downloads are not application reads.
    await settleRequests(page);
    await page.mouse.move(0, 0);
    await page.evaluate(() => document.fonts.ready);
    if (locator) {
        await locator.scrollIntoViewIfNeeded();
    }
    // Screenshot animations:'disabled' advances finite animations to their end.
    // Measure the crop at that same settled geometry, never at the smaller
    // intermediate transform of a dialog opening.
    await page.evaluate(async () => {
        const animations = document.getAnimations().filter(animation =>
            Number.isFinite(animation.effect?.getComputedTiming().endTime));
        await Promise.all(animations.map(animation => animation.finished.catch(() => {})));
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    });
    if (blurEditableFocus) {
        await page.evaluate(() => {
            const active = document.activeElement;
            if (active?.matches('input, textarea, [contenteditable="true"]')) active.blur();
        });
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)));
    }
    if (locator) {
        clip = await locator.boundingBox();
        if (!clip) throw new Error('Screenshot focus is not visible');
    }
    const crop = clip ? viewportCrop(clip) : undefined;
    const masterPath = 'masters/' + relative;
    const masterFile = path.join(run, masterPath);
    const file = path.join(run, 'captures', relative);
    fs.mkdirSync(path.dirname(masterFile), {recursive: true});
    fs.mkdirSync(path.dirname(file), {recursive: true});
    const options = {animations, caret: 'hide', mask, maskColor: '#CBD5E1', fullPage: false};
    // Prove the checkpoint is visually settled with consecutive real captures.
    // Retain both images: this is same-state stability, not fresh-run identity.
    let previous = await page.screenshot(options);
    let stable;
    for (let attempt = 0; attempt < 5; attempt++) {
        await page.waitForTimeout(100);
        const next = await page.screenshot(options);
        if (previous.equals(next)) { stable = next; break; }
        previous = next;
    }
    if (!stable) throw new Error('Manual checkpoint did not reach a stable rendered state: ' + checkpoint);
    const repeatPath = 'masters/repeats/' + relative;
    const repeatFile = path.join(run, repeatPath);
    fs.mkdirSync(path.dirname(repeatFile), {recursive: true});
    fs.writeFileSync(masterFile, previous);
    fs.writeFileSync(repeatFile, stable);
    const masterSize = pngDimensions(fs.readFileSync(masterFile));
    if (masterSize.width !== FULL_HD.width || masterSize.height !== FULL_HD.height) {
        throw new Error('Captured master is not Full HD');
    }
    if (crop) await page.screenshot({...options, path: file, clip: crop});
    else fs.copyFileSync(masterFile, file);
    const size = pngDimensions(fs.readFileSync(file));
    if (crop && (size.width !== crop.width || size.height !== crop.height)) throw new Error('Crop dimensions differ from its bounds');
    return {path: relative, sha256: hash(file), checkpoint, ...size,
        master: {path: masterPath, sha256: hash(masterFile), ...masterSize},
        stability: {scope: 'same-state-consecutive-captures', path: repeatPath,
            sha256: hash(repeatFile), changed_pixel_fraction: 0},
        ...(crop ? {clip: crop} : {}),
        ...(mask.length ? {redaction_reason: redactionReason || 'private field'} : {})};
}
