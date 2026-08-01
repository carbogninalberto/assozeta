<script>
    import {tick} from 'svelte';
    import {createEventDispatcher} from 'svelte';
    import {ChevronDown} from 'lucide-svelte';

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

    export let label = 'Dropdown';
    export let variant = 'primary'; // 'primary', 'secondary', 'success', 'danger', 'warning', 'info', 'light', 'dark'
    export let size = 'md'; // 'sm', 'md', 'lg'
    export let items = [];
    export let dropdownClass = '';
    export let buttonClass = '';
    export let itemClass = '';
    export let showCaret = true;

    const dispatch = createEventDispatcher();
    const dropdownId = `dropdown-${Math.random().toString(36).slice(2)}`;
    const menuId = `${dropdownId}-menu`;
    let open = false;
    let menu;

    function handleItemClick(item) {
        open = false;
        dispatch('itemClick', item);
    }

    function handleButtonClick() {
        open = !open;
        dispatch('buttonClick');
    }

    function close() {
        open = false;
    }

    function getMenuItems() {
        return Array.from(menu?.querySelectorAll('.dropdown-item:not([disabled])') || []);
    }

    async function focusMenuItem(index) {
        open = true;
        await tick();
        getMenuItems()[index]?.focus();
    }

    function focusNextItem(event, direction) {
        const menuItems = getMenuItems();
        const currentIndex = menuItems.indexOf(document.activeElement);
        const nextIndex = currentIndex === -1
            ? 0
            : (currentIndex + direction + menuItems.length) % menuItems.length;

        event.preventDefault();
        menuItems[nextIndex]?.focus();
    }

    function handleKeydown(event) {
        if (event.key === 'Escape') {
            close();
            return;
        }

        if (event.key === 'ArrowDown') {
            if (!open) {
                event.preventDefault();
                focusMenuItem(0);
            } else {
                focusNextItem(event, 1);
            }
        }

        if (event.key === 'ArrowUp') {
            if (!open) {
                event.preventDefault();
                focusMenuItem(items.length - 1);
            } else {
                focusNextItem(event, -1);
            }
        }
    }
</script>

<div class="dropdown {dropdownClass}" use:clickOutside on:click_outside={close} on:keydown={handleKeydown}>
    <button
        on:click={handleButtonClick}
        class="btn btn-{variant} btn-{size} dropdown-toggle bk-dropdown-toggle {buttonClass} d-flex align-items-center font-weight-boldest"
        type="button"
        id={dropdownId}
        aria-haspopup="menu"
        style="width: fit-content;"
        aria-controls={menuId}
        aria-expanded={open ? 'true' : 'false'}>
        <slot name="button-content">
            {label}
        </slot>
        {#if showCaret}
            <ChevronDown size={14} class="dropdown-caret ml-1" />
        {/if}
    </button>
    <div
        bind:this={menu}
        id={menuId}
        class="dropdown-menu {open ? 'show' : ''}"
        role="menu"
        aria-labelledby={dropdownId}
        style="display: {open ? 'block' : 'none'};">
        {#each items as item (item.id)}
            <button
                class="dropdown-item {itemClass}"
                type="button"
                role="menuitem"
                tabindex={open ? 0 : -1}
                on:click={() => handleItemClick(item)}>
                <slot name="item-content" {item}>
                    {#if item.icon}
                        <svelte:component this={item.icon} size={18} class="mr-2" />
                    {/if}
                    <span>{item.label}</span>
                </slot>
            </button>
        {/each}
    </div>
</div>

<style>
    .bk-dropdown-toggle::after {
        display: none !important;
    }

    .dropdown-caret {
        flex: 0 0 auto;
        transition: transform 0.2s ease;
    }

    [aria-expanded='true'] .dropdown-caret {
        transform: rotate(180deg);
    }
</style>
