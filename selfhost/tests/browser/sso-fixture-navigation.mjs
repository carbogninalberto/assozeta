import {setTimeout as delay} from 'node:timers/promises';

// Chromium can observe a runner network change on its first navigation. Repeat
// only this idempotent, empty fixture GET. Never wrap pairing or callback actions.
export async function openSSOFixture(page,base,wait=delay) {
    const url=base+'/fixture-blank.html';
    try { return await page.goto(url); }
    catch(error) {
        if(!/(?:^|\s)net::ERR_NETWORK_CHANGED(?:\s|$)/.test(error?.message||''))throw error;
        await wait(250);
        return await page.goto(url);
    }
}
