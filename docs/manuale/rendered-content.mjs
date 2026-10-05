// Presentation of already captured evidence; never produces capture reports.
export function displayedImages(body) {
    return [...new Set([
        ...[...body.matchAll(/!\[[^\]]*\]\((\/images\/[^)]+)\)/g)].map(match => match[1].slice(1)),
        ...[...body.matchAll(/<img\b[^>]*\bsrc=["'](\/images\/[^"']+)["'][^>]*>/g)].map(match => match[1].slice(1)),
    ])];
}

export function capturedImageCaptions(body, screenshots) {
    const images = new Map(screenshots.map(image => ['/' + image.path, image]));
    const escape = text => String(text).replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;');
    const replace = (attributes, name, image) => attributes.replace(new RegExp('\\b' + name + '="([^"]*)"'),
        (whole, caption) => /^(?:Segnaposto|Immagine segnaposto)\b/i.test(caption)
            ? name + '="' + escape(image.caption || image.checkpoint) + '"' : whole);
    body = body.replace(/<Frame\b([^>]*)>([\s\S]*?)<\/Frame>/g, (whole, attributes, contents) => {
        const paths = displayedImages(contents);
        const image = paths.length === 1 ? images.get('/' + paths[0]) : null;
        return image ? '<Frame' + replace(attributes, 'caption', image) + '>' + contents + '</Frame>' : whole;
    });
    return body.replace(/<img\b([^>]*)>/g, (whole, attributes) => {
        const source = attributes.match(/\bsrc=["']([^"']+)["']/)?.[1];
        const image = images.get(source);
        return image ? '<img' + replace(attributes, 'alt', image) + '>' : whole;
    });
}
