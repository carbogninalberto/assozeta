// Publication and planning must resolve precisely the same reviewed candidates.
import {referenceSections} from './reference-recipes.mjs';
import {overviewReferencePages} from './overview-recipes.mjs';
import {authoredReferenceSections} from './authored-reference-recipes.mjs';

export function referenceCatalogue(source) {
    const sections = [
        ...referenceSections(source),
        ...overviewReferencePages(source),
        ...authoredReferenceSections(source),
    ];
    if (new Set(sections.map(section => section.path + '#' + section.id)).size !== sections.length)
        throw new Error('Duplicate reviewed reference section');
    return sections;
}
