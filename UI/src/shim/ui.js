export const UiApp = {
  block(target = 'body', options = {}) {
    const el = typeof target === 'string' ? document.querySelector(target) : target;
    if (!el) return;

    if (el.querySelector('.bkn-blockui-overlay')) return;

    const overlay = document.createElement('div');
    overlay.className = 'bkn-blockui-overlay';
    let inner = `<div class="bkn-spinner bkn-spinner--v2 bkn-spinner--${options.spinner || 'primary'}"></div>`;
    if (options.message) {
        inner += `<div style="margin-top:12px;font-size:0.85rem;color:var(--text-primary, #181C32);">${options.message}</div>`;
    }
    overlay.innerHTML = inner;
    overlay.style.cssText = `position:absolute;top:0;left:0;right:0;bottom:0;background:var(--bg-surface, rgba(255,255,255,0.8));z-index:${options.zIndex || 100};display:flex;flex-direction:column;align-items:center;justify-content:center;`;
    el.style.position = 'relative';
    el.appendChild(overlay);
  },

  unblock(target = 'body') {
    const el = typeof target === 'string' ? document.querySelector(target) : target;
    if (!el) return;
    const overlay = el.querySelector('.bkn-blockui-overlay');
    if (overlay) overlay.remove();
  },

  blockPage(options = {}) {
    const existing = document.getElementById('bkn-blockui-page');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'bkn-blockui-page';
    overlay.innerHTML = `<div class="bkn-spinner bkn-spinner--v2 bkn-spinner--${options.spinner || 'primary'}" style="position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);z-index:1100;"></div>`;
    overlay.style.cssText = `position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(255,255,255,0.9);z-index:1050;`;
    document.body.appendChild(overlay);
    document.body.style.overflow = 'hidden';
  },

  unblockPage() {
    const overlay = document.getElementById('bkn-blockui-page');
    if (overlay) overlay.remove();
    document.body.style.overflow = '';
  },

  initPopover(el, options = {}) {
    if (!el) return;

    const trigger = options.trigger || 'click';

    function show() {
      const existing = document.querySelector('.bkn-popover-shim');
      if (existing) existing.remove();

      const popover = document.createElement('div');
      popover.className = 'bkn-popover-shim';
      popover.innerHTML = options.content || '';
      popover.style.cssText = `position:fixed;z-index:1060;background:var(--bg-dropdown, #fff);color:var(--text-primary, #181C32);border:1px solid var(--border-color, #EBEDF3);border-radius:1.5rem;padding:16px;box-shadow:var(--shadow-dropdown, 0 0.5rem 3rem 0rem rgba(0,0,0,0.26));max-width:300px;`;

      const rect = el.getBoundingClientRect();
      popover.style.top = `${rect.bottom + 8}px`;
      popover.style.left = `${rect.left + rect.width / 2}px`;
      popover.style.transform = 'translateX(-50%)';

      document.body.appendChild(popover);
      return popover;
    }

    if (trigger === 'hover') {
      let popover = null;

      el.addEventListener('mouseenter', function onMouseEnter() {
        popover = show();
      });

      el.addEventListener('mouseleave', function onMouseLeave() {
        if (popover) {
          popover.remove();
          popover = null;
        }
      });
    } else {
      el.addEventListener('click', function onClick() {
        const popover = show();

        const close = (e) => {
          if (!popover.contains(e.target) && e.target !== el) {
            popover.remove();
            document.removeEventListener('click', close);
          }
        };
        setTimeout(() => document.addEventListener('click', close), 0);
      });
    }
  }
};

export const UiUtil = {
  isMobileDevice() {
    return window.innerWidth < 768;
  },

  isRTL() {
    return document.documentElement.getAttribute('dir') === 'rtl';
  },

  scrollTop(target = 0, duration = 300) {
    window.scrollTo({ top: target, behavior: duration > 0 ? 'smooth' : 'auto' });
  },

  getById(id) {
    return document.getElementById(id);
  },

  css(el, property, value) {
    if (!el) return;
    if (value === undefined) return getComputedStyle(el)[property];
    el.style[property] = value;
  },

  btnWait(el) {
    if (!el) return;
    if (el instanceof Event) el = el.currentTarget;
    if (typeof el === 'string') el = document.querySelector(el);
    if (!el) return;
    el._originalHtml = el.innerHTML;
    el.innerHTML = '<i class="spinner spinner-sm"></i>';
    el.disabled = true;
  },

  btnRelease(el) {
    if (!el) return;
    if (el instanceof Event) el = el.currentTarget;
    if (typeof el === 'string') el = document.querySelector(el);
    if (!el) return;
    if (el._originalHtml) el.innerHTML = el._originalHtml;
    el.disabled = false;
  }
};

export class UiWizard {
  constructor(el, options = {}) {
    this.el = typeof el === 'string' ? document.querySelector(el) : el;
    this.options = options;
    this.currentStep = options.startStep || options.initialStep || 1;
    this.totalSteps = this.el?.querySelectorAll('.wizard-step').length || 0;
    this._events = {};
    this._pendingStep = null;

    this._wireActions();

    this.goTo(this.currentStep, true);
  }

  _wireActions() {
    this.el?.querySelectorAll('[data-wizard-type="action-next"]').forEach(btn => {
      btn.addEventListener('click', () => this.next());
    });
    this.el?.querySelectorAll('[data-wizard-type="action-prev"]').forEach(btn => {
      btn.addEventListener('click', () => this.previous());
    });
    this.el?.querySelectorAll('[data-wizard-type="action-submit"]').forEach(btn => {
      btn.addEventListener('click', () => this.submit());
    });
    if (this.options.clickableSteps !== false) {
      this.el?.querySelectorAll('[data-wizard-type="step"]').forEach((stepEl, i) => {
        stepEl.addEventListener('click', () => this.goTo(i + 1));
      });
    }
  }

  on(event, callback) {
    if (!this._events[event]) this._events[event] = [];
    this._events[event].push(callback);
    return this;
  }

  _fire(event, ...args) {
    if (!this._events[event]) return undefined;
    for (const cb of this._events[event]) {
      const result = cb.apply(this, args);
      if (result === false) return false;
    }
    return undefined;
  }

  getStep() {
    return this.currentStep;
  }

  getNewStep() {
    return this._pendingStep || this.currentStep;
  }

  goTo(step, skipEvents = false) {
    if (step < 1 || step > this.totalSteps) return;

    if (!skipEvents && step !== this._pendingStep) {
      this._pendingStep = step;
      if (this._fire('change', this) === false) return;
    }

    this._pendingStep = null;
    this.currentStep = step;

    // Update nav step states in sidebar
    this.el?.querySelectorAll('[data-wizard-type="step"]').forEach((s, i) => {
      s.setAttribute('data-wizard-state', i + 1 === step ? 'current' : i + 1 < step ? 'completed' : '');
    });

    // Show/hide step content sections
    this.el?.querySelectorAll('[data-wizard-type="step-content"]').forEach((s, i) => {
      s.setAttribute('data-wizard-state', i + 1 === step ? 'current' : '');
    });

    // Set wizard root state for action button visibility via CSS
    if (this.el) {
      if (step === 1) {
        this.el.setAttribute('data-wizard-state', 'first');
      } else if (step === this.totalSteps) {
        this.el.setAttribute('data-wizard-state', 'last');
      } else {
        this.el.setAttribute('data-wizard-state', 'between');
      }
    }

    if (!skipEvents) {
      this._fire('changed', this);
    }

    if (this.options.onStep) {
      this.options.onStep({ currentStep: step });
    }
  }

  next() {
    if (this.currentStep < this.totalSteps) {
      this.goTo(this.currentStep + 1);
    }
  }

  previous() {
    if (this.currentStep > 1) {
      this.goTo(this.currentStep - 1);
    }
  }

  submit() {
    this._fire('submit', this);
  }

  stop() {
  }
}
