<script>
    import {X} from 'phosphor-svelte';
    import {onMount, onDestroy, createEventDispatcher} from 'svelte';
    import {fly} from 'svelte/transition';

    const focusableSelector = [
        'a[href]',
        'area[href]',
        'button:not([disabled])',
        'input:not([disabled])',
        'select:not([disabled])',
        'textarea:not([disabled])',
        'iframe',
        'object',
        'embed',
        '[contenteditable]',
        '[tabindex]:not([tabindex="-1"])',
    ].join(',');

    function focusTrap(node, active = true) {
        let previouslyFocused = null;

        function getFocusableElements() {
            return Array.from(node.querySelectorAll(focusableSelector)).filter(element => {
                return element.offsetParent !== null || element === document.activeElement;
            });
        }

        function focusFirstElement() {
            if (!active) return;

            previouslyFocused = document.activeElement;

            setTimeout(() => {
                const focusableElements = getFocusableElements();
                const firstElement = focusableElements[0] || node;

                if (!node.hasAttribute('tabindex')) {
                    node.setAttribute('tabindex', '-1');
                }

                firstElement.focus?.();
            }, 0);
        }

        function restoreFocus() {
            if (previouslyFocused && previouslyFocused !== document.body) {
                previouslyFocused.focus?.();
            }

            previouslyFocused = null;
        }

        function handleKeydown(event) {
            if (!active || event.key !== 'Tab') return;

            const focusableElements = getFocusableElements();

            if (focusableElements.length === 0) {
                event.preventDefault();
                node.focus?.();
                return;
            }

            const firstElement = focusableElements[0];
            const lastElement = focusableElements[focusableElements.length - 1];

            if (event.shiftKey && document.activeElement === firstElement) {
                event.preventDefault();
                lastElement.focus();
            } else if (!event.shiftKey && document.activeElement === lastElement) {
                event.preventDefault();
                firstElement.focus();
            }
        }

        document.addEventListener('keydown', handleKeydown);
        focusFirstElement();

        return {
            update(nextActive = true) {
                const wasActive = active;
                active = nextActive;

                if (!wasActive && active) {
                    focusFirstElement();
                } else if (wasActive && !active) {
                    restoreFocus();
                }
            },
            destroy() {
                document.removeEventListener('keydown', handleKeydown);
                restoreFocus();
            },
        };
    }

    let scrollLockCount = 0;
    let scrollPreviousOverflow = '';

    function lockBodyScroll() {
        if (typeof document === 'undefined') {
            return () => {};
        }

        if (scrollLockCount === 0) {
            scrollPreviousOverflow = document.body.style.overflow;
            document.body.style.overflow = 'hidden';
        }

        scrollLockCount += 1;

        let released = false;

        return () => {
            if (released) return;

            released = true;
            scrollLockCount = Math.max(0, scrollLockCount - 1);

            if (scrollLockCount === 0) {
                document.body.style.overflow = scrollPreviousOverflow || 'auto';
                scrollPreviousOverflow = '';
            }
        };
    }

    export let isOpen = false;
    export let position = 'right';
    export let width = '85vw';
    export let height = '100%';
    export let maxHeight = '100%';
    export let closeOnClickOutside = true;
    export let closeOnEsc = true;
    export let title = '';

    let drawer;
    let backdrop;
    let touchOverflow = true;
    let isCompactViewport = false;
    let releaseScrollLock;
    const dispatch = createEventDispatcher();

    function handleKeydown(event) {
        if (closeOnEsc && event.key === 'Escape' && isOpen) {
            close();
        }
    }

    function handleBackdropClick(event) {
        if (closeOnClickOutside && event.target === backdrop) {
            close();
        }
    }

    function close() {
        isOpen = false;
        dispatch('close');
    }

    function updateViewportState() {
        isCompactViewport = window.innerWidth < 920;
    }

    onMount(() => {
        document.addEventListener('keydown', handleKeydown);
        updateViewportState();
        window.addEventListener('resize', updateViewportState);
    });

    onDestroy(() => {
        document.removeEventListener('keydown', handleKeydown);
        window.removeEventListener('resize', updateViewportState);
        releaseScrollLock?.();
    });

    $: if (touchOverflow && isOpen && !releaseScrollLock) {
        releaseScrollLock = lockBodyScroll();
    } else if ((!isOpen || !touchOverflow) && releaseScrollLock) {
        releaseScrollLock();
        releaseScrollLock = null;
    }

    function getTransitionParams() {
        const duration = 300;
        switch (position) {
            case 'left':
                return {x: -width, duration};
            case 'right':
                return {x: width, duration};
            case 'top':
                return {y: '-100%', duration};
            case 'bottom':
                return {y: '100%', duration};
        }
    }

    $: drawerStyles = `
      width: ${width && isCompactViewport ? '100vw' : width};
      min-width: ${width && isCompactViewport ? '100vw' : width};
      max-width: ${width && isCompactViewport ? '100vw' : width};
      height: ${height};
      max-height: ${maxHeight};
      ${position}: 0;
    `;
</script>

{#if isOpen}
    <!-- svelte-ignore a11y-click-events-have-key-events -->
    <!-- svelte-ignore a11y-no-static-element-interactions -->
    <div
        class="drawer-backdrop"
        on:click={handleBackdropClick}
        bind:this={backdrop}
        transition:fly={{duration: 200, opacity: 0}}>
        <div
            class="drawer drawer-{position} shadow-lg"
            style={drawerStyles}
            bind:this={drawer}
            use:focusTrap={isOpen}
            on:click={e => {
                if (e.target === drawer) {
                    e.stopPropagation();
                }
            }}
            transition:fly={getTransitionParams()}>
            <div class="drawer-header">
                <slot name="header">
                    <h1 class="mb-0 font-weight-boldest">{title}</h1>
                    <button
                        class="btn btn-icon btn-xs rounded-circle close btn-secondary mb-0 font-weight-boldest p-0"
                        type="button"
                        on:click={close}>
                        <X size="14" weight="bold" />
                    </button>
                </slot>
            </div>
            <slot name="content" />
        </div>
    </div>
{/if}

<svelte:head>
    <style>
        /* Slide from right */
        .drawer-right {
            right: -100%;
            transition: right 1s ease-in-out;
        }
        .drawer-right.show {
            right: 0;
        }

        /* Slide from left */
        .drawer-left {
            left: -100%;
            transition: left 1s ease-in-out;
        }
        .drawer-left.show {
            left: 0;
        }

        /* Slide from top */
        .drawer-top {
            top: -100%;
            border-radius: 0 0 1.25rem 1.25rem !important;
            transition: top 1s ease-in-out;
        }
        .drawer-top.show {
            top: 0;
        }

        /* Slide from bottom */
        .drawer-bottom {
            bottom: -100%;
            border-radius: 1.25rem 1.25rem 0 0 !important;
            transition: bottom 1s ease-in-out;
        }
        .drawer-bottom.show {
            bottom: 0;
        }
    </style>
</svelte:head>

<style>
    .drawer-backdrop {
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        background-color: rgba(0, 0, 0, 0.3);
        z-index: 1040;
        backdrop-filter: blur(2px);
        -webkit-backdrop-filter: blur(2px);
    }

    .drawer {
        position: fixed;
        height: 100%;
        background-color: var(--bg-surface);
        border-radius: 1.5rem 0 0 0rem;
        z-index: 1050;
        overflow-y: auto;
        will-change: transform;
        scroll-behavior: smooth;
        -webkit-scroll-behavior: smooth;
        animation: smoothScroll var(--scroll-duration) cubic-bezier(0.45, 0.05, 0.55, 0.95);
    }

    .drawer-header {
        min-height: 5rem;
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 0 2rem;
    }
</style>
