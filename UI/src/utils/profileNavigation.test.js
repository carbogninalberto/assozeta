import test from 'node:test';
import assert from 'node:assert/strict';
import {readProfileLocation, navigateProfile, profileTab} from './profileNavigation.js';

test('profile links accept reordered parameters and reject unknown pages', () => {
    assert.deepEqual(readProfileLocation('#/profile?tab=restore&page=data-management'),{page:'data-management',tab:'restore'});
    assert.equal(readProfileLocation('#/profile?page=unknown').page,null);
    assert.equal(readProfileLocation('#/courses?page=data-management').page,null);
});

test('profile navigation preserves the deployment path and changes tab history', () => {
    const location=new URL('https://local.test/app/?lang=it#/profile?page=data-management&tab=restore');
    const changes=[];
    const events=[];
    globalThis.window={location,history:{state:{key:1},pushState(state,_,url){changes.push('push');location.href=new URL(url,location).href;},
        replaceState(state,_,url){changes.push('replace');location.href=new URL(url,location).href;}},dispatchEvent(event){events.push(event);}};
    globalThis.HashChangeEvent=class {constructor(type,values){this.type=type;Object.assign(this,values);}};
    try {
        assert.equal(profileTab('data-management',['export','restore'],'export'),'restore');
        navigateProfile('data-management','export');
        assert.equal(location.pathname,'/app/');
        assert.equal(location.search,'?lang=it');
        assert.equal(location.hash,'#/profile?page=data-management&tab=export');
        navigateProfile('password');
        assert.equal(location.hash,'#/profile?page=password');
        navigateProfile('password');
        assert.deepEqual(changes,['push','push']);
        assert.equal(events.length,2);
        navigateProfile('self-instance','updates',true);
        assert.equal(profileTab('self-instance',['overview','updates'],'overview'),'updates');
        assert.equal(changes.at(-1),'replace');
    } finally {delete globalThis.window;delete globalThis.HashChangeEvent;}
});
