import test from 'node:test';
import assert from 'node:assert/strict';

import {filterMentionSuggestions} from './mentionSuggestions.js';


const items = [
    {label: 'Nome', value: 'associate.first_name', aliases: ['nome']},
    {label: 'Data Odierna', value: 'other.today', aliases: ['dataodierna']},
    {label: 'Lista corsi', value: 'other.courses_list', aliases: ['listacorsi']},
];

test('mention search accepts compact legacy command names', () => {
    assert.deepEqual(
        filterMentionSuggestions(items, 'dataodierna').map(item => item.value),
        ['other.today'],
    );
    assert.deepEqual(
        filterMentionSuggestions(items, '@listacorsi').map(item => item.value),
        ['other.courses_list'],
    );
});

test('mention search keeps normal labels case and whitespace insensitive', () => {
    assert.deepEqual(
        filterMentionSuggestions(items, 'DATA ODIERNA').map(item => item.value),
        ['other.today'],
    );
});
