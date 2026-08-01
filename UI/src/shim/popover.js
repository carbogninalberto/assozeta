// -- module-level state ---------------------------------------------------
const SHIM_ATTR = 'data-shim-tip';
const TIP_SEL = `[${SHIM_ATTR}="1"]`;

/** owner element -> { tip, handlers, placement, trigger } */
const _state = new WeakMap();
/** live tip elements (for orphan sweep) */
const _tips = new Set();
/** whether the document-level click-outside listener has been bound (click trigger only) */
let _clickBound = false;

// -- tiny helpers (duplicated per file to avoid creating new modules) -------

function _removeTip(tip) {
    if (tip && tip.parentNode) tip.parentNode.removeChild(tip);
}

function _cleanOrphans() {
    for (const tip of _tips) {
        const owner = tip._shimOwner;
        if (!owner || !owner.isConnected) {
            _removeTip(tip);
            _tips.delete(tip);
        }
    }
}

function _readPlacement(el) {
    return el.getAttribute('data-placement') || 'right';
}

function _readHtml(el) {
    return el.getAttribute('data-html') === 'true';
}

function _readTrigger(el) {
    // Preserve the app's legacy default: hover instead of Bootstrap's click.
    return el.getAttribute('data-trigger') || 'hover';
}

function _setContent(el, tip) {
    const useHtml = _readHtml(el);
    const header = tip.querySelector('.popover-header');
    const body = tip.querySelector('.popover-body');

    if (header) {
        const title = el.getAttribute('title') || '';
        if (useHtml) {
            header.innerHTML = title;
        } else {
            header.textContent = title;
        }
    }

    if (body) {
        const content = el.getAttribute('data-content') || '';
        if (useHtml) {
            body.innerHTML = content;
        } else {
            body.textContent = content;
        }
    }
}

// -- positioning -----------------------------------------------------------

/**
 * Compute left & top for the tip + arrow offset.
 * placement: 'top' | 'bottom' | 'left' | 'right'
 * Returns { left, top, arrowLeft, arrowTop }
 */
function _computePosition(owner, tip, placement) {
    const targetRect = owner.getBoundingClientRect();
    const tipRect = tip.getBoundingClientRect();

    const scrollX = window.scrollX || window.pageXOffset;
    const scrollY = window.scrollY || window.pageYOffset;

    const vw = document.documentElement.clientWidth;
    const vh = document.documentElement.clientHeight;

    // Popover arrow sizing from theme CSS:
    //   .popover .arrow { width:1rem; height:0.5rem; margin:0 0.42rem }
    //   .bs-popover-left > .arrow, .bs-popover-right > .arrow { width:0.5rem; height:1rem; margin:0.42rem 0 }
    // Arrow spans 1rem = 16px along the placement axis; half of arrow = 0.5rem = 8px for centering.
    const arrowHalf = 8;
    const arrowSize = 16;
    // 1.5rem border-radius on .popover; guard = 12px keeps arrow clear of rounded corners
    const cornerGuard = 12;

    let left, top;

    if (placement === 'top') {
        top = targetRect.top + scrollY - tipRect.height;
        left = targetRect.left + scrollX + targetRect.width / 2 - tipRect.width / 2;
    } else if (placement === 'bottom') {
        top = targetRect.bottom + scrollY;
        left = targetRect.left + scrollX + targetRect.width / 2 - tipRect.width / 2;
    } else if (placement === 'left') {
        left = targetRect.left + scrollX - tipRect.width;
        top = targetRect.top + scrollY + targetRect.height / 2 - tipRect.height / 2;
    } else {
        // right
        left = targetRect.right + scrollX;
        top = targetRect.top + scrollY + targetRect.height / 2 - tipRect.height / 2;
    }

    // Clamp horizontal (keep >= 4px from viewport edges)
    const minX = 4;
    const maxX = vw - tipRect.width - 4;
    const clampedLeft = Math.max(minX, Math.min(maxX, left));

    let arrowLeft, arrowTop;

    if (placement === 'top' || placement === 'bottom') {
        const targetCenterX = targetRect.left + targetRect.width / 2 + scrollX;
        // Center arrow on target, then clamp so whole arrow stays inside tip with corner guard
        let arrowOffset = targetCenterX - clampedLeft - arrowHalf;
        const minArrow = cornerGuard;
        const maxArrow = tipRect.width - arrowSize - cornerGuard;
        if (minArrow > maxArrow) {
            // Tip too narrow for arrow; just center it
            arrowOffset = (tipRect.width - arrowSize) / 2;
        } else {
            arrowOffset = Math.max(minArrow, Math.min(maxArrow, arrowOffset));
        }
        arrowLeft = arrowOffset;
        arrowTop = undefined;
    } else {
        const targetCenterY = targetRect.top + targetRect.height / 2 + scrollY;
        // Center arrow on target, then clamp so whole arrow stays inside tip with corner guard
        let arrowOffset = targetCenterY - top - arrowHalf;
        const minArrow = cornerGuard;
        const maxArrow = tipRect.height - arrowSize - cornerGuard;
        if (minArrow > maxArrow) {
            // Tip too short for arrow; just center it
            arrowOffset = (tipRect.height - arrowSize) / 2;
        } else {
            arrowOffset = Math.max(minArrow, Math.min(maxArrow, arrowOffset));
        }
        arrowTop = arrowOffset;
        arrowLeft = undefined;
    }

    // Clamp vertical
    if (placement === 'top' || placement === 'bottom') {
        top = Math.max(4, Math.min(vh - tipRect.height - 4, top));
    } else {
        left = Math.max(4, Math.min(vw - tipRect.width - 4, clampedLeft));
    }

    return { left: clampedLeft, top, arrowLeft, arrowTop };
}

function _positionTip(owner, tip, placement) {
    if (!tip.parentNode) {
        document.body.appendChild(tip);
    }

    // Remove old placement classes, add new ones
    tip.className = tip.className.replace(/\bbs-popover-\w+\b/g, '');
    tip.classList.add('popover', 'show', `bs-popover-${placement}`);

    // Read content at show time
    _setContent(owner, tip);

    // Force layout, then position
    tip.style.left = '-9999px';
    tip.style.top = '-9999px';
    const pos = _computePosition(owner, tip, placement);

    tip.style.left = pos.left + 'px';
    tip.style.top = pos.top + 'px';

    const arrow = tip.querySelector('.arrow');
    if (arrow) {
        if (pos.arrowLeft != null) {
            arrow.style.left = pos.arrowLeft + 'px';
            arrow.style.top = '';
        }
        if (pos.arrowTop != null) {
            arrow.style.top = pos.arrowTop + 'px';
            arrow.style.left = '';
        }
    }
}

// -- show / hide -----------------------------------------------------------

function _createTip(owner) {
    const tip = document.createElement('div');
    tip.setAttribute(SHIM_ATTR, '1');
    tip.setAttribute('role', 'tooltip');
    tip.style.position = 'absolute';
    tip.style.zIndex = '1070';
    tip.style.left = '-9999px';
    tip.style.top = '-9999px';
    // Always include popover-header (CSS hides it when empty via :empty)
    tip.innerHTML = '<div class="arrow"></div><h3 class="popover-header"></h3><div class="popover-body"></div>';
    tip._shimOwner = owner;
    return tip;
}

function _show(owner) {
    let state = _state.get(owner);
    if (!state) return;

    // Don't show an empty popover
    const title = owner.getAttribute('title') || '';
    const content = owner.getAttribute('data-content') || '';
    if (!title.trim() && !content.trim()) return;

    if (state.tip) {
        _removeTip(state.tip);
        _tips.delete(state.tip);
    }

    const tip = _createTip(owner);
    state.tip = tip;
    _tips.add(tip);

    _positionTip(owner, tip, state.placement);

    requestAnimationFrame(() => {
        if (tip.isConnected && (!owner.isConnected || !document.body.contains(tip))) {
            _removeTip(tip);
            _tips.delete(tip);
            if (state.tip === tip) state.tip = null;
        }
    });
}

function _hide(owner) {
    const state = _state.get(owner);
    if (!state) return;
    if (state.tip) {
        _tips.delete(state.tip);
        _removeTip(state.tip);
        state.tip = null;
    }
}

// -- click-outside listener (lazy-init once per module) --------------------

function _ensureClickOutside() {
    if (_clickBound) return;
    _clickBound = true;
    document.addEventListener('click', function docClick(e) {
        for (const tip of _tips) {
            const owner = tip._shimOwner;
            if (!owner) continue;
            const s = _state.get(owner);
            if (!s || s.trigger !== 'click') continue;
            if (e.target !== owner && !owner.contains(e.target)) {
                _hide(owner);
            }
        }
    }, true);
}

// -- public API ------------------------------------------------------------

export function initPopovers(containerEl) {
    if (typeof document === 'undefined') return;
    const container = containerEl || document;

    _cleanOrphans();

    const els = container.querySelectorAll('[data-toggle="popover"]');
    els.forEach(function (el) {
        if (_state.has(el)) return; // idempotent

        const placement = _readPlacement(el);
        const trigger = _readTrigger(el);

        const handlers = {};

        function showHandler() {
            const s = _state.get(el);
            if (!s) return;
            _show(el);
        }

        function hideHandler() {
            _hide(el);
        }

        handlers.show = showHandler;
        handlers.hide = hideHandler;

        if (trigger === 'hover') {
            el.addEventListener('mouseenter', showHandler);
            el.addEventListener('mouseleave', hideHandler);
            el.addEventListener('focusin', showHandler);
            el.addEventListener('focusout', hideHandler);
        } else if (trigger === 'click') {
            function clickHandler(e) {
                const s = _state.get(el);
                if (!s) return;
                if (s.tip && s.tip.isConnected) {
                    _hide(el);
                } else {
                    _show(el);
                }
                e.stopPropagation();
            }
            handlers.click = clickHandler;
            el.addEventListener('click', clickHandler);
            _ensureClickOutside();
        } else if (trigger === 'focus') {
            handlers.focusIn = showHandler;
            handlers.focusOut = hideHandler;
            el.addEventListener('focusin', showHandler);
            el.addEventListener('focusout', hideHandler);
        }

        _state.set(el, { tip: null, placement, trigger, handlers });
    });
}

export function destroyPopovers(containerEl) {
    if (typeof document === 'undefined') return;
    const container = containerEl || document;

    const els = container.querySelectorAll('[data-toggle="popover"]');
    els.forEach(function (el) {
        const state = _state.get(el);
        if (!state) return;

        if (state.tip) {
            _tips.delete(state.tip);
            _removeTip(state.tip);
        }

        const h = state.handlers;
        if (h) {
            el.removeEventListener('mouseenter', h.show);
            el.removeEventListener('mouseleave', h.hide);
            el.removeEventListener('focusin', h.show);
            el.removeEventListener('focusout', h.hide);
            if (h.click) {
                el.removeEventListener('click', h.click);
            }
            if (h.focusIn) {
                el.removeEventListener('focusin', h.focusIn);
                el.removeEventListener('focusout', h.focusOut);
            }
        }

        _state.delete(el);
    });
}
