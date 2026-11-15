import type { Action } from 'svelte/action';

type RevealOptions = {
	/** Delay in ms before the element animates in once visible. */
	delay?: number;
	/** IntersectionObserver threshold. */
	threshold?: number;
};

/**
 * Scroll-reveal action: content is visible by default (no JS, print,
 * full-page capture, reduced motion). Only elements that scroll into view
 * after load fade/slide in once.
 */
export const reveal: Action<HTMLElement, RevealOptions | undefined> = (node, options) => {
	const { delay = 0, threshold = 0 } = options ?? {};

	if (
		typeof IntersectionObserver === 'undefined' ||
		window.matchMedia('(prefers-reduced-motion: reduce)').matches
	) {
		return;
	}

	let initial = true;

	const observer = new IntersectionObserver(
		(entries) => {
			for (const entry of entries) {
				if (initial) {
					initial = false;

					// Already visible (or scrolled past) on first observation:
					// leave the element untouched, with no animation.
					if (entry.isIntersecting || entry.boundingClientRect.top < 0) {
						observer.disconnect();

						return;
					}

					continue;
				}

				if (entry.isIntersecting) {
					observer.disconnect();
					node.style.setProperty('--reveal-delay', `${delay}ms`);
					node.classList.add('reveal-init');
					// Force a style flush so the hidden state paints before transitioning.
					void node.offsetHeight;
					node.classList.add('reveal-in');

					return;
				}
			}
		},
		{ threshold }
	);

	observer.observe(node);

	return {
		destroy() {
			observer.disconnect();
		}
	};
};
