// Publication and planning must resolve precisely the same reviewed candidates.
import {referencePages} from './reference-recipes.mjs';
import {overviewReferencePages} from './overview-recipes.mjs';
import {authoredReferenceSections} from './authored-reference-recipes.mjs';

export function referenceCatalogue(source) {
    const sections = [
        ...referencePages(source).flatMap(page => page.sections.map(section => ({
            ...section, path: page.path, title: section.body.match(/^#{1,6} +(.+)$/m)[1],
        }))),
        ...overviewReferencePages(source),
        ...authoredReferenceSections(source),
    ];
    if (new Set(sections.map(section => section.path + '#' + section.id)).size !== sections.length)
        throw new Error('Duplicate reviewed reference section');
    return sections;
}
