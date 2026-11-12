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

// Each fixture container adds a veth interface to the runner. Its addresses
// settle (IPv6 duplicate address detection) shortly after the container starts,
// and Chromium fails every request in flight at that moment with
// ERR_NETWORK_CHANGED. Launch the browser only after the host's links and
// addresses are unchanged and none is tentative for a full quiet window.
export function readHostNetwork(run) {
    const read=args=>{const result=run('ip',args,{encoding:'utf8'});return result.status===0?result.stdout:null;};
    const links=read(['-o','link','show']),addresses=read(['-o','addr','show']);
    if(links===null||addresses===null)return null;
    // Packet counters and lifetimes change on their own; keep only identity and state.
    return (links+addresses).split('\n').map(line=>line.replace(/\s+(?:valid_lft|preferred_lft)\s+\S+/g,'').trim()).filter(Boolean).join('\n');
}
export async function waitForStableHostNetwork(read,{wait=delay,now=Date.now,quietMs=3000,timeoutMs=60000,intervalMs=250}={}) {
    const start=now();let last=read();
    if(last===null)return false; // No Linux netlink tools: nothing to wait for.
    let stableSince=now();
    for(;;) {
        if(!/\btentative\b/.test(last)&&now()-stableSince>=quietMs)return true;
        if(now()-start>=timeoutMs)throw new Error('Host network did not settle before the browser launch');
        await wait(intervalMs);
        const current=read();
        if(current===null)throw new Error('Host network state became unreadable');
        if(current!==last){last=current;stableSince=now();}
    }
}
