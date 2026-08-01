export function $(selector, context = document) {
  if (typeof selector === 'string') {
    const el = context.querySelector(selector);
    return el;
  }
  return selector;
}

export function $$(selector, context = document) {
  return [...context.querySelectorAll(selector)];
}

export function addClass(el, ...classes) {
  if (!el) return;
  el.classList.add(...classes.filter(Boolean));
}

export function removeClass(el, ...classes) {
  if (!el) return;
  el.classList.remove(...classes.filter(Boolean));
}

export function toggleClass(el, className) {
  if (!el) return;
  el.classList.toggle(className);
}

export function hasClass(el, className) {
  if (!el) return false;
  return el.classList.contains(className);
}

export function css(el, prop, value) {
  if (!el) return;
  if (value === undefined) {
    return getComputedStyle(el)[prop];
  }
  el.style[prop] = value;
}

export function on(el, event, handler, options) {
  if (!el) return;
  el.addEventListener(event, handler, options);
  return () => el.removeEventListener(event, handler, options);
}

export function off(el, event, handler) {
  if (!el) return;
  el.removeEventListener(event, handler);
}

export function val(el, value) {
  if (!el) return;
  if (value === undefined) return el.value;
  el.value = value;
}

export function data(el, key, value) {
  if (!el) return;
  if (value === undefined) return el.dataset[key];
  el.dataset[key] = value;
}

export function html(el, content) {
  if (!el) return;
  if (content === undefined) return el.innerHTML;
  el.innerHTML = content;
}

export function trigger(el, eventName, detail) {
  if (!el) return;
  el.dispatchEvent(new CustomEvent(eventName, { detail, bubbles: true }));
}

export function ready(fn) {
  if (document.readyState !== 'loading') {
    fn();
  } else {
    document.addEventListener('DOMContentLoaded', fn);
  }
}
