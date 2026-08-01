// Bootstrap 4 dropdown replacement shim — zero dependencies, vanilla DOM.
// Bootstrap 4 CSS is still present, so .dropdown-menu { display:none; position:absolute; }
// and .dropdown-menu.show { display:block } are honoured.  This shim provides the JS
// behaviour: delegated toggle, close-on-outside-click, escape-key, and overflow-clipping
// escape (position:fixed fallback for menus inside scroll/overflow containers).

// ---------------------------------------------------------------------------
// module-level guard — safe to import in SSR / plain Node
// ---------------------------------------------------------------------------
let installed = false;

function install() {
    if (installed || typeof document === 'undefined') return;
    installed = true;
    document.addEventListener('click', handleClick, false);
    document.addEventListener('keydown', handleKeydown, false);
}

// ---------------------------------------------------------------------------
// per-dropdown registry — maps menu element -> { toggle, parent, savedStyle, cleanup }
// ---------------------------------------------------------------------------
const _registry = new WeakMap();

// ---------------------------------------------------------------------------
// menu-finding helpers
// ---------------------------------------------------------------------------
const WRAPPER_SEL = '.dropdown, .dropup, .dropleft, .dropright, .btn-group';

function findMenu(toggle) {
    // Prefer the Bootstrap 4 wrapper pattern: .dropdown > .dropdown-menu
    const wrapper = toggle.closest(WRAPPER_SEL);
    if (wrapper) {
        const menu = wrapper.querySelector(':scope > .dropdown-menu');
        if (menu) return { menu: menu, parent: wrapper };
    }
    // Fallback: sibling right after the toggle (used by AssignTag, etc.)
    const sibling = toggle.nextElementSibling;
    if (sibling && sibling.classList.contains('dropdown-menu')) {
        return { menu: sibling, parent: null };
    }
    return null;
}

// ---------------------------------------------------------------------------
// overflow-clipping detection
// ---------------------------------------------------------------------------
function hasOverflowAncestor(el) {
    let cur = el.parentElement;
    while (cur && cur !== document.body) {
        const s = getComputedStyle(cur);
        if (s.overflow !== 'visible' || s.overflowX !== 'visible' || s.overflowY !== 'visible') {
            return true;
        }
        cur = cur.parentElement;
    }
    return false;
}

// ---------------------------------------------------------------------------
// fixed-position helpers (overflow escape)
// ---------------------------------------------------------------------------
function switchToFixed(menu, toggle) {
    const rec = _registry.get(menu);
    if (!rec || rec.savedStyle !== null) return;   // already switched
    rec.savedStyle = menu.style.cssText;
    menu.style.position = 'fixed';
    menu.style.right = 'auto';
    menu.style.zIndex = '1050';
    positionFixedMenu(menu, toggle);
}

function positionFixedMenu(menu, toggle) {
    const tRect = toggle.getBoundingClientRect();
    const mRect = menu.getBoundingClientRect();
    const isRight = menu.classList.contains('dropdown-menu-right');

    let top = tRect.bottom + 2;
    let left = isRight
        ? tRect.right - mRect.width
        : tRect.left;

    // Clamp inside viewport with a 4px gutter
    const vw = window.innerWidth;
    const vh = window.innerHeight;

    if (left < 4) left = 4;
    if (left + mRect.width > vw - 4) left = vw - mRect.width - 4;
    if (top < 4) top = 4;
    if (top + mRect.height > vh - 4) {
        // Try flipping above the toggle
        top = tRect.top - mRect.height - 2;
        if (top < 4) top = 4;
    }

    menu.style.top = Math.round(top) + 'px';
    menu.style.left = Math.round(left) + 'px';
}

function addOverflowListeners(menu) {
    function onOverflow() {
        closeDropdown(menu);
    }
    window.addEventListener('scroll', onOverflow, true);
    window.addEventListener('resize', onOverflow, true);
    const rec = _registry.get(menu);
    if (rec) rec.cleanup = onOverflow;
}

function removeOverflowListeners(menu) {
    const rec = _registry.get(menu);
    if (rec && rec.cleanup) {
        window.removeEventListener('scroll', rec.cleanup, true);
        window.removeEventListener('resize', rec.cleanup, true);
        rec.cleanup = null;
    }
}

// ---------------------------------------------------------------------------
// open / close
// ---------------------------------------------------------------------------
function openDropdown(toggle, menu, parent) {
    const needsFixed = hasOverflowAncestor(menu);

    _registry.set(menu, { toggle: toggle, parent: parent, savedStyle: null, cleanup: null });

    menu.classList.add('show');

    if (needsFixed) {
        switchToFixed(menu, toggle);
        addOverflowListeners(menu);
        // Re-measure now that the menu is display:block
        positionFixedMenu(menu, toggle);
    }

    if (parent) {
        parent.classList.add('show');
    }
    toggle.setAttribute('aria-expanded', 'true');

    const eventTarget = parent || toggle.parentElement;
    if (eventTarget) {
        eventTarget.dispatchEvent(new CustomEvent('shown.bs.dropdown', { bubbles: false }));
    }
}

function closeDropdown(menu) {
    if (!menu || !menu.classList.contains('show')) return;

    const rec = _registry.get(menu);

    menu.classList.remove('show');

    if (rec) {
        if (rec.parent) {
            rec.parent.classList.remove('show');
        }
        if (rec.toggle) {
            rec.toggle.setAttribute('aria-expanded', 'false');
        }

        // Restore original inline style (undoes position:fixed overrides)
        if (rec.savedStyle !== null) {
            menu.style.cssText = rec.savedStyle;
            rec.savedStyle = null;
        }

        removeOverflowListeners(menu);

        const eventTarget = rec.parent || (rec.toggle ? rec.toggle.parentElement : menu.parentElement);
        if (eventTarget) {
            eventTarget.dispatchEvent(new CustomEvent('hidden.bs.dropdown', { bubbles: false }));
        }

        _registry.delete(menu);
    } else {
        // Defensive fallback — not expected to be hit often
        let parentFound = null;
        let el = menu.parentElement;
        while (el) {
            if (el.classList.contains('show')) {
                el.classList.remove('show');
                parentFound = el;
                break;
            }
            el = el.parentElement;
        }
        const prev = menu.previousElementSibling;
        if (prev && prev.matches('[data-toggle="dropdown"]')) {
            prev.setAttribute('aria-expanded', 'false');
        }
        const eventTarget = parentFound || (prev ? prev.parentElement : menu.parentElement);
        if (eventTarget) {
            eventTarget.dispatchEvent(new CustomEvent('hidden.bs.dropdown', { bubbles: false }));
        }
    }
}

// ---------------------------------------------------------------------------
// exports — closeAllDropdowns is also used internally
// ---------------------------------------------------------------------------
export function closeAllDropdowns() {
    const menus = document.querySelectorAll('.dropdown-menu.show');
    menus.forEach(function (m) { closeDropdown(m); });
}

export function initDropdowns() {
    install();
}

// ---------------------------------------------------------------------------
// delegated click handler (bubble phase)
// ---------------------------------------------------------------------------
function handleClick(event) {
    // 1. Did we click on a dropdown toggle?
    const toggle = event.target.closest('[data-toggle="dropdown"]');
    if (toggle) {
        // Only preventDefault on anchor toggles that have href="#"
        if (toggle.tagName === 'A' && toggle.getAttribute('href') === '#') {
            event.preventDefault();
        }

        const info = findMenu(toggle);
        if (!info) return;

        const { menu, parent } = info;

        if (menu.classList.contains('show')) {
            // Toggle is already open -> close it
            closeDropdown(menu);
        } else {
            // Close any other open dropdowns, then open this one
            closeAllDropdowns();
            openDropdown(toggle, menu, parent);
        }
        return;   // IMPORTANT: don't fall through to "outside click" branch
    }

    // 2. Did we click inside an already-open dropdown-menu?
    const openMenu = event.target.closest('.dropdown-menu.show');
    if (openMenu) {
        // Keep open if the click is on a form control (Bootstrap 4 behaviour)
        const keepOpen = event.target.closest('input, textarea, select, label, [data-keep-open]');
        if (!keepOpen) {
            closeDropdown(openMenu);
        }
        return;
    }

    // 3. Every other click on the page -> close any open dropdowns
    closeAllDropdowns();
}

// ---------------------------------------------------------------------------
// delegated keydown handler — Escape closes all
// ---------------------------------------------------------------------------
function handleKeydown(event) {
    if (event.key === 'Escape') {
        closeAllDropdowns();
    }
}

// ---------------------------------------------------------------------------
// auto-install (import side-effect)
// ---------------------------------------------------------------------------
install();
