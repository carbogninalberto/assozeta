// Bootstrap 4 collapse replacement shim — zero dependencies, vanilla DOM.
// Provides programmatic show/hide/toggle and a delegated data-API for
// [data-toggle="collapse"] with accordion (data-parent) support.

// --- Core collapse functions ---

export function toggleCollapse(id) {
  const el = typeof id === 'string' ? document.getElementById(id) : id;
  if (!el) return;
  if (el.classList.contains('show')) {
    hideCollapse(el);
  } else {
    showCollapse(el);
  }
}

export function showCollapse(el) {
  if (typeof el === 'string') el = document.getElementById(el);
  if (!el) return;
  el.classList.add('show');
  el.style.removeProperty('max-height');
}

export function hideCollapse(el) {
  if (typeof el === 'string') el = document.getElementById(el);
  if (!el) return;
  el.classList.remove('show');
  el.style.removeProperty('max-height');
}

// --- Trigger state helpers ---

function syncTriggerState(trigger, target) {
  const isShown = target.classList.contains('show');
  trigger.setAttribute('aria-expanded', isShown ? 'true' : 'false');
  if (isShown) {
    trigger.classList.remove('collapsed');
  } else {
    trigger.classList.add('collapsed');
  }
}

function findTriggerForTarget(target) {
  const sel = '#' + target.id;
  return document.querySelector(
    '[data-toggle="collapse"][data-target="' + sel + '"]'
  ) || document.querySelector(
    'a[data-toggle="collapse"][href="' + sel + '"]'
  );
}

// --- Data-API delegation (Bootstrap 4 compatibility) ---

let dataApiInstalled = false;

function onDocumentClick(event) {
  const trigger = event.target.closest('[data-toggle="collapse"]');
  if (!trigger) return;

  // Resolve target: prefer data-target, fall back to href (if starts with #)
  let targetSelector = trigger.getAttribute('data-target');
  if (!targetSelector) {
    const href = trigger.getAttribute('href');
    if (href && href.startsWith('#')) {
      targetSelector = href;
    }
  }
  if (!targetSelector || !targetSelector.startsWith('#')) return;

  const target = document.querySelector(targetSelector);
  if (!target) return;

  // Handle accordion (data-parent): Bootstrap markup may put this on either
  // the trigger or the target panel.
  const parentId = trigger.getAttribute('data-parent') || target.getAttribute('data-parent');
  if (parentId) {
    const parent = document.querySelector(parentId);
    if (parent) {
      const siblings = parent.querySelectorAll(
        '.collapse.show[data-parent="' + parentId + '"]'
      );
      siblings.forEach(function (sibling) {
        if (sibling !== target) {
          hideCollapse(sibling);
          // Keep sibling's trigger state in sync
          const siblingTrigger = findTriggerForTarget(sibling);
          if (siblingTrigger) syncTriggerState(siblingTrigger, sibling);
        }
      });
    }
  }

  // Toggle the target
  if (target.classList.contains('show')) {
    hideCollapse(target);
  } else {
    showCollapse(target);
  }

  // Keep trigger state in sync (aria-expanded, .collapsed)
  syncTriggerState(trigger, target);

  // Prevent default only for anchor triggers (avoids navigation)
  if (trigger.tagName === 'A' && trigger.getAttribute('href')) {
    event.preventDefault();
  }
}

function installDataApi() {
  if (dataApiInstalled || typeof document === 'undefined') return;
  dataApiInstalled = true;
  document.addEventListener('click', onDocumentClick);
}

// --- Public installer ---

export function initCollapseDataApi() {
  installDataApi();
}

// Auto-install on module load (safe no-op in Node/SSR)
installDataApi();
