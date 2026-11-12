/**
 * An HTML page fetched from the API, prepared for an iframe's `srcdoc`: its relative links
 * (styles, images) resolve against the address the page was fetched from.
 *
 * @param {string} html - The fetched page.
 * @param {string} address - Address the page was fetched from.
 * @returns {string} The page with a `<base>` element.
 */
export function framedDocument(html, address) {
    const page = typeof html === 'string' ? html : '';
    const base = `<base href="${String(address).replace(/&/g, '&amp;').replace(/"/g, '&quot;')}">`;
    const head = /<head(\s[^>]*)?>/i.exec(page);
    if (head) {
        const at = head.index + head[0].length;
        return page.slice(0, at) + base + page.slice(at);
    }
    return base + page;
}
