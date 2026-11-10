import test from 'node:test';
import assert from 'node:assert/strict';
import {deleteBody, keepBody, remoteSettingsBody, remoteToggleBody, remoteTestBody, keyBody, keyConfirmBody, apiErrorMessage} from './backupPassword.js';

test('deleting a backup sends the owner password', () => {
    assert.deepEqual(deleteBody({name: 'a'}, 'pw'), {confirm: false, password: 'pw'});
    assert.deepEqual(deleteBody({keep: true}, 'pw'), {confirm: true, password: 'pw'});
    assert.deepEqual(deleteBody({last_upgrade: true}, 'pw'), {confirm: true, password: 'pw'});
});

test('keeping is one click, un-keeping confirms the owner password', () => {
    assert.deepEqual(keepBody(true, 'pw'), {keep: true});
    assert.deepEqual(keepBody(true), {keep: true});
    assert.deepEqual(keepBody(false, 'pw'), {keep: false, password: 'pw'});
});

test('remote settings carry the password, the connection test never does', () => {
    const draft = {endpoint: 'https://s3.example.com', bucket: 'b', access_key: 'k', secret_key: '', retention: '5', password: 'stale'};
    const body = remoteSettingsBody(draft, 'pw');
    assert.equal(body.password, 'pw');
    assert.equal(body.retention, 5);
    assert.equal('secret_key' in body, false);
    assert.equal(remoteSettingsBody({...draft, secret_key: 's'}, 'pw').secret_key, 's');

    const probe = remoteTestBody(draft);
    assert.equal('password' in probe, false);
    assert.equal('secret_key' in probe, false);
    assert.equal(probe.bucket, 'b');
    assert.deepEqual(remoteTestBody(null), {});
    assert.equal(draft.password, 'stale', 'the draft is not changed');
});

test('the remote switch and the key wizard send the owner password', () => {
    assert.deepEqual(remoteToggleBody(true, 'pw'), {enabled: true, password: 'pw'});
    assert.deepEqual(remoteToggleBody(false, 'pw'), {enabled: false, password: 'pw'});
    assert.deepEqual(keyBody('pw'), {password: 'pw'});
    assert.deepEqual(keyConfirmBody('abcd1234', ' x1y2z3 ', 'pw'), {key_id: 'abcd1234', tail: 'x1y2z3', password: 'pw'});
});

test('API errors read well, including field errors', () => {
    assert.equal(apiErrorMessage({error: 'Password non corretta.'}), 'Password non corretta.');
    assert.equal(apiErrorMessage({error: 'Troppi tentativi. Riprova tra qualche minuto.'}), 'Troppi tentativi. Riprova tra qualche minuto.');
    assert.equal(apiErrorMessage({detail: 'Non trovato.'}), 'Non trovato.');
    assert.equal(apiErrorMessage({password: ['This field is required.']}), 'Password: campo obbligatorio.');
    assert.equal(apiErrorMessage({endpoint: ['Usa un indirizzo https://.']}), 'Endpoint: Usa un indirizzo https://.');
    assert.equal(apiErrorMessage({bucket: ['Nome non valido.']}), 'Nome non valido.');
    assert.equal(apiErrorMessage({bucket: ['This field is required.'], access_key: ['This field is required.']}), 'Compila i campi obbligatori.');
    assert.equal(apiErrorMessage({non_field_errors: ['Errore.']}), 'Errore.');
    assert.equal(apiErrorMessage(null), 'Operazione non riuscita. Riprova.');
    assert.equal(apiErrorMessage('<html>bad gateway</html>'), 'Operazione non riuscita. Riprova.');
    assert.equal(apiErrorMessage({}), 'Operazione non riuscita. Riprova.');
});
