// Real UI writes and persisted backend assertions; execution belongs to the run owner.
import {scenario, expect} from './scenario.mjs';
import {dashboardWorkflowSources} from './dashboard-sources.mjs';
import {focusRegion} from './focus.mjs';
import {fileURLToPath} from 'node:url';

export async function runDashboardPersonalize({page, api, actor, input, capture, report}) {
        expect(input.fixture_version).toBe(8);
        expect(input.fixture_profile || 'baseline').toBe('baseline');
        const initialRows = [{id: 'associates', size: 6}, {id: 'payments', size: 6},
            {id: 'subscriptions', size: 4}, {id: 'subscriptionstoapprove', size: 8},
            {id: 'bestcourses', size: 4}, {id: 'expiringmedicalcertificates', size: 4},
            {id: 'todaylessons', size: 4}, {id: 'staffboard', size: 4}];
        const profile = async request => {
            const response = await request('profile/info');
            expect(response.status()).toBe(200);
            return (await response.json()).user_data;
        };
        const take = async (browserPage, number, checkpoint, locator) => {
            await expect(browserPage.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 10000});
            await expect(browserPage.locator('.dashboard-widget').getByText('...', {exact: true})).toHaveCount(0);
            await expect(browserPage.locator('.dashboard-widget [aria-busy="true"]')).toHaveCount(0);
            await capture(browserPage, number, checkpoint, locator);
        };
        const save = async browserPage => {
            const response = browserPage.waitForResponse(result =>
                new URL(result.url()).pathname === '/api/statistic/dashboard/layout' &&
                result.request().method() === 'POST');
            await browserPage.getByRole('button', {name: 'Salva', exact: true}).click();
            expect((await response).status()).toBe(200);
            await expect(browserPage.getByText('Layout salvato con successo', {exact: true})).toBeVisible();
        };
        const column = (browserPage, label) => browserPage.locator('section.min-w-100 > div').filter({
            has: browserPage.locator('.dashboard-widget .card-label').filter({hasText: new RegExp('^' + label + '$')})});
        await expect(page.getByRole('heading', {name: 'Bacheca', exact: true})).toBeVisible();
        expect((await profile(api)).dashboard_layout).toBeNull();
        await expect(page.locator('.dashboard-widget')).toHaveCount(8);
        await expect(page.locator('#today-lessons-widget')).toContainText('Nessuna lezione oggi');
        await take(page, 1, 'default-dashboard-eight-widgets');
        await page.getByRole('button', {name: 'Modifica', exact: true}).click();
        const associates = column(page, 'Iscrizioni');
        await expect(associates.getByRole('group', {name: 'Component size'})).toBeVisible();
        await expect(associates.getByRole('group').getByRole('button')).toHaveCount(4);
        await take(page, 2, 'edit-mode-size-remove-and-save-controls');
        await associates.getByRole('group').getByRole('button').nth(0).click();
        await page.getByRole('button', {name: 'Aggiungi widget', exact: true}).click();
        const modal = page.locator('#addWidget');
        await expect(modal).toBeVisible();
        const offered = modal.locator('.cursor-pointer');
        await expect(offered).toHaveCount(4);
        await expect(modal).not.toContainText('Pagamenti incassati');
        await offered.filter({hasText: 'Certificati medici scaduti'}).click();
        await take(page, 3, 'add-widget-modal-expired-certificates-selected', modal.locator('.modal-content'));
        // This modal's source retains aria-hidden even while visibly open.
        await modal.getByRole('button', {name: 'Aggiungi Widget', exact: true, includeHidden: true}).click();
        await expect(modal).not.toBeVisible();
        await expect(page.locator('.dashboard-widget')).toHaveCount(9);
        const expired = column(page, 'Certificati medici scaduti');
        await expect(expired).toHaveClass(/col-md-4/);
        await expired.getByRole('group').getByRole('button').nth(3).click();
        await expect(expired).toHaveClass(/col-md-12/);
        // The real edit toolbar overflows above the widget column (top:-1.5rem).
        const expiredControls = expired.getByRole('group', {name: 'Component size'}).locator('..');
        await expect(expiredControls).toBeVisible();
        await take(page, 4, 'added-widget-resized-to-full-width', focusRegion(expiredControls, expired));
        await save(page);
        await page.reload();
        await expect(page.getByRole('button', {name: 'Modifica', exact: true})).toBeVisible();
        const addedLayout = (await profile(api)).dashboard_layout;
        expect(addedLayout.rows[0]).toEqual([...initialRows.map(widget => widget.id === 'associates'
            ? {...widget, size: 4} : widget), {id: 'expiredmedicalcertificates', size: 12}]);
        await expect(column(page, 'Certificati medici scaduti')).toHaveClass(/col-md-12/);
        await take(page, 5, 'added-widget-and-width-persist-after-reload', column(page, 'Certificati medici scaduti'));
        await page.getByRole('button', {name: 'Modifica', exact: true}).click();
        const removalControls = column(page, 'Certificati medici scaduti')
            .getByRole('group', {name: 'Component size'}).locator('..');
        await expect(removalControls.locator('button.btn-light-danger')).toBeVisible();
        await take(page, 6, 'remove-added-widget-with-trash-control', removalControls);
        await column(page, 'Certificati medici scaduti').locator('button.btn-light-danger').click();
        await expect(page.locator('.dashboard-widget')).toHaveCount(8);
        await save(page);
        await page.reload();
        const removedLayout = (await profile(api)).dashboard_layout;
        expect(removedLayout.rows[0]).toEqual(initialRows.map(widget => widget.id === 'associates'
            ? {...widget, size: 4} : widget));
        await expect(column(page, 'Certificati medici scaduti')).toHaveCount(0);
        await take(page, 7, 'removed-widget-absent-after-reload');
        await page.getByRole('button', {name: 'Modifica', exact: true}).click();
        await page.getByRole('button', {name: 'Ripristina default', exact: true}).click();
        await expect(column(page, 'Iscrizioni')).toHaveClass(/col-md-6/);
        expect((await profile(api)).dashboard_layout).toEqual(removedLayout);
        await page.evaluate(() => window.scrollTo(0, 0));
        await take(page, 8, 'default-restored-before-save');
        await save(page);
        await page.reload();
        const resetLayout = (await profile(api)).dashboard_layout;
        expect(resetLayout.rows[0]).toEqual(initialRows);
        await expect(column(page, 'Iscrizioni')).toHaveClass(/col-md-6/);
        await take(page, 9, 'default-layout-persists-after-reload');
        const reader = await actor('reader');
        expect((await profile(reader.api)).dashboard_layout).toBeNull();
        await expect(reader.page.locator('.staff-board-widget')).toContainText('Permessi insufficienti');
        await reader.page.locator('.staff-board-widget').scrollIntoViewIfNeeded();
        await take(reader.page, 10, 'reader-dashboard-staff-permission-limit');
        await reader.page.getByRole('button', {name: 'Modifica', exact: true}).click();
        const readerPayments = column(reader.page, 'Pagamenti incassati');
        await readerPayments.getByRole('group').getByRole('button').nth(2).click();
        await save(reader.page);
        await reader.page.reload();
        const readerLayout = (await profile(reader.api)).dashboard_layout;
        expect(readerLayout.rows[0]).toEqual(initialRows.map(widget => widget.id === 'payments'
            ? {...widget, size: 8} : widget));
        expect((await profile(api)).dashboard_layout).toEqual(resetLayout);
        await expect(column(reader.page, 'Pagamenti incassati')).toHaveClass(/col-md-8/);
        await reader.page.evaluate(() => window.scrollTo(0, 0));
        await take(reader.page, 11, 'reader-personal-width-persists-owner-layout-unchanged');
        report.dashboard_workflow = {initial_widget_count: 8, available_widget_count: 12,
            initially_offered_widget_count: 4, added_widget: 'expiredmedicalcertificates',
            added_default_size: 4, added_saved_size: 12, associates_saved_size: 4,
            added_persisted_after_reload: true, removed_persisted_after_reload: true,
            reset_unsaved_left_database_unchanged: true, reset_persisted_after_reload: true,
            reader_layout_status: 200, reader_payments_size: 8, reader_layout_persisted_after_reload: true,
            owner_layout_preserved_after_reader_save: true, reader_staff_permission_message: true,
            drag_exercised: false, attendance_write_exercised: false, widget_business_actions_exercised: false};
        report.checks = ['eight baseline widgets; modal offers four remaining widgets from the twelve-entry map',
            'actual UI adds and resizes expired certificates then saves and reloads persisted layout',
            'trash removal and reset each persist only after the actual Save POST',
            'reader may personalize own layout with dashboard read permission; owner remains unchanged',
            'reader staff board displays Permessi insufficienti; no staff request or business write is fabricated'];
        return {initialRows, profile, take, save, column, reader};
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
    await scenario({id: 'dashboard-personalize', prefix: 'images/bacheca/personalizzazione',
        sources: dashboardWorkflowSources, actions: runDashboardPersonalize});
}
