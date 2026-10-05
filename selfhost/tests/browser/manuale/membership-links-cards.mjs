// Application evidence only. Native print completion and external delivery stay pending.
import {scenario, expect} from './scenario.mjs';
import {membershipLinksCardsAuthoredWorkflows} from '../../../../docs/manuale/membership-links-cards-authored-workflows.mjs';
import {openGiulia} from './member-profile-sources.mjs';
import {reloadOrganizationProfile} from './organization-access-sources.mjs';
import {pngDimensions} from './frame.mjs';
import manualConfig from '../playwright.manual.config.mjs';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';

const id = 'membership-links-cards', spec = membershipLinksCardsAuthoredWorkflows[id];
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const decoderPath = fileURLToPath(new URL('../node_modules/html5-qrcode/third_party/zxing-js.umd.js', import.meta.url));
// Decode unchanged, natural-resolution pixels. The bundled browser adapter
// misclassifies HTMLImageElement as video and reset() removes its source.
function decodeQrImagePixels(image) {
    const canvas = document.createElement('canvas');
    if (!image.naturalWidth || !image.naturalHeight) throw new Error('QR image is not loaded');
    canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
    const context = canvas.getContext('2d');
    context.drawImage(image, 0, 0);
    const source = new window.ZXing.HTMLCanvasElementLuminanceSource(canvas);
    const bitmap = new window.ZXing.BinaryBitmap(new window.ZXing.HybridBinarizer(source));
    const reader = new window.ZXing.QRCodeReader();
    try {
        return reader.decode(bitmap).getText();
    } catch (error) {
        // The app also produces isolated, axis-aligned QR images with no quiet
        // zone. ZXing's pure-image mode reads those unchanged pixels directly.
        return reader.decode(bitmap, new Map([[window.ZXing.DecodeHintType.PURE_BARCODE, true]])).getText();
    }
}
await scenario({id, prefix: spec.prefix.replace(/\/$/, ''), sources: spec.sources,
    actions: async ({page, api, open, actor, context, input, capture, report}) => {
        expect(input.fixture_version).toBe(8); expect(input.fixture_profile || 'baseline').toBe('baseline');
        const run = process.env.ASSOZETA_MANUAL_RUN;
        const state = JSON.parse(fs.readFileSync(path.join(run, 'run.json')));
        const json = async (route, request = api) => {const response = await request(route);
            expect(response.status(), route.replace(/token=.*/, 'token=[redacted]')).toBe(200); return response.json();};
        const profile = async () => (await json('profile/info')).user_data;
        const originalProfile = await profile();
        const originalSettings = structuredClone((await json('profile/settings')).settings);
        const settings = async () => (await json('profile/settings')).settings;
        const businessRecords = async () => ({
            subscriptions: Object.values((await json('subscription/list?pagination[perpage]=100')).data),
            payments: Object.values((await json('payment/list?pagination[perpage]=100')).data),
        });
        const originalRecords = await businessRecords();
        const uid = input.subscription_ids[0];
        const originalMember = (await json(`subscription/${uid}/info`)).data.info;
        expect(originalMember.associate.first_name).toBe('Giulia'); expect(originalMember.associate.last_name).toBe('Bianchi');
        expect(originalMember.status_flag).toBe(4);
        const facts = {}, tokens = [], pages = [], downloads = [];
        let downloadedPngCount = 0;
        const proof = (field, value) => {expect(value, field).toEqual(spec.outcome.expected[field]); facts[field] = value;};
        const take = async (browserPage, checkpoint, locator, mask = []) => {
            await expect(browserPage.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 15000});
            const n = spec.checkpoints.findIndex(item => item.id === checkpoint) + 1; expect(n).toBeGreaterThan(0);
            await capture(browserPage, n, checkpoint, locator, {mask,
                redactionReason: mask.length ? 'Registration URL and encoded association/subscription identifiers are private evidence.' : ''});
        };
        const popupErrors = [], popupFailures = [], businessWrites = [];
        const observe = browserPage => {
            browserPage.on('pageerror', error => popupErrors.push(error.message));
            browserPage.on('response', response => {if (response.status() >= 400 && response.url().startsWith(input.origin))
                popupFailures.push({path: new URL(response.url()).pathname, status: response.status()});});
            browserPage.on('request', request => {if (request.url().startsWith(input.origin) && /^(POST|PATCH|PUT|DELETE)$/.test(request.method())
                && /\/(subscription\/add|auth\/(register|signup)|collaborators\/add)(\/|$)/.test(new URL(request.url()).pathname))
                businessWrites.push({path: new URL(request.url()).pathname, method: request.method()});});
        };
        // Observe real beforeprint events; retain the browser's native print implementation.
        const observePrint = () => {window.__manualPrintRequests = 0;
            window.addEventListener('beforeprint', () => {window.__manualPrintRequests++;});};
        await context.addInitScript(observePrint);
        context.on('page', observe);
        const decoderBytes = fs.readFileSync(decoderPath);
        report.qr_decoder = {library: 'html5-qrcode bundled ZXing', sha256: sha(decoderBytes),
            input: 'unchanged image pixels at natural resolution', adapter: 'canvas luminance / core QRCodeReader with isolated QR fallback'};
        const decode = async (browserPage, image) => {
            await expect(image).toBeVisible(); await expect.poll(() => image.evaluate(img => img.complete && img.naturalWidth > 0)).toBe(true);
            if (!await browserPage.evaluate(() => Boolean(window.ZXing))) await browserPage.addScriptTag({path: decoderPath});
            return image.evaluate(decodeQrImagePixels);
        };
        const browserPrintPdf = async (browserPage, name) => {
            // This exercises Chromium's print renderer, independently of the native
            // dialog. It is not a PDF endpoint or download button in the application.
            const bytes = await browserPage.pdf({format: 'A4', printBackground: true, displayHeaderFooter: false});
            expect(bytes.subarray(0, 5).toString()).toBe('%PDF-'); expect(bytes.length).toBeGreaterThan(1000);
            expect(bytes.subarray(-2048).toString('latin1')).toContain('%%EOF');
            const relative = `browser-print-pdfs/membership-links-cards/${name}.pdf`;
            const file = path.join(run, relative); fs.mkdirSync(path.dirname(file), {recursive: true}); fs.writeFileSync(file, bytes);
            report.browser_print_pdfs ||= [];
            report.browser_print_pdfs.push({path: relative, bytes: bytes.length, sha256: sha(bytes),
                provenance: 'Playwright Page.pdf / Chromium print renderer', application_pdf_download: false,
                native_destination_selected: false});
            return bytes;
        };
        let anonymous, configured = false, originalError;
        try {
            const prepared = {...originalSettings, membership_card_configuration: {
                ...originalSettings.membership_card_configuration,
                customized_template: {color: '#2357A7', template: 'standard', show_qr_code: true}}};
            // Fixture preparation only; this scenario does not promote settings instructions.
            configured = true;
            expect((await api('profile/settings', {method: 'POST', data: prepared})).status()).toBe(200);
            expect((await settings()).membership_card_configuration).toEqual(prepared.membership_card_configuration);
            await reloadOrganizationProfile({page, api, context, expect});
            report.fixture_preparation = {existing_accepted_subscription: true, created_person: false,
                card_configuration: 'standard, blue, QR visible; restored afterwards', external_delivery_requested: false};
            await open('Organizzazione', '/#/members/list');
            await page.getByRole('button', {name: 'Libro Soci', exact: true}).click();
            await expect(page).toHaveURL(/#\/members\/members-book$/);
            await page.getByText('Condividi link iscrizioni', {exact: true}).click();
            const modal = page.locator('#share-link'); await expect(modal).toBeVisible();
            const displayedLink = await modal.locator('input[type="text"]').inputValue();
            const url = new URL(displayedLink);
            expect(url.origin).toBe(input.origin); expect(url.search).toBe('');
            expect(url.hash).toBe('#/subscribe/' + originalProfile.username.toLowerCase());
            const qr = modal.locator('#qr-code-subscriptions img');
            proof('qr_pixels_decode_to_displayed_link', await decode(page, qr) === displayedLink);
            await take(page, 'membership-share-dialog-real-qr', modal.locator('.modal-content'), [modal.locator('input'), qr]);
            await context.grantPermissions(['clipboard-read', 'clipboard-write'], {origin: input.origin});
            await modal.locator('[data-clipboard="true"]').click();
            await expect(page.getByText('Link copiato negli appunti', {exact: true})).toBeVisible();
            proof('clipboard_equals_displayed_link', await page.evaluate(() => navigator.clipboard.readText()) === displayedLink);
            // Capture the actual success message before toast settling hides it.
            await capture(page, 2, 'membership-link-clipboard-confirmation', modal.locator('.modal-content'), {
                mask: [modal.locator('input'), qr], redactionReason: 'Registration URL and encoded QR are excluded from public screenshots.'});
            const opening = page.waitForEvent('popup'); await modal.getByRole('button', {name: 'Apri', exact: true}).click();
            const authenticated = await opening; pages.push(authenticated);
            await authenticated.waitForLoadState('domcontentloaded');
            await expect(authenticated.getByText(originalProfile.sport_association.denomination, {exact: true})).toBeVisible();
            proof('authenticated_popup_is_current_module', authenticated.url() === displayedLink);
            await take(authenticated, 'membership-authenticated-module-opened');
            anonymous = await context.browser().newContext(manualConfig.use);
            anonymous.on('page', observe); await anonymous.addInitScript(observePrint);
            const publicPage = await anonymous.newPage(); pages.push(publicPage);
            await publicPage.goto(displayedLink);
            await expect(publicPage.getByText(originalProfile.sport_association.denomination, {exact: true})).toBeVisible();
            expect(await publicPage.evaluate(() => JSON.parse(localStorage.getItem('sessionToken')))).toBeNull();
            const publicConfig = await anonymous.request.get(input.origin + '/api/search/profile/' + originalProfile.username.toLowerCase() + '?module_info=1');
            expect(publicConfig.status()).toBe(200);
            const module = (await publicConfig.json()).data.user.sport_association;
            proof('anonymous_module_configuration_matches', JSON.stringify(module.enabled_for) === JSON.stringify(originalProfile.sport_association.enabled_for)
                && module.subscription_fee === originalProfile.sport_association.subscription_fee
                && module.membership_fee === originalProfile.sport_association.membership_fee);
            await publicPage.locator('[id="associate_data.type"]').click();
            for (const title of ['Socio', 'Socio e Tesserato', 'Tesserato'])
                await expect(publicPage.locator('.list-item').filter({hasText: new RegExp('^\\s*' + title + '\\s*$')})).toBeVisible();
            await publicPage.locator('.list-item').filter({hasText: /^\s*Socio e Tesserato\s*$/}).click();
            await expect(publicPage.getByText('25,00', {exact: false}).first()).toBeVisible();
            await take(publicPage, 'membership-anonymous-module-options');
            const qrOpening = page.waitForEvent('popup'); await modal.getByRole('button', {name: 'QR code', exact: true}).click();
            const qrPrint = await qrOpening; pages.push(qrPrint);
            await expect(qrPrint.locator('#content')).toContainText(originalProfile.sport_association.denomination);
            await expect(qrPrint.locator('#content')).toContainText(displayedLink);
            proof('qr_print_popup_contains_same_code_and_link', await decode(qrPrint, qrPrint.locator('#content img')) === displayedLink);
            // document.write clears listeners installed on the blank popup.
            // Observe and use the rendered page's genuine native print button.
            await qrPrint.evaluate(observePrint);
            await qrPrint.getByRole('button', {name: 'Stampa', exact: true}).click();
            await expect.poll(() => qrPrint.evaluate(() => window.__manualPrintRequests)).toBeGreaterThan(0);
            proof('qr_native_print_requested', true);
            await qrPrint.setViewportSize(manualConfig.use.viewport);
            await take(qrPrint, 'membership-qr-printable-popup', qrPrint.locator('#content'),
                [qrPrint.locator('#content img'), qrPrint.getByText(displayedLink, {exact: true})]);
            proof('qr_chromium_print_pdf_validated', (await browserPrintPdf(qrPrint, 'registration-qr')).subarray(0, 5).toString() === '%PDF-');
            await modal.getByRole('button', {name: 'Chiudi', exact: true}).first().click();

            const drawer = await openGiulia({page, open, expect});
            await expect(drawer.locator('[name="subscription_number"]')).toHaveValue(originalMember.subscription_number || '');
            await take(page, 'membership-card-correct-subscription', drawer);
            await drawer.getByRole('button', {name: 'Mostra Tessera', exact: true}).click();
            await expect(drawer.getByRole('button', {name: 'Nascondi Tessera', exact: true})).toBeVisible();
            const card = drawer.locator('#card-to-print'); await expect(card).toBeVisible();
            const expiration = new Date(originalMember.end_date).toLocaleDateString('it-IT');
            await expect(card).toContainText('Giulia'); await expect(card).toContainText('Bianchi');
            await expect(card).toContainText(originalProfile.sport_association.denomination);
            await expect(card).toContainText(originalMember.subscription_number || 'n/d'); await expect(card).toContainText(expiration);
            proof('card_person_period_and_number_match', true);
            proof('card_qr_pixels_identify_subscription', await decode(page, card.locator('img.qrcode')) === uid);
            await take(page, 'membership-card-visible', card, [card.locator('img.qrcode')]);
            const downloading = page.waitForEvent('download');
            await drawer.getByRole('button', {name: 'Scarica immagine', exact: true}).click();
            const download = await downloading; downloads.push(download); expect(download.suggestedFilename()).toBe('tessera.png');
            expect(await download.failure()).toBeNull(); const png = fs.readFileSync(await download.path());
            const dimensions = pngDimensions(png); expect(dimensions.width).toBeGreaterThan(250); expect(dimensions.height).toBeGreaterThan(150);
            // Decode the downloaded image independently of the live DOM QR/alt text.
            const downloadedImage = await page.evaluateHandle(async bytes => {const image = new Image();
                image.src = 'data:image/png;base64,' + bytes; await image.decode(); return image;}, png.toString('base64'));
            const downloadedQr = await downloadedImage.evaluate(decodeQrImagePixels);
            expect(downloadedQr).toBe(uid); await downloadedImage.dispose();
            proof('real_card_png_downloaded', true); report.card_png = {bytes: png.length, sha256: sha(png), ...dimensions, decoded_identifier_matches: true};
            downloadedPngCount++;
            await download.delete(); downloads.splice(downloads.indexOf(download), 1);
            await take(page, 'membership-card-png-downloaded', card, [card.locator('img.qrcode')]);
            const cardPost = page.waitForResponse(response => new URL(response.url()).pathname === `/api/subscription/${uid}/card`
                && response.request().method() === 'POST');
            const cardOpening = page.waitForEvent('popup'); await drawer.getByRole('button', {name: 'Stampa', exact: true}).click();
            const minted = await cardPost; expect(minted.status()).toBe(200);
            const token = (await minted.json()).token; expect(token).toMatch(/^[a-f0-9-]{36}$/i); tokens.push(token);
            const cardPrint = await cardOpening; pages.push(cardPrint);
            await cardPrint.waitForLoadState('domcontentloaded');
            await expect(cardPrint.locator('#card-to-print')).toBeVisible();
            const cardJson = await anonymous.request.get(`${input.origin}/api/subscription/${uid}/card?token=${token}`);
            expect(cardJson.status()).toBe(200); const publicCard = (await cardJson.json()).member;
            expect(publicCard.associate).toMatchObject({first_name: 'Giulia', last_name: 'Bianchi'});
            expect(publicCard.subscription_number).toBe(originalMember.subscription_number);
            expect(publicCard.subscription_end_date).toBe(originalMember.end_date);
            const anonymousCard = await anonymous.newPage(); pages.push(anonymousCard);
            await anonymousCard.goto(`${input.origin}/#/card/${uid}/${token}`);
            await expect(anonymousCard.locator('#card-to-print')).toContainText('Giulia');
            await expect(anonymousCard.locator('#card-to-print')).toContainText(expiration);
            proof('token_authorized_anonymous_card_opened', await decode(anonymousCard, anonymousCard.locator('img.qrcode')) === uid
                && await anonymousCard.evaluate(() => JSON.parse(localStorage.getItem('sessionToken'))) === null);
            await expect(cardPrint.locator('#card-to-print')).toContainText(expiration);
            proof('card_print_popup_matches_api', await decode(cardPrint, cardPrint.locator('#card-to-print img.qrcode')) === uid);
            await expect.poll(() => cardPrint.evaluate(() => window.__manualPrintRequests)).toBeGreaterThan(0);
            proof('card_native_print_requested', true);
            await cardPrint.setViewportSize(manualConfig.use.viewport);
            await take(cardPrint, 'membership-card-real-print-page', cardPrint.locator('#card-to-print'), [cardPrint.locator('img.qrcode')]);
            proof('card_chromium_print_pdf_validated', (await browserPrintPdf(cardPrint, 'giulia-card')).subarray(0, 5).toString() === '%PDF-');
            for (const query of ['', '?token=00000000-0000-0000-0000-000000000000'])
                expect((await anonymous.request.get(`${input.origin}/api/subscription/${uid}/card${query}`)).status()).toBe(400);
            proof('missing_and_invalid_card_tokens_denied', true);
            const anonymousMint = await anonymous.request.post(`${input.origin}/api/subscription/${uid}/card`, {data: {}});
            expect(anonymousMint.status()).toBe(403);
            const reader = await actor('reader'); const readDrawer = await openGiulia({...reader, expect});
            await readDrawer.getByRole('button', {name: 'Mostra Tessera', exact: true}).click();
            await expect(readDrawer.locator('#card-to-print')).toContainText('Giulia');
            const denied = await reader.api(`subscription/${uid}/card`, {method: 'POST', data: {}});
            proof('reader_token_generation_denied', denied.status() === 403);
            report.expected_denials.push({identity: 'reader', path: `/api/subscription/${uid}/card`, status: denied.status()},
                {identity: 'anonymous', path: `/api/subscription/${uid}/card`, status: anonymousMint.status()});
            await take(reader.page, 'membership-reader-card-controls', readDrawer.locator('#card-to-print'), [readDrawer.locator('img.qrcode')]);
            expect(businessWrites).toEqual([]); expect(popupErrors).toEqual([]); expect(popupFailures).toEqual([]);
        } catch (error) {
            // Hash-path card tokens never enter public reports, even in navigation failures.
            let message = error.message; for (const token of tokens) message = message.replaceAll(token, '[redacted]');
            originalError = new Error(message); throw originalError;
        } finally {
            const cleanupFailures = [];
            for (const download of downloads) try {await download.delete();} catch {cleanupFailures.push('download removal');}
            for (const browserPage of pages) try {if (!browserPage.isClosed()) await browserPage.close();} catch {cleanupFailures.push('popup closure');}
            if (anonymous) try {await anonymous.close();} catch {cleanupFailures.push('anonymous context closure');}
            try {await context.clearPermissions();} catch {cleanupFailures.push('clipboard permission reset');}
            if (configured) try {
                const restored = await api('profile/settings', {method: 'POST', data: originalSettings}); expect(restored.status()).toBe(200);
                expect(await settings()).toEqual(originalSettings);
                await reloadOrganizationProfile({page, api, context, expect});
            } catch {cleanupFailures.push('card configuration restoration');}
            if (tokens.length) {
                const privatePath = path.join(run, 'private-artifacts/membership-card-tokens.json');
                fs.mkdirSync(path.dirname(privatePath), {recursive: true});
                fs.writeFileSync(privatePath, JSON.stringify({version: 1, run_id: state.run_id,
                    owner_user_id: input.user_id, subscription_id: uid, tokens}, null, 2) + '\n', {mode: 0o600});
                report.private_cleanup = {contract: 'membership-card-tokens-v1', generated_token_count: tokens.length,
                    status: 'awaiting_runner_fixture_reset', exact_ids_kept_in_private_manifest: true,
                    mechanism: 'association-scoped disposable seed reset; scenario has no token-delete API'};
            }
            if (cleanupFailures.length) {report.cleanup_failures = cleanupFailures; if (!originalError) throw new Error('Membership evidence cleanup failed');}
        }
        proof('configuration_preserved', JSON.stringify(await settings()) === JSON.stringify(originalSettings));
        proof('business_records_preserved', JSON.stringify(await businessRecords()) === JSON.stringify(originalRecords));
        proof('local_downloads_cleaned', downloadedPngCount === 1 && downloads.length === 0 && pages.every(browserPage => browserPage.isClosed()));
        proof('token_cleanup_delegated_to_owned_fixture_reset', tokens.length === 1 && report.private_cleanup.status === 'awaiting_runner_fixture_reset');
        expect(facts).toEqual(spec.outcome.expected); report[spec.outcome.field] = facts;
        report.checks = ['actual QR pixels decoded independently of alt text', 'clipboard and authenticated/anonymous module readback agree',
            'actual QR print markup and card PNG checked', 'real Stampa token/popup matches public card API',
            'Chromium print PDFs have explicit harness provenance', 'native print request observed without replacing window.print',
            'reader and anonymous minting denied; settings and business records preserved'];
        report.external_gaps = spec.pending_sections.map(section => ({page: section.path, section: section.id,
            status: section.status, reason: section.reason}));
        report.external_gaps.push({operation: 'physical-qr-camera-scan', status: 'needs_external_verification',
            reason: 'Image pixels were decoded locally; no camera, mobile device or paper scan was exercised.'});
    },
});
