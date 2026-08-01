// --- Data-API delegation (Bootstrap 4 compatibility) ---

let dataApiInstalled = false;

function onDocumentClick(event) {
  // Handle data-toggle="modal" openers
  const opener = event.target.closest('[data-toggle="modal"]');
  if (opener) {
    const target = opener.getAttribute('data-target') || opener.getAttribute('href');
    if (target) {
      const id = target.startsWith('#') ? target.slice(1) : target;
      showModal(id);
    }
    if (opener.tagName === 'A') event.preventDefault();
    return;
  }

  // Handle data-dismiss="modal" closers
  const dismisser = event.target.closest('[data-dismiss="modal"]');
  if (dismisser) {
    const modal = dismisser.closest('.modal');
    if (modal && modal.id) {
      hideModal(modal.id);
    }
  }
}

function installDataApi() {
  if (dataApiInstalled || typeof document === 'undefined') return;
  dataApiInstalled = true;
  document.addEventListener('click', onDocumentClick);
}

// --- Core modal functions ---

/**
 * Shared teardown: remove backdrop, restore DOM state, fire hidden event.
 * Called synchronously for non-fade modals and after the transition for
 * fade modals.
 */
function finishHide(el, id, backdrop) {
  el.style.display = 'none';
  el.setAttribute('aria-hidden', 'true');

  const bd = backdrop || document.getElementById(`backdrop-${id}`);
  if (bd) {
    bd.remove();
  }

  document.body.classList.remove('modal-open');

  if (el._escHandler) {
    document.removeEventListener('keydown', el._escHandler);
    delete el._escHandler;
  }

  el.dispatchEvent(new CustomEvent('hidden.bs.modal'));
}

export function showModal(id) {
  const el = document.getElementById(id);
  if (!el) return;

  // --- Race safety -------------------------------------------------------
  // If a previous hideModal started a fade-out for this element and the
  // delayed cleanup hasn't run yet, cancel it and tear down immediately.
  // Otherwise the lingering cleanup would hide the freshly opened modal
  // and leave stale backdrops in the DOM.
  if (el._hideCleanup) {
    clearTimeout(el._hideCleanup.timer);
    el.removeEventListener('transitionend', el._hideCleanup.handler);
    const cleanupFn = el._hideCleanup.cleanup;
    delete el._hideCleanup;
    cleanupFn();
  }

  // Guard against double-open (prevents stacking backdrops and handlers)
  if (el.classList.contains('show')) return;

  const isFade = el.classList.contains('fade');

  // 1. Make the modal visible so the browser can render its initial state
  el.style.display = 'block';

  // 2. Create the backdrop (without 'show' when fading, so it starts
  //    transparent and transitions in together with the dialog)
  const backdrop = document.createElement('div');
  backdrop.className = isFade
    ? 'modal-backdrop fade'
    : 'modal-backdrop fade show';
  backdrop.id = `backdrop-${id}`;
  document.body.appendChild(backdrop);

  // 3. Force a synchronous layout so the browser paints the pre-show
  //    state (opacity 0 for the modal, translate(-50px) for the dialog,
  //    opacity 0 for the backdrop).  Without this reflow the browser
  //    coalesces the class addition and skips the transition entirely.
  if (isFade) {
    void el.offsetHeight;       // modal initial state
    void backdrop.offsetHeight; // backdrop initial state
  }

  // 4. Add 'show' — the CSS transitions now run
  el.classList.add('show');
  if (isFade) {
    backdrop.classList.add('show');
  }

  el.setAttribute('aria-hidden', 'false');
  document.body.classList.add('modal-open');

  // data-backdrop="static": clicking the backdrop must NOT close the modal
  if (el.getAttribute('data-backdrop') !== 'static') {
    backdrop.addEventListener('click', () => hideModal(id));
  }

  // Esc key handler — respect data-keyboard; skip if already installed
  if (el.getAttribute('data-keyboard') !== 'false' && !el._escHandler) {
    const escHandler = (e) => {
      if (e.key === 'Escape') {
        hideModal(id);
      }
    };
    document.addEventListener('keydown', escHandler);
    el._escHandler = escHandler;
  }

  el.dispatchEvent(new CustomEvent('shown.bs.modal'));
}

export function hideModal(id) {
  const el = document.getElementById(id);
  if (!el) return;

  // Guard: if the modal is not currently shown (or is already fading
  // out), ignore the call.  This makes double-hide and Esc-during-
  // fade-out safe.
  if (!el.classList.contains('show')) return;

  const isFade = el.classList.contains('fade');

  // Remove the visible classes so the reverse transitions begin
  el.classList.remove('show');

  const backdrop = document.getElementById(`backdrop-${id}`);
  if (backdrop) {
    backdrop.classList.remove('show');
  }

  // Non-fade modals: tear down synchronously (existing behaviour)
  if (!isFade) {
    finishHide(el, id, backdrop);
    return;
  }

  // Fade modals: wait for the dialog transition (transform 300ms) to
  // finish before removing elements from the DOM.  We listen for
  // 'transitionend' on the modal element (the dialog's transform
  // transition bubbles up) and fall back to a 300ms timer.
  let cleaned = false;
  let timer;

  const cleanup = () => {
    if (cleaned) return;
    cleaned = true;
    clearTimeout(timer);
    el.removeEventListener('transitionend', handler);
    delete el._hideCleanup;
    finishHide(el, id, backdrop);
  };

  const handler = (e) => {
    // Only react to the transform transition so the 300ms slide-up
    // has time to complete (opacity only takes 150ms).
    if (e.propertyName === 'transform' || e.propertyName === '-webkit-transform') {
      cleanup();
    }
  };

  timer = setTimeout(cleanup, 300);
  el.addEventListener('transitionend', handler);

  // Stash state on the element so showModal can cancel a pending
  // hide before opening the modal again (race safety).
  el._hideCleanup = { timer, handler, cleanup };
}

// --- Public data-API installer ---

export function initModalDataApi() {
  installDataApi();
}

// Auto-install on module load (in browser; safe no-op in Node)
installDataApi();
