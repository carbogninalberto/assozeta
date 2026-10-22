// Real wizard, backend writes, and persisted state. No simulated API success.
import {scenario} from './scenario.mjs';

import {createMember} from './member-create-flow.mjs';

await scenario({id: 'members-create', actions: createMember});
