// Bootstrap 4 alert dismiss replacement shim — zero dependencies, vanilla DOM.
// Handles [data-dismiss="alert"] clicks, finds closest .alert, and removes it
// with fade transition support or synchronously.

// --- Core dismiss function ---

export function dismissAlert(el) {
  if (!el) return;
  const alert = el.classList.contains('alert') ? el : el.closest('.alert');
  if (!alert) return;

  const isFade = alert.classList.contains('fade');

  if (!isFade) {
    // Remove synchronously (no transition)
    alert.remove();
    return;
  }

  // Fade transition: remove .show to trigger opacity → 0, then clean up
  alert.classList.remove('show');

  let cleaned = false;

  function cleanup() {
    if (cleaned) return;
    cleaned = true;
    clearTimeout(timer);
    alert.removeEventListener('transitionend', handler);
    alert.remove();
  }

  function handler(e) {
    // Only react to the opacity transition (0.15s per Bootstrap CSS)
    if (e.propertyName === 'opacity') {
      cleanup();
    }
  }

  // Fallback timer in case transitionend doesn't fire (e.g. element hidden)
  const timer = setTimeout(cleanup, 200);

  alert.addEventListener('transitionend', handler);
}

// --- Data-API delegation (Bootstrap 4 compatibility) ---

let dataApiInstalled = false;

function onDocumentClick(event) {
  const dismisser = event.target.closest('[data-dismiss="alert"]');
  if (!dismisser) return;
  dismissAlert(dismisser);

  // Prevent default to avoid form submission or other side effects
  event.preventDefault();
}

function installDataApi() {
  if (dataApiInstalled || typeof document === 'undefined') return;
  dataApiInstalled = true;
  document.addEventListener('click', onDocumentClick);
}

// --- Public installer ---

export function initAlertDataApi() {
  installDataApi();
}

// Auto-install on module load (safe no-op in Node/SSR)
installDataApi();
