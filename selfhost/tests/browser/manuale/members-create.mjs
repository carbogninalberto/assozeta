// Real wizard, backend writes, and persisted state. No simulated API success.
import {scenario} from './scenario.mjs';
import {memberCreationSources} from './member-sources.mjs';
import {createMember} from './member-create-flow.mjs';

await scenario({id: 'members-create', prefix: 'images/faq/creazione-socio',
    sources: memberCreationSources, actions: createMember});
