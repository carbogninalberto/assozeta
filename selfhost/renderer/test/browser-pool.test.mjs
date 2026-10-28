import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import test from 'node:test';
import {BrowserPool, PoolUnavailableError} from '../browser-pool.mjs';

class FakeBrowser extends EventEmitter {
    constructor() {
        super();
        this.connected = true;
        this.closeCalls = 0;
    }

    isConnected() {
        return this.connected;
    }

    async close() {
        this.closeCalls++;
        this.connected = false;
        this.emit('disconnected');
    }
}

function fakeLauncher() {
    const browsers = [];
    return {
        browsers,
        launch: async () => {
            const browser = new FakeBrowser();
            browsers.push(browser);
            return browser;
        },
    };
}

test('bounds concurrent leases and reuses the released browser', async () => {
    const launcher = fakeLauncher();
    const pool = new BrowserPool({
        launchBrowser: launcher.launch,
        size: 1,
        warmSize: 1,
        acquireTimeoutMs: 500,
    });
    await pool.warmUp();

    const first = await pool.acquire();
    let secondAcquired = false;
    const secondPromise = pool.acquire().then(resource => {
        secondAcquired = true;
        return resource;
    });
    await new Promise(resolve => setTimeout(resolve, 20));
    assert.equal(secondAcquired, false);

    pool.release(first);
    const second = await secondPromise;
    assert.equal(second, first);
    assert.equal(launcher.browsers.length, 1);
    assert.deepEqual(pool.getStats().metrics, {
        launched: 1,
        renders: 1,
        destroyed: 0,
        recycled: 0,
        launchFailures: 0,
        acquireTimeouts: 0,
    });
    pool.release(second);
    await pool.drain();
});

test('bounds simultaneous lazy browser creation', async () => {
    const browsers = [];
    let launches = 0;
    const pool = new BrowserPool({
        launchBrowser: async () => {
            launches++;
            await new Promise(resolve => setTimeout(resolve, 10));
            const browser = new FakeBrowser();
            browsers.push(browser);
            return browser;
        },
        size: 2,
        acquireTimeoutMs: 500,
    });

    const firstTwo = await Promise.all([pool.acquire(), pool.acquire()]);
    assert.equal(launches, 2);
    let thirdAcquired = false;
    const thirdPromise = pool.acquire().then(resource => {
        thirdAcquired = true;
        return resource;
    });
    await new Promise(resolve => setTimeout(resolve, 20));
    assert.equal(thirdAcquired, false);

    for (const resource of firstTwo) pool.release(resource);
    const third = await thirdPromise;
    assert.equal(launches, 2);
    pool.release(third);
    await pool.drain();
});

test('replaces a browser after disconnection', async () => {
    const launcher = fakeLauncher();
    const pool = new BrowserPool({
        launchBrowser: launcher.launch,
        size: 1,
        warmSize: 1,
    });
    await pool.warmUp();

    const first = await pool.acquire();
    pool.release(first);
    first.browser.connected = false;
    first.browser.emit('disconnected');

    const replacement = await pool.acquire();
    assert.notEqual(replacement.browser, first.browser);
    assert.equal(launcher.browsers.length, 2);
    pool.release(replacement);
    await pool.drain();
});

test('health probes use a leased browser without taking another pool slot', async () => {
    const launcher = fakeLauncher();
    const pool = new BrowserPool({
        launchBrowser: launcher.launch,
        size: 1,
        warmSize: 1,
    });
    await pool.warmUp();
    const leased = await pool.acquire();

    let probed;
    await pool.probeBrowser(async browser => {
        probed = browser;
    });

    assert.equal(probed, leased.browser);
    assert.equal(pool.getStats().leased, 1);
    assert.equal(pool.getStats().launching, 0);
    pool.release(leased);
    await pool.drain();
});

test('recycles a browser after its request budget is spent', async () => {
    const launcher = fakeLauncher();
    const pool = new BrowserPool({
        launchBrowser: launcher.launch,
        size: 1,
        warmSize: 1,
        browserMaxRequests: 1,
    });
    await pool.warmUp();

    const first = await pool.acquire();
    pool.release(first);
    await new Promise(resolve => setImmediate(resolve));
    const replacement = await pool.acquire();

    assert.notEqual(replacement.browser, first.browser);
    assert.equal(launcher.browsers.length, 2);
    assert.equal(pool.getStats().metrics.recycled, 1);
    pool.release(replacement);
    await pool.drain();
});

test('fails acquisition when all browsers remain leased', async () => {
    const launcher = fakeLauncher();
    const pool = new BrowserPool({
        launchBrowser: launcher.launch,
        size: 1,
        warmSize: 1,
        acquireTimeoutMs: 30,
    });
    await pool.warmUp();
    const leased = await pool.acquire();

    await assert.rejects(pool.acquire(), PoolUnavailableError);
    assert.equal(pool.getStats().metrics.acquireTimeouts, 1);
    pool.release(leased);
    await pool.drain();
});
