// Real mouse drag, then the production Save request and persisted readback.
// A successful report requires the real UI scenario; no dispatch or mocked API.
import {scenario, expect} from './scenario.mjs';
import {dashboardWorkflowSources} from './dashboard-sources.mjs';
import {runDashboardPersonalize} from './dashboard.mjs';

await scenario({id: 'dashboard-reorder', prefix: 'images/bacheca/riordinamento',
    sources: dashboardWorkflowSources, actions: async context => {
        const {page, api, capture, report} = context;
        const {initialRows, profile, save, column, reader} = await runDashboardPersonalize({
            ...context, capture: async () => {},
        });
        const before = (await profile(api)).dashboard_layout;
        const readerBefore = (await profile(reader.api)).dashboard_layout;
        await page.getByRole('button', {name: 'Modifica', exact: true}).click();
        await page.evaluate(() => window.scrollTo(0, 0));
        const labels = page.locator('section.min-w-100 > div .dashboard-widget .card-label');
        const originalLabels = await labels.allTextContents();
        const source = column(page, 'Pagamenti incassati').locator('.dashboard-widget .card-label');
        const destination = column(page, 'Iscrizioni').locator('.dashboard-widget .card-label');
        await expect(source).toBeVisible();
        await expect(destination).toBeVisible();
        // Real keyboard sorting remains available after the accessibility fix.
        const sortable = page.locator('section.min-w-100').first();
        await expect(sortable).toHaveAttribute('role', 'list');
        const paymentColumn = column(page, 'Pagamenti incassati');
        await expect(paymentColumn).toHaveAttribute('role', 'listitem');
        await expect(paymentColumn).toHaveAttribute('tabindex', '0');
        await paymentColumn.focus();
        await paymentColumn.press('Space');
        await paymentColumn.press('ArrowLeft');
        await paymentColumn.press('Space');
        await expect(labels).toHaveText([originalLabels[1], originalLabels[0], ...originalLabels.slice(2)]);
        expect((await profile(api)).dashboard_layout).toEqual(before);
        // Restore the draft with keyboard before proving the original mouse flow.
        await paymentColumn.focus();
        await paymentColumn.press('Space');
        await paymentColumn.press('ArrowRight');
        await paymentColumn.press('Space');
        await expect(labels).toHaveText(originalLabels);
        expect((await profile(api)).dashboard_layout).toEqual(before);
        await capture(page, 1, 'dashboard-reorder-edit-before-drag');
        const from = await source.boundingBox();
        const to = await destination.boundingBox();
        expect(from).not.toBeNull();
        expect(to).not.toBeNull();
        await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
        await page.mouse.down();
        await page.mouse.move(from.x + from.width / 2 - 15, from.y + from.height / 2, {steps: 5});
        await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2, {steps: 30});
        await page.mouse.up();
        const reordered = [initialRows[1], initialRows[0], ...initialRows.slice(2)];
        const expectedLabels = [originalLabels[1], originalLabels[0], ...originalLabels.slice(2)];
        await expect(labels).toHaveText(expectedLabels);
        // Drag changes the draft, not the persisted record.
        expect((await profile(api)).dashboard_layout).toEqual(before);
        await capture(page, 2, 'dashboard-reorder-drag-before-save');
        await save(page);
        await page.reload();
        expect((await profile(api)).dashboard_layout.rows[0]).toEqual(reordered);
        await expect(labels).toHaveText(expectedLabels);
        expect((await profile(reader.api)).dashboard_layout).toEqual(readerBefore);
        await capture(page, 3, 'dashboard-reorder-order-persists-after-reload');
        report.dashboard_reorder = {widgets: 8, first_widget: 'payments', second_widget: 'associates',
            real_mouse_drag: true, unsaved_database_unchanged: true, order_persisted_after_reload: true,
            sizes_preserved: true, reader_layout_preserved: true};
        report.checks = ['real keyboard reorder and restoration preserve unsaved database state',
            'real mouse drag then save persists order and preserves reader layout'];
        report.cleanup_policy = {status: 'pending-runner-reset', scope: 'manuale seed identities only'};
    }});
