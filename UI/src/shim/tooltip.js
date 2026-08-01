// -- module-level state ---------------------------------------------------
const SHIM_ATTR = 'data-shim-tip';
const TIP_SEL = `[${SHIM_ATTR}="1"]`;

/** owner element -> { tip, handlers, placement } */
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
    return el.getAttribute('data-placement') || 'top';
}

function _readHtml(el) {
    return el.getAttribute('data-html') === 'true';
}

function _readTrigger(el) {
    return el.getAttribute('data-trigger') || 'hover';
}

function _setContent(el, tipInner) {
    const useHtml = _readHtml(el);
    const title = el.getAttribute('data-original-title') || el.getAttribute('title') || '';
    if (useHtml) {
        tipInner.innerHTML = title;
    } else {
        tipInner.textContent = title;
    }
}

// -- positioning -----------------------------------------------------------

/**
 * Compute left & top for the tip + arrow offset.
 * placement: 'top' | 'bottom' | 'left' | 'right'
 * Returns { left, top, arrowLeft, arrowTop } — only one of arrowLeft/arrowTop
 * is meaningful depending on placement.
 */
function _computePosition(owner, tip, placement) {
    const targetRect = owner.getBoundingClientRect();
    const tipRect = tip.getBoundingClientRect();

    const scrollX = window.scrollX || window.pageXOffset;
    const scrollY = window.scrollY || window.pageYOffset;

    const vw = document.documentElement.clientWidth;
    const vh = document.documentElement.clientHeight;

    // Tooltip arrow sizing from theme CSS:
    //   .tooltip .arrow { width:0.8rem; height:0.4rem }
    //   .bs-tooltip-left .arrow, .bs-tooltip-right .arrow { width:0.4rem; height:0.8rem }
    // Arrow spans 0.8rem ≈ 12.8px along the placement axis; half = 0.4rem ≈ 6px for centering.
    const arrowHalf = 6;
    const arrowSize = 13;
    // .tooltip-inner has 3px border-radius; guard = 8px keeps arrow clear of corners
    const cornerGuard = 8;

    let left, top;

    // Initial placement — tip edge against target edge (CSS margins add visual gap)
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
        // Target center relative to tip
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

    // Clamp vertical into viewport (4px margin)
    if (placement === 'top' || placement === 'bottom') {
        top = Math.max(4, Math.min(vh - tipRect.height - 4, top));
    } else {
        left = Math.max(4, Math.min(vw - tipRect.width - 4, clampedLeft));
    }

    return { left: clampedLeft, top, arrowLeft, arrowTop };
}

function _positionTip(owner, tip, placement) {
    // Ensure tip is in DOM so we can measure
    if (!tip.parentNode) {
        document.body.appendChild(tip);
    }

    // Remove placement classes, add the right one
    tip.className = tip.className.replace(/\bbs-tooltip-\w+\b/g, '');
    tip.classList.add('tooltip', 'show', `bs-tooltip-${placement}`);

    // Read content at show time
    const inner = tip.querySelector('.tooltip-inner');
    if (inner) _setContent(owner, inner);

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
    tip.style.zIndex = '1080';
    tip.style.left = '-9999px';
    tip.style.top = '-9999px';
    tip.innerHTML = '<div class="arrow"></div><div class="tooltip-inner"></div>';
    tip._shimOwner = owner;
    return tip;
}

function _show(owner) {
    let state = _state.get(owner);
    if (!state) return;

    // Don't show an empty tooltip
    const text = owner.getAttribute('data-original-title') || owner.getAttribute('title') || '';
    if (!text.trim()) return;

    // Remove any existing tip for this owner
    if (state.tip) {
        _removeTip(state.tip);
        _tips.delete(state.tip);
    }

    const tip = _createTip(owner);
    state.tip = tip;
    _tips.add(tip);

    _positionTip(owner, tip, state.placement);

    // Double-check orphan on next frame (datatable row removal edge case)
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

// -- public API ------------------------------------------------------------

export function initTooltips(containerEl) {
    if (typeof document === 'undefined') return;
    const container = containerEl || document;

    // Sweep orphan tips before adding new ones
    _cleanOrphans();

    const els = container.querySelectorAll('[data-toggle="tooltip"]');
    els.forEach(function (el) {
        if (_state.has(el)) return; // idempotent

        // Bootstrap semantics: move title -> data-original-title, remove title
        const originalTitle = el.getAttribute('title');
        if (originalTitle !== null) {
            el.setAttribute('data-original-title', originalTitle);
            el.removeAttribute('title');
        }

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
            // Not used in this codebase; supported for completeness
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
            // Hide on outside click — lazy-bound once
            if (!_clickBound) {
                _clickBound = true;
                document.addEventListener('click', function docClick(e) {
                    // Hide any open click-triggered tips whose owner is not the target
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
        } else if (trigger === 'focus') {
            handlers.focusIn = showHandler;
            handlers.focusOut = hideHandler;
            el.addEventListener('focusin', showHandler);
            el.addEventListener('focusout', hideHandler);
        }

        _state.set(el, { tip: null, placement, trigger, handlers });
    });
}

export function destroyTooltips(containerEl) {
    if (typeof document === 'undefined') return;
    const container = containerEl || document;

    const els = container.querySelectorAll('[data-toggle="tooltip"]');
    els.forEach(function (el) {
        const state = _state.get(el);
        if (!state) return;

        // Remove open tip
        if (state.tip) {
            _tips.delete(state.tip);
            _removeTip(state.tip);
        }

        // Remove listeners
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

        // Restore title attribute
        const origTitle = el.getAttribute('data-original-title');
        if (origTitle !== null) {
            el.setAttribute('title', origTitle);
        }

        _state.delete(el);
    });
}
