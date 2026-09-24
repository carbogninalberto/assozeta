import test from 'node:test';
import assert from 'node:assert/strict';
import {buildVersion, assetVersion} from './build-version.js';

test('production release tags are displayed unchanged and HTML queries remain numeric', () => {
    for (const tag of ['v1.2.3', '1.2.3', 'v2.0.0-rc.1']) assert.equal(buildVersion(tag, 'production'), tag);
    assert.equal(assetVersion('v1.30.245'), '010300245');
});

test('missing versions, branch builds and local development are unstable', () => {
    for (const value of [undefined, '', 'development', 'edge', 'main', 'a'.repeat(40), '0.138.1-1530-invalid!', 'v01.2.3']) {
        assert.equal(buildVersion(value, 'production'), 'unstable');
    }
    for (const environment of ['development', 'staging', undefined]) assert.equal(buildVersion('v1.2.3', environment), 'unstable');
    assert.equal(assetVersion('unstable'), '0');
});
