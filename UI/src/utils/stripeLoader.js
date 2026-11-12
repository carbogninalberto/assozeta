import {loadStripe} from '@stripe/stripe-js/pure.js';

/**
 * Stripe.js loaded globally pings m.stripe.com on every route change, so it is fetched only
 * when a checkout page needs it, once per document.
 *
 * `loadStripe` comes from the `pure` entry, which does not inject the script as an import
 * side effect. Instances are memoized per publishable key, and a failed load is not cached so
 * a later visit can retry.
 *
 * @param {(publicKey: string) => Promise<object>} load - Loads the Stripe.js instance.
 * @returns {(publicKey: string) => Promise<object|null>} Loader returning the memoized instance.
 */
export function createStripeLoader(load) {
    const instances = new Map();

    return function getStripe(publicKey) {
        if (!publicKey) return Promise.resolve(null);
        if (instances.has(publicKey)) return instances.get(publicKey);

        const promise = Promise.resolve(load(publicKey)).catch(error => {
            if (instances.get(publicKey) === promise) instances.delete(publicKey);
            throw error;
        });
        instances.set(publicKey, promise);
        return promise;
    };
}

export const getStripe = createStripeLoader(loadStripe);
