import test from 'node:test';
import assert from 'node:assert/strict';
import {FormValidation} from './form-validation.js';

const classes = () => ({remove() {}, toggle() {}});
function formField(value) {
    const field = {value, type: 'text', tagName: 'INPUT', classList: classes(),
        addEventListener() {}, removeEventListener() {}, closest() {return null;}};
    const form = {querySelectorAll(selector) {return selector.startsWith('[name=') ? [field] : [];}};
    return {field, form};
}

test('registered validators are chainable and validate changing input with their configured options', async () => {
    globalThis.window = {};
    globalThis.document = {createElement() {return {dataset: {}, appendChild() {}};}};
    try {
        const {field, form} = formField('17');
        const validation = FormValidation.formValidation(form, {fields: {age: {validators: {adult: {minimum: 18}}}}});
        const seen = [];
        assert.equal(validation.registerValidator('adult', () => ({async validate(input) {
            seen.push(input);
            return {valid: Number(input.value) >= input.options.minimum};
        }})), validation);
        assert.equal(await validation.validate(), 'Invalid');
        field.value = '18';
        assert.equal(await validation.validate(), 'Valid');
        assert.deepEqual(seen.map(input => input.value), ['17', '18']);
        assert.equal(seen[0].field, 'age');
        assert.equal(seen[0].element, field);
        validation.destroy();
    } finally {
        delete globalThis.window;
        delete globalThis.document;
    }
});

test('invalid custom validator registrations fail immediately', () => {
    const validation = FormValidation.formValidation(null);
    assert.throws(() => validation.registerValidator('adult', () => ({})), TypeError);
});

test('native date fields validate ISO values while text dates retain their configured format', async () => {
    globalThis.window = {};
    globalThis.document = {createElement() {return {dataset: {}, appendChild() {}};}};
    try {
        const {field, form} = formField('2028-02-29');
        field.type = 'date';
        const validation = FormValidation.formValidation(form, {fields: {born: {validators: {date: {format: 'DD/MM/YYYY'}}}}});
        assert.equal(await validation.validate(), 'Valid');
        field.value = '2027-02-29';
        assert.equal(await validation.validate(), 'Invalid');
        field.type = 'text';
        field.value = '29/02/2028';
        assert.equal(await validation.validate(), 'Valid');
        field.value = '2028-02-29';
        assert.equal(await validation.validate(), 'Invalid');
        validation.destroy();
    } finally {delete globalThis.window; delete globalThis.document;}
});
