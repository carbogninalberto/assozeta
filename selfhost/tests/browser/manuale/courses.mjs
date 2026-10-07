import {scenario, expect} from './scenario.mjs';
import {focusRegion, tableFocus, focusStart} from './focus.mjs';
import {coreLegacyAuthoredWorkflows} from '../../../../docs/manuale/core-legacy-authored-workflows.mjs';

await scenario({id: 'courses-create-edit', prefix: 'images/corsi/creazione-modifica', sources: coreLegacyAuthoredWorkflows['courses-create-edit'].sources, actions: async ({page, api, open, actor, capture, report}) => {
    await open('Attività', '/#/course/list');
    await expect(page.locator('[data-row]')).toHaveCount(1);
    await page.getByRole('button', {name: 'Corso o Abbonamento', exact: true}).click();
    const drawer = page.locator('.drawer').filter({has: page.getByPlaceholder('Titolo Corso')});
    await expect(drawer).toBeVisible();
    await drawer.getByPlaceholder('Titolo Corso').fill('Yoga del mattino');
    await drawer.locator('[contenteditable="true"]').fill('Lezioni di yoga per adulti.');
    await drawer.locator('input[name="fee"]').fill('90,00');
    await expect(drawer.locator('input[name="course_type"][value="1"]')).toBeChecked();
    await capture(page, 1, 'filled-standard-course', drawer);
    const created = page.waitForResponse(response => response.url().endsWith('/course/add') && response.request().method() === 'POST');
    await drawer.getByRole('button', {name: 'Salva', exact: true}).click();
    expect((await created).status()).toBe(200);
    await expect(drawer).not.toBeVisible();
    const row = page.locator('[data-row]').filter({hasText: 'Yoga del mattino'});
    await expect(row).toBeVisible();
    await expect(page.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 10000});
    await capture(page, 2, 'saved-course-in-list', tableFocus(page, ['title', 'description'], row));
    const list = await api('course/list?all=1');
    const course = (await list.json()).data.find(course => course.title === 'Yoga del mattino');
    expect(Number(course.fee)).toBe(90);
    await row.getByText('Yoga del mattino', {exact: true}).click();
    await page.getByRole('button', {name: 'Modifica', exact: true}).click();
    await page.getByPlaceholder('Titolo', {exact: true}).fill('Yoga del mattino avanzato');
    await expect(page.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 10000});
    await capture(page, 3, 'edit-course-title', focusStart(focusRegion(
        page.getByRole('heading', {name: 'Titolo', exact: true}),
        page.getByPlaceholder('Titolo', {exact: true}))));
    const updated = page.waitForResponse(response => response.url().endsWith(`/course/${course.course_id}/update`) && response.request().method() === 'PATCH');
    await page.getByRole('button', {name: 'Salva', exact: true}).click();
    expect((await updated).status()).toBe(200);
    await page.reload();
    await expect(page.locator('.card-title').getByText('Yoga del mattino avanzato')).toBeVisible();
    const persisted = (await (await api('course/list?all=1')).json()).data.find(entry => entry.course_id === course.course_id);
    expect(persisted.title).toBe('Yoga del mattino avanzato');
    expect(Number(persisted.fee)).toBe(90);
    await capture(page, 4, 'edited-course-persists-after-reload', focusRegion(
        page.locator('.card-title').getByText('Yoga del mattino avanzato'),
        page.getByText('90,00 €', {exact: true}).first()));
    const reader = await actor('reader');
    await reader.open('Attività', '/#/course/list');
    await expect(reader.page.locator('[data-row]').filter({hasText: persisted.title})).toBeVisible();
    await expect(reader.page.getByRole('button', {name: 'Corso o Abbonamento', exact: true})).toHaveCount(0);
    const readerCreate = await reader.api('course/add', {method: 'POST', data: {new_course: {title: 'Negato'}, subscriptions: []}});
    expect(readerCreate.status()).toBe(403);
    const readerUpdate = await reader.api(`course/${course.course_id}/update`, {method: 'PATCH', data: {title: 'Negato'}});
    expect(readerUpdate.status()).toBe(403);
    const afterDenied = (await (await api('course/list?all=1')).json()).data.find(entry => entry.course_id === course.course_id);
    expect(afterDenied.title).toBe(persisted.title);
    report.course_lifecycle = {fee: Number(course.fee), original_title: course.title, edited_title: persisted.title,
        title_persisted: persisted.title === 'Yoga del mattino avanzato', fee_preserved: Number(persisted.fee) === Number(course.fee),
        reader_create_status: readerCreate.status(), reader_update_status: readerUpdate.status(), denied_write_preserves_title: afterDenied.title === persisted.title};
    report.checks = ['real navigation through Attività → Corsi', 'standard course created with actual POST',
        'title edited with actual PATCH and survives reload', 'fee persists without alteration',
        'restricted collaborator reads courses but cannot create or update them', 'denied writes leave persisted state intact'];
}});
