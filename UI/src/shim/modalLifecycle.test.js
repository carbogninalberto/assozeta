import test from 'node:test';
import assert from 'node:assert/strict';


class FakeClassList {
    constructor(initial = '') {
        this.values = new Set(initial.split(/\s+/).filter(Boolean));
    }
    add(...values) { values.forEach(value => this.values.add(value)); }
    remove(...values) { values.forEach(value => this.values.delete(value)); }
    contains(value) { return this.values.has(value); }
}

class FakeElement {
    constructor(document, id = '', className = '') {
        this.document = document;
        this.id = id;
        this.classList = new FakeClassList(className);
        this.style = {};
        this.attributes = new Map();
        this.listeners = new Map();
    }
    set className(value) { this.classList = new FakeClassList(value); }
    get offsetHeight() { return 0; }
    setAttribute(name, value) { this.attributes.set(name, value); }
    getAttribute(name) { return this.attributes.get(name) ?? null; }
    addEventListener(type, handler) {
        const handlers = this.listeners.get(type) || [];
        handlers.push(handler);
        this.listeners.set(type, handlers);
    }
    removeEventListener(type, handler) {
        this.listeners.set(type, (this.listeners.get(type) || []).filter(item => item !== handler));
    }
    dispatchEvent(event) {
        (this.listeners.get(event.type) || []).forEach(handler => handler(event));
    }
    remove() { this.document.elements.delete(this.id); }
}

test('fade modal cleanup removes backdrop and body lock before hidden event', async () => {
    const elements = new Map();
    const document = {
        elements,
        body: null,
        getElementById(id) { return elements.get(id) || null; },
        createElement() { return new FakeElement(document); },
        addEventListener() {},
        removeEventListener() {},
    };
    document.body = new FakeElement(document, 'body');
    document.body.appendChild = element => elements.set(element.id, element);
    const modal = new FakeElement(document, 'editEventElement', 'modal fade');
    elements.set(modal.id, modal);

    globalThis.document = document;
    globalThis.CustomEvent = class CustomEvent {
        constructor(type) { this.type = type; }
    };
    const {showModal, hideModal} = await import(`./modal.js?test=${Date.now()}`);
    let hiddenState;
    modal.addEventListener('hidden.bs.modal', () => {
        hiddenState = {
            backdrop: document.getElementById('backdrop-editEventElement'),
            bodyLocked: document.body.classList.contains('modal-open'),
        };
    });

    showModal('editEventElement');
    assert.equal(document.body.classList.contains('modal-open'), true);
    assert.ok(document.getElementById('backdrop-editEventElement'));

    hideModal('editEventElement');
    modal.dispatchEvent({type: 'transitionend', propertyName: 'transform'});

    assert.deepEqual(hiddenState, {backdrop: null, bodyLocked: false});
});
