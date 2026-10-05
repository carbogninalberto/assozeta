import test from 'node:test';
import assert from 'node:assert/strict';
import {attachManualSection, manualLinkUrl, manualChapters} from './manualPresentation.js';
import AgentWebSocket from '../../utils/AgentWebSocket.js';

const section = {id: 'docs/tag#create', title: 'Creare un tag', reader: [], screenshots: [], embedded_url: '/#/manuale?section=docs%2Ftag%23create'};

test('chapter reading keeps every section and uses source order rather than search rank', () => {
    const chapters = manualChapters([
        {id: 'docs/corsi#modifica', page: 'docs/corsi', page_title: 'Corsi', page_order: 1, section_order: 3},
        {id: 'docs/libro-soci#ricerca', page: 'docs/libro-soci', page_title: 'Libro Soci', page_order: 0, section_order: 2},
        {id: 'docs/corsi#crea', page: 'docs/corsi', page_title: 'Corsi', page_order: 1, section_order: 1},
    ]);
    assert.deepEqual(chapters.map(chapter => chapter.page), ['docs/libro-soci', 'docs/corsi']);
    assert.deepEqual(chapters[1].sections.map(section => section.id), ['docs/corsi#crea', 'docs/corsi#modifica']);
});

test('manual events attach only to their active response and leave ordinary Markdown intact', () => {
    const client = new AgentWebSocket();
    let messages = [{id: 4, role: 'agent', streaming: true, content: '**Apri** Iscrizioni.'}];
    let activeId = 4;
    client.setOnManualSection(guide => { messages = attachManualSection(messages, activeId, guide); });
    client.setOnMessageEnd(() => { messages = messages.map(message => ({...message, streaming: false})); activeId = null; });
    client.handleMessage({type: 'manual_section', section});
    client.handleMessage({type: 'message_end'});
    assert.equal(messages[0].content, '**Apri** Iscrizioni.');
    assert.deepEqual(messages[0].manualSection, section);
    const completed = messages;
    client.handleMessage({type: 'manual_section', section: {...section, title: 'Late event'}});
    assert.equal(messages, completed);
    assert.equal(attachManualSection([{id: 4, role: 'user', streaming: true}], 4, section)[0].manualSection, undefined);
    assert.equal(attachManualSection(completed, 4, section)[0].manualSection.title, 'Creare un tag');
});

test('manual links allow embedded navigation and reject executable or deceptive URLs', () => {
    for (const href of ['/#/manuale?section=docs%2Ftag', '#passaggio', 'https://manuale.bakney.com/docs/tag']) {
        assert.equal(manualLinkUrl(href), href);
    }
    assert.equal(manualLinkUrl('/docs/ricevute#numerazione-progressiva'), '/#/manuale?section=docs%2Fricevute%23numerazione-progressiva');
    assert.equal(manualLinkUrl('/docs/tag'), '/#/manuale?page=docs%2Ftag');
    assert.equal(manualLinkUrl('impostazioni', 'docs/introduzione'), '/#/manuale?page=docs%2Fimpostazioni');
    assert.equal(manualLinkUrl('#creare', 'docs/tag'), '/#/manuale?section=docs%2Ftag%23creare');
    for (const href of ['javascript:alert(1)', 'data:text/html,hello', '//evil.test', '/\\evil.test', '\njavascript:alert(1)']) {
        assert.equal(manualLinkUrl(href), null);
    }
});
