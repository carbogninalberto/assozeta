import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync, readdirSync} from 'node:fs';

import {createStripeLoader, getStripe} from './stripeLoader.js';

const uiRoot = new URL('../../', import.meta.url);
const read = path => readFileSync(new URL(path, uiRoot), 'utf8');

const checkoutPages = ['src/routes/stripe/Checkout.svelte', 'src/routes/stripe/CartCheckout.svelte'];

function sourceFiles(directory = 'src') {
    return readdirSync(new URL(`${directory}/`, uiRoot), {withFileTypes: true}).flatMap(entry => {
        const path = `${directory}/${entry.name}`;
        if (entry.isDirectory()) return entry.name === 'node_modules' ? [] : sourceFiles(path);
        return /\.(?:js|svelte|html)$/.test(entry.name) ? [path] : [];
    });
}

test('a falsy public key resolves null without touching the loader', async () => {
    const calls = [];
    const loader = createStripeLoader(key => {
        calls.push(key);
        return Promise.resolve({key});
    });

    for (const key of ['', null, undefined]) {
        assert.equal(await loader(key), null);
    }
    assert.deepEqual(calls, []);
});

test('the same key loads once and returns the identical promise', async () => {
    const instance = {id: 'stripe-instance'};
    const calls = [];
    const loader = createStripeLoader(key => {
        calls.push(key);
        return Promise.resolve(instance);
    });

    const first = loader('pk_test_same');
    const second = loader('pk_test_same');

    assert.equal(first, second);
    assert.deepEqual(calls, ['pk_test_same']);
    assert.equal(await first, instance);
    assert.equal(await second, instance);
});

test('different keys load once each and memoize separately', async () => {
    const calls = [];
    const loader = createStripeLoader(key => {
        calls.push(key);
        return Promise.resolve({key});
    });

    const first = loader('pk_test_one');
    const second = loader('pk_test_two');

    assert.notEqual(first, second);
    assert.deepEqual(calls, ['pk_test_one', 'pk_test_two']);

    const [one, two] = await Promise.all([first, second]);
    assert.equal(one.key, 'pk_test_one');
    assert.equal(two.key, 'pk_test_two');
    assert.notEqual(one, two);
});

test('a rejected load is not cached so the next call retries', async () => {
    const failure = new Error('stripe script unavailable');
    let calls = 0;
    const loader = createStripeLoader(() => {
        calls += 1;
        return calls === 1 ? Promise.reject(failure) : Promise.resolve({id: 'retried'});
    });

    await assert.rejects(loader('pk_test_retry'), error => error === failure);
    assert.equal(calls, 1);

    assert.deepEqual(await loader('pk_test_retry'), {id: 'retried'});
    assert.equal(calls, 2);
});

test('getStripe is exported as a function', () => {
    assert.equal(typeof getStripe, 'function');
});

test('index.html no longer loads the global stripe script', () => {
    assert.doesNotMatch(read('index.html'), /js\.stripe\.com/);
});

test('the loader imports only the side effect free stripe entry', () => {
    const source = read('src/utils/stripeLoader.js');

    assert.match(source, /from\s+['"]@stripe\/stripe-js\/pure(?:\.js)?['"]/);
    assert.doesNotMatch(source, /from\s+['"]@stripe\/stripe-js['"]/);
});

test('both checkout pages obtain stripe through the lazy loader', () => {
    for (const page of checkoutPages) {
        const source = read(page);

        assert.match(source, /import\s*\{[^}]*\bgetStripe\b[^}]*\}\s*from\s*['"][^'"]*utils\/stripeLoader\.js['"]/, page);
        assert.doesNotMatch(source, /(^|[^\w.$])Stripe\s*\(/m, page);
        assert.match(source, /get\(stripeConfig\)\?\.publicKey/, page);
    }
});

test('nothing but the loader mentions stripe itself', () => {
    const allowed = new Set(['src/utils/stripeLoader.js', 'src/utils/stripeLoader.test.js']);
    for (const file of sourceFiles()) {
        if (allowed.has(file)) continue;
        const source = read(file);

        assert.doesNotMatch(source, /@stripe\/stripe-js/, `${file} imports stripe eagerly`);
        assert.doesNotMatch(source, /js\.stripe\.com/, `${file} loads the global stripe script`);
    }
});

test('nothing but the checkout pages references the loader', () => {
    const allowed = new Set([...checkoutPages, 'src/utils/stripeLoader.test.js']);
    for (const file of sourceFiles()) {
        if (allowed.has(file)) continue;

        assert.doesNotMatch(read(file), /stripeLoader/, `${file} references the lazy loader`);
    }
});

test('the stripe js dependency is pinned to major version two', () => {
    const dependencies = JSON.parse(read('package.json')).dependencies ?? {};
    const version = dependencies['@stripe/stripe-js'];

    assert.equal(typeof version, 'string');
    assert.match(version, /^\^?2\./);
});
