const instances = new WeakMap();

export function createDropzone(el, options = {}) {
  if (!el) return null;
  const input = document.createElement('input');
  input.type = 'file';
  input.multiple = options.multiple ?? true;
  input.style.display = 'none';
  input.accept = options.accept || '';
  el.appendChild(input);

  el.classList.add('dropzone-shim');
  el.setAttribute('draggable', 'true');

  const dz = {
    files: [],
    _handlers: {},
    _input: input,
    _el: el,

    on: function (event, handler) {
      this._handlers[event] = this._handlers[event] || [];
      this._handlers[event].push(handler);
      return this;
    },

    emit: function (event, ...args) {
      (this._handlers[event] || []).forEach(handler => handler(...args));
    },

    addFile: function (file) {
      this.files.push(file);
      this.emit('addedfile', file);
    },

    removeAllFiles: function () {
      this.files = [];
      el.querySelectorAll('.dropzone-shim-preview').forEach(p => p.remove());
    },

    destroy: function () {
      el.classList.remove('dropzone-shim');
      el.removeAttribute('draggable');
      if (input.parentNode) input.remove();
      instances.delete(el);
    }
  };

  instances.set(el, dz);

  el.addEventListener('click', () => input.click());

  input.addEventListener('change', () => {
    [...input.files].forEach(file => {
      dz.addFile(file);
    });
    dz.emit('queuecomplete');
  });

  el.addEventListener('dragover', (e) => {
    e.preventDefault();
    el.classList.add('dropzone-shim-dragover');
  });
  el.addEventListener('dragleave', () => {
    el.classList.remove('dropzone-shim-dragover');
  });
  el.addEventListener('drop', (e) => {
    e.preventDefault();
    el.classList.remove('dropzone-shim-dragover');
    [...e.dataTransfer.files].forEach(file => {
      dz.addFile(file);
    });
    dz.emit('queuecomplete');
  });

  return dz;
}

export function getDropzone(el) {
  const target = typeof el === 'string' ? document.querySelector(el) : el;
  return target ? instances.get(target) : undefined;
}

export function destroyDropzone(dz) {
  if (dz && dz.destroy) dz.destroy();
}
