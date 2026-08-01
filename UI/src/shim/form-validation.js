import moment from 'moment';

class Trigger {}
class Bootstrap {}
class SubmitButton {}
class DefaultSubmit {}
class PasswordStrength {}

class Excluded {
  constructor(options = {}) {
    this.options = options;
  }

  isExcluded(field, element, elements) {
    const excluded = this.options.excluded;
    if (typeof excluded === 'function') {
      return excluded(field, element, elements);
    }
    return excluded === true;
  }
}

function getFieldElements(form, field) {
  if (!form || !field) return [];

  const escapedField = window.CSS?.escape ? CSS.escape(field) : field.replace(/"/g, '\\"');
  return [
    ...form.querySelectorAll(`[name="${escapedField}"]`),
    ...form.querySelectorAll(`#${escapedField}`),
  ].filter((element, index, elements) => element && elements.indexOf(element) === index);
}

function getElementValue(element) {
  if (!element) return '';
  if (element.type === 'checkbox') return element.checked ? element.value || 'on' : '';
  if (element.type === 'radio') {
    const checked = element.form?.querySelector(`[name="${element.name}"]:checked`);
    return checked?.value || '';
  }
  if (element.tagName === 'SELECT' && element.multiple) {
    return [...element.selectedOptions].map(option => option.value);
  }
  return element.value ?? '';
}

function isEmpty(value) {
  if (Array.isArray(value)) return value.length === 0;
  return value === undefined || value === null || String(value).trim() === '';
}

function getMessage(options, fallback) {
  return options?.message || fallback;
}

function buildErrorElement(message) {
  const container = document.createElement('div');
  container.className = 'fv-plugins-message-container fv-plugins-message-container--enabled invalid-feedback';

  const block = document.createElement('div');
  block.className = 'fv-help-block';
  block.innerHTML = message;
  container.appendChild(block);

  return container;
}

function findMessageAnchor(element) {
  return element.closest('.input-group') || element.closest('.form-group') || element.parentElement;
}

function clearFieldState(elements) {
  elements.forEach(element => {
    element.classList.remove('is-invalid', 'is-valid');
    const formGroup = element.closest('.form-group');
    formGroup?.classList.remove('is-invalid', 'is-valid');

    const anchor = findMessageAnchor(element);
    anchor?.querySelectorAll('.fv-plugins-message-container[data-shim-validation="true"]').forEach(node => node.remove());
  });
}

function showFieldState(elements, valid, message) {
  clearFieldState(elements);

  elements.forEach(element => {
    element.classList.toggle('is-invalid', !valid);
    element.classList.toggle('is-valid', valid);
    const formGroup = element.closest('.form-group');
    formGroup?.classList.toggle('is-invalid', !valid);
    formGroup?.classList.toggle('is-valid', valid);
  });

  if (!valid && elements[0] && message) {
    const anchor = findMessageAnchor(elements[0]);
    const errorElement = buildErrorElement(message);
    errorElement.dataset.shimValidation = 'true';
    anchor?.appendChild(errorElement);
  }
}

function normalizeRegExp(pattern, flags) {
  if (pattern instanceof RegExp) return pattern;
  return new RegExp(pattern, flags || undefined);
}

function validateDate(value, options) {
  if (isEmpty(value)) return true;
  if (options?.format) return moment(String(value), options.format, true).isValid();
  return !Number.isNaN(Date.parse(value));
}

function validateInteger(value, options = {}) {
  if (isEmpty(value)) return true;
  let normalized = String(value).trim();
  if (options.thousandsSeparator) normalized = normalized.split(options.thousandsSeparator).join('');
  if (options.decimalSeparator) normalized = normalized.replace(options.decimalSeparator, '.');
  return Number.isFinite(Number(normalized));
}

function validatePhone(value) {
  if (isEmpty(value)) return true;
  return /^\+?[0-9\s().-]{7,20}$/.test(String(value).trim());
}

function validateVat(value) {
  if (isEmpty(value)) return true;
  return /^[0-9]{11}$/.test(String(value).trim());
}

function validateRemoteResponse(response) {
  if (typeof response === 'boolean') return response;
  if (!response || typeof response !== 'object') return true;

  const candidates = [
    response.valid,
    response.available,
    response.success,
    response.data?.valid,
    response.data?.available,
    response.data?.success,
  ];

  const explicit = candidates.find(value => typeof value === 'boolean');
  return explicit ?? true;
}

async function validateRemote(field, value, options = {}) {
  if (isEmpty(value) || !options.url) return true;

  const method = (options.method || 'GET').toUpperCase();
  const request = {method, headers: options.headers || {}};
  let url = options.url;

  if (method === 'GET') {
    const remoteUrl = new URL(url, window.location.origin);
    remoteUrl.searchParams.set(field, value);
    url = options.url.startsWith('http') ? remoteUrl.toString() : `${remoteUrl.pathname}${remoteUrl.search}${remoteUrl.hash}`;
  } else {
    request.headers = {'Content-Type': 'application/json', ...request.headers};
    request.body = JSON.stringify({[field]: value});
  }

  let result;
  try {
    result = await window.fetch(url, request);
  } catch (error) {
    return false;
  }

  if (!result.ok) return false;

  const contentType = result.headers.get('content-type') || '';
  if (!contentType.includes('application/json')) return true;

  return validateRemoteResponse(await result.json());
}

class ValidationInstance {
  constructor(form, options = {}) {
    this.form = typeof form === 'string' ? document.querySelector(form) : form;
    this.options = options;
    this.fields = options.fields || {};
    this.listeners = [];
    this.events = new Map();
    this.excludedPlugin = Object.values(options.plugins || {}).find(plugin => plugin instanceof Excluded);

    this.bindTriggers();
  }

  bindTriggers() {
    Object.keys(this.fields).forEach(field => {
      getFieldElements(this.form, field).forEach(element => {
        const listener = () => clearFieldState([element]);
        element.addEventListener('input', listener);
        element.addEventListener('change', listener);
        this.listeners.push(() => {
          element.removeEventListener('input', listener);
          element.removeEventListener('change', listener);
        });
      });
    });
  }

  isFieldExcluded(field, elements) {
    const [element] = elements;
    const fieldConfig = this.fields[field] || {};
    if (fieldConfig.enabled === false) return true;
    if (!element) return true;
    if (element.disabled) return true;
    return this.excludedPlugin?.isExcluded(field, element, elements) === true;
  }

  async validateField(field) {
    const fieldConfig = this.fields[field] || {};
    const validators = fieldConfig.validators || {};
    const elements = getFieldElements(this.form, field);

    if (this.isFieldExcluded(field, elements)) {
      clearFieldState(elements);
      return 'NotValidated';
    }

    const [element] = elements;
    const value = getElementValue(element);

    for (const [validator, options] of Object.entries(validators)) {
      let valid = true;
      const input = {field, element, elements, value};

      if (validator === 'notEmpty') valid = !isEmpty(value);
      else if (validator === 'regexp') {
        const regexp = normalizeRegExp(options.regexp, options.flags);
        regexp.lastIndex = 0;
        valid = isEmpty(value) || regexp.test(String(value));
      }
      else if (validator === 'emailAddress') valid = isEmpty(value) || /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(value).trim());
      else if (validator === 'date') valid = validateDate(value, options);
      else if (validator === 'stringLength') {
        const length = String(value || '').length;
        valid = isEmpty(value) || ((options.min === undefined || length >= options.min) && (options.max === undefined || length <= options.max));
      } else if (validator === 'identical') {
        const compare = typeof options.compare === 'function' ? options.compare() : options.compare;
        valid = String(value) === String(compare ?? '');
      } else if (validator === 'integer') valid = validateInteger(value, options);
      else if (validator === 'phone') valid = validatePhone(value);
      else if (validator === 'vat') valid = validateVat(value);
      else if (validator === 'callback' && typeof options.callback === 'function') {
        const result = await options.callback(input);
        valid = typeof result === 'object' && result !== null && 'valid' in result ? result.valid : result;
      } else if (validator === 'remote') {
        valid = await validateRemote(field, value, options);
      }

      if (!valid) {
        showFieldState(elements, false, getMessage(options, 'Campo non valido.'));
        this.emit('core.field.invalid', {field, element, validator});
        return 'Invalid';
      }
    }

    showFieldState(elements, true);
    this.emit('core.field.valid', {field, element});
    return 'Valid';
  }

  async validate() {
    let valid = true;

    for (const field of Object.keys(this.fields)) {
      const status = await this.validateField(field);
      if (status === 'Invalid') valid = false;
    }

    const status = valid ? 'Valid' : 'Invalid';
    this.emit(valid ? 'core.form.valid' : 'core.form.invalid', {form: this.form});
    return status;
  }

  revalidateField(field) {
    return this.validateField(field);
  }

  resetForm(resetValues = false) {
    Object.keys(this.fields).forEach(field => clearFieldState(getFieldElements(this.form, field)));
    if (resetValues) this.form?.reset?.();
    return this;
  }

  on(eventName, handler) {
    const handlers = this.events.get(eventName) || [];
    handlers.push(handler);
    this.events.set(eventName, handlers);
    return this;
  }

  emit(eventName, payload) {
    (this.events.get(eventName) || []).forEach(handler => handler(payload));
  }

  destroy() {
    this.listeners.forEach(remove => remove());
    this.listeners = [];
    this.resetForm(false);
  }
}

export const FormValidation = {
  formValidation(form, options) {
    return new ValidationInstance(form, options);
  },
  plugins: {
    Bootstrap,
    DefaultSubmit,
    Excluded,
    PasswordStrength,
    SubmitButton,
    Trigger,
  },
};

export default FormValidation;
