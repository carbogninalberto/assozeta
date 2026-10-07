import test from 'node:test';
import assert from 'node:assert/strict';
import {uploadCertificate, validateCertificateFile, MAX_CERTIFICATE_BYTES, expirationFromResponse} from './medicalCertificateUpload.js';

const file = () => new File(['%PDF-1.4\nDEMO'], 'documento-demo.pdf', {type: 'application/pdf'});
test('upload sends the actual selected file as medical_certificate multipart data', async () => {
    const input = file();
    let request;
    const response = await uploadCertificate({file: input, url: '/api/subscription/fixture/medical-certificate/upload',
        api: async (url, options) => { request = {url, ...options}; return {status: 200, error: false, response: {medical: 'document-id'}}; }});
    assert.equal(response.medical, 'document-id');
    assert.equal(request.method, 'POST');
    assert.ok(request.body instanceof FormData);
    assert.equal(request.body.get('medical_certificate').name, input.name);
    assert.equal(await request.body.get('medical_certificate').text(), await input.text());
    assert.equal(request.headers, undefined); // Browser sets the multipart boundary.
});

test('invalid selection is rejected before any backend request', async () => {
    let calls = 0;
    const api = async () => { calls++; };
    for (const input of [null, new File([], 'empty.pdf', {type: 'application/pdf'}),
        new File(['script'], 'unsupported.js', {type: 'text/javascript'}),
        new File([new Uint8Array(MAX_CERTIFICATE_BYTES + 1)], 'large.pdf', {type: 'application/pdf'})])
        await assert.rejects(uploadCertificate({file: input, url: '/upload', api}));
    assert.equal(calls, 0);
});

test('failed or unattached responses cannot be presented as a completed upload', async () => {
    for (const response of [undefined, null, {status: 403, error: true, response: {}},
        {status: 200, error: false, response: {}}, {status: 500, error: true, response: {error: 'Storage unavailable'}}])
        await assert.rejects(uploadCertificate({file: file(), url: '/upload', api: async () => response}));
    await assert.rejects(uploadCertificate({file: file(), url: '/upload', api: async () => {throw new Error('Network failed');}}));
});

test('optional server expiration is displayed in the format accepted by the date handler', () => {
    assert.equal(expirationFromResponse('2027-09-30'), '30/09/2027');
    assert.equal(expirationFromResponse('30/09/2027'), '30/09/2027');
    assert.equal(expirationFromResponse(null), '');
    assert.equal(expirationFromResponse({value: '2027-09-30'}), '');
});

test('expiration requires an actual calendar day, including leap years', () => {
    for (const value of ['2027-02-29', '31/04/2027', '2027-13-01', '00/09/2027', '1/9/2027', '', '2027-09-30T12:00:00Z'])
        assert.equal(expirationFromResponse(value), '', value);
    assert.equal(expirationFromResponse('2028-02-29'), '29/02/2028');
    assert.equal(expirationFromResponse('29/02/2028'), '29/02/2028');
});

test('PDFs and images at the size limit are accepted without substituting the selected file', () => {
    const image = new File(['image'], 'certificato.png', {type: 'image/png'});
    const limit = new File([new Uint8Array(MAX_CERTIFICATE_BYTES)], 'certificato.pdf', {type: 'application/pdf'});
    assert.equal(validateCertificateFile(image), image);
    assert.equal(validateCertificateFile(limit), limit);
});

test('cancellation propagates its signal and never returns an uploaded certificate', async () => {
    const controller = new AbortController();
    let receivedSignal;
    const pending = uploadCertificate({file: file(), url: '/upload', signal: controller.signal,
        api: async (url, options) => {
            receivedSignal = options.signal;
            return await new Promise((resolve, reject) => {
                options.signal.addEventListener('abort', () => reject(new DOMException('Cancelled', 'AbortError')), {once: true});
            });
        }});
    controller.abort();
    await assert.rejects(pending, {name: 'AbortError'});
    assert.equal(receivedSignal, controller.signal);
});

test('backend failure text remains visible to the caller', async () => {
    await assert.rejects(uploadCertificate({file: file(), url: '/upload',
        api: async () => ({status: 500, error: true, response: {error: 'Storage unavailable'}})}),
    {message: 'Storage unavailable'});
});
