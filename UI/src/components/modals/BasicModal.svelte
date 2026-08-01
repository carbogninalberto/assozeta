<script>
    import {fade} from 'svelte/transition';
    import * as easing from 'svelte/easing';
    import Portal from 'svelte-portal';
    import {createEventDispatcher, onDestroy} from 'svelte';
    import {X} from 'phosphor-svelte';

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

    function clickOutside(node) {
        const handleClick = event => {
            if (node && !node.contains(event.target) && !event.defaultPrevented) {
                node.dispatchEvent(new CustomEvent('click_outside', {detail: event}));
            }
        };

        document.addEventListener('click', handleClick, true);

        return {
            destroy() {
                document.removeEventListener('click', handleClick, true);
            },
        };
    }

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

    const dispatch = createEventDispatcher();

    export let id;
    export let show = false;
    export let title = 'Modal Title';
    export let showTitle = false;
    export let cancelButton = 'Chiudi';
    export let showCancelButton = true;
    export let showFooter = true;
    export let showActionButton = true;
    export let actionButton = 'Conferma';
    export let scrollable = false;
    export let dataHeight = 'auto';
    export let modalSize = 'lg';
    export let fullHeight = false;
    export let alignFooterCenter = true;
    export let bodyClass = 'py-2 px-2 px-md-8';
    export let hideOnClickOutside = true;
    export let target = null;
    export let contentOverflowVisible = false;

    let releaseScrollLock;

    function close(reason = 'close') {
        show = false;

        if (reason === 'confirm') {
            dispatch('confirm');
            return;
        }

        if (reason === 'cancel') {
            dispatch('cancel');
        }

        dispatch('close');
    }

    const handleKeydown = e => {
        if (!show) return;

        if (e.key === 'Escape') {
            close('cancel');
        }
    };

    $: if (show && !releaseScrollLock) {
        releaseScrollLock = lockBodyScroll();
    } else if (!show && releaseScrollLock) {
        releaseScrollLock();
        releaseScrollLock = null;
    }

    onDestroy(() => {
        releaseScrollLock?.();
    });

    // add event listener to close modal on click outside
    const handleClickOutside = () => {
        if (hideOnClickOutside) {
            close('close');
        }
    };
</script>

<svelte:window on:keydown={handleKeydown} />

<!-- svelte-ignore missing-declaration -->
{#if show}
    <Portal target={target ? document.getElementById(target.replace('#', '')) : document.body}>
        <!-- Modal-->
        <div
            in:fade={{duration: 500, easing: easing.cubicInOut}}
            out:fade={{duration: 50}}
            class="modal fade show"
            {id}
            tabindex="-1"
            role="dialog"
            aria-labelledby="staticBackdrop"
            aria-hidden={show ? 'false' : 'true'}
            style="display:block; overflow-y:auto;">
            <div
                use:clickOutside
                on:click_outside={handleClickOutside}
                use:focusTrap={show}
                class="modal-dialog modal-{modalSize} modal-dialog-centered {scrollable
                    ? 'modal-dialog-scrollable'
                    : ''}"
                role="document">
                <div
                    class="modal-content"
                    style=" {fullHeight ? 'min-height: 95svh' : ''} {contentOverflowVisible
                        ? 'overflow: visible'
                        : ''}">
                    <!-- svelte-ignore a11y-click-events-have-key-events -->
                    <div class="w-24 h-24" style="position:absolute;right:0;z-index:999999">
                        <!-- svelte-ignore a11y-click-events-have-key-events -->
                        <span
                            class="btn btn-sm btn-icon btn-ghost m-1"
                            on:click={() => {
                                close('cancel');
                            }}>
                            <X weight="bold" size={20} class="m-2" />
                        </span>
                    </div>
                    {#if showTitle}
                        <div class="modal-header d-flex justify-content-left">
                            <h3>{title}</h3>
                        </div>
                    {/if}
                    <div
                        class="modal-body {bodyClass}"
                        data-scroll={scrollable}
                        data-height={dataHeight}
                        style=" {contentOverflowVisible ? 'overflow: visible' : ''}">
                        <slot />
                    </div>
                    {#if showFooter}
                        <div
                            class="modal-footer d-flex {alignFooterCenter
                                ? 'justify-content-center'
                                : 'justify-content-between'}">
                            <slot name="footer" />
                            {#if showCancelButton}
                                <button
                                    class="btn btn-secondary font-weight-boldest"
                                    on:click={() => {
                                        close('cancel');
                                    }}>
                                    {cancelButton}
                                </button>
                            {/if}
                            {#if showActionButton}
                                <button
                                    class="btn btn-primary font-weight-boldest font-weight-boldest"
                                    on:click={() => {
                                        close('confirm');
                                    }}>
                                    {actionButton}
                                </button>
                            {/if}
                        </div>
                    {/if}
                </div>
            </div>
        </div>
    </Portal>
{/if}
