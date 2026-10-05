// A genuine rectangular region spanning named real controls. No DOM/image edits.
export function unionFocusBounds(boxes, padding = 10) {
    if (!Array.isArray(boxes) || !boxes.length || !Number.isFinite(padding) || padding < 0)
        throw new Error('Screenshot focus requires visible real controls');
    for (const box of boxes)
        if (!box || !['x', 'y', 'width', 'height'].every(key => Number.isFinite(box[key]))
            || box.width <= 0 || box.height <= 0)
            throw new Error('Screenshot focus control is not visible');
    const left = Math.min(...boxes.map(box => box.x));
    const top = Math.min(...boxes.map(box => box.y));
    const right = Math.max(...boxes.map(box => box.x + box.width));
    const bottom = Math.max(...boxes.map(box => box.y + box.height));
    return {x: left - padding, y: top - padding,
        width: right - left + 2 * padding, height: bottom - top + 2 * padding};
}
export function focusRegion(...locators) {
    if (!locators.length) throw new Error('Screenshot focus requires real locators');
    return {
        // Frame settles fonts and animations after this scroll, then calls bounds.
        scrollIntoViewIfNeeded: () => locators[0].scrollIntoViewIfNeeded(),
        boundingBox: async () => unionFocusBounds(await Promise.all(locators.map(locator => locator.boundingBox()))),
    };
}
export function tableFocus(page, fields, rows = page.locator('[data-row]')) {
    return focusRegion(...fields.flatMap(field => [
        page.locator(`.datatable-head [data-field="${field}"]`).first(),
        rows.first().locator(`[data-field="${field}"]`),
        rows.last().locator(`[data-field="${field}"]`),
    ]));
}
// A native close-up of the start of a long value field. Its full control remains
// in the untouched master; only unused horizontal field surface is omitted.
export function focusStart(locator, maximumWidth = 640) {
    if (!Number.isFinite(maximumWidth) || maximumWidth <= 0)
        throw new Error('Screenshot focus width must be positive');
    return {
        scrollIntoViewIfNeeded: () => locator.scrollIntoViewIfNeeded(),
        boundingBox: async () => {
            const bounds = await locator.boundingBox();
            // Validate before trimming; a hidden control must never become a crop.
            unionFocusBounds([bounds], 0);
            return {...bounds, width: Math.min(bounds.width, maximumWidth)};
        },
    };
}
// Text in an empty-table message spans the table; measure the actual visible
// characters rather than its mostly blank, full-width layout span.
export function textFocus(locator) {
    return {
        scrollIntoViewIfNeeded: () => locator.scrollIntoViewIfNeeded(),
        boundingBox: () => locator.evaluate(element => {
            const range = document.createRange();
            range.selectNodeContents(element);
            const {x, y, width, height} = range.getBoundingClientRect();
            return {x, y, width, height};
        }),
    };
}
