export function initSelectpicker(el, options = {}) {
  if (!el) return;
  el.classList.remove('selectpicker');
  el.classList.add('selectpicker-shim');

  if (options.liveSearch) {
    const wrapper = document.createElement('div');
    wrapper.className = 'select-shim-wrapper';
    el.parentNode.insertBefore(wrapper, el);
    wrapper.appendChild(el);

    if (el.options.length > 10 && el.getAttribute('size') !== '1') {
      enhanceSearchableSelect(el);
    }
  }
}

function enhanceSearchableSelect(select) {
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'form-control select-shim-filter mb-1';
  input.placeholder = 'Cerca...';
  select.parentNode.insertBefore(input, select);

  input.addEventListener('input', () => {
    const filter = input.value.toLowerCase();
    [...select.options].forEach(opt => {
      opt.style.display = opt.text.toLowerCase().includes(filter) ? '' : 'none';
    });
  });

  select.addEventListener('change', () => {
    input.value = select.options[select.selectedIndex]?.text || '';
  });
}

export function refreshSelectpicker(el) {
  if (!el) return;
}

export function destroySelectpicker(el) {
  if (!el) return;
  el.classList.remove('selectpicker-shim');
  const filter = el.parentNode?.querySelector('.select-shim-filter');
  if (filter) filter.remove();
}
