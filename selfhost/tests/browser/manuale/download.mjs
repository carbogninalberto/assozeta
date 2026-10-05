// A download is verified by Chromium's actual download event and saved file.
// Navigating to an attachment ends navigation with a browser-version-specific
// signal, so await both operations without leaving an orphan rejection.
export async function navigateToDownload(page, url, label = 'Document') {
    const [download] = await Promise.all([
        page.waitForEvent('download'),
        page.goto(url).catch(error => {
            if (/net::ERR_ABORTED|Download is starting/.test(error.message)) return;
            const reason = error.message.match(/net::ERR_[A-Z_]+|Timeout[^\n]*/)?.[0] || 'navigation error';
            throw new Error(`${label} browser download failed (${reason})`);
        }),
    ]);
    const failure = await download.failure();
    if (failure) throw new Error(`${label} browser download failed (${failure})`);
    if (!await download.path()) throw new Error(`${label} browser download has no saved file`);
    return download;
}
