// Draft discovery only. Recorded reference status and reviewed hashes describe the
// trusted recipe descriptors; neither means current sources or prose are verified.
import {accountingBalanceEditorialContracts} from './accounting-balance-recipes.mjs';
import {paymentEditorialContracts} from './payment-recipes.mjs';
import {contentEditorialContracts} from './content-recipes.mjs';
import {instructorEditorialContracts} from './instructor-recipes.mjs';
import {attendanceCarnetEditorialContracts} from './attendance-carnet-recipes.mjs';
import {campsCalendarEditorialContracts} from './camps-calendar-recipes.mjs';
import {registrationFormsEditorialContracts} from './registration-forms-recipes.mjs';
import {organizationAccessEditorialContracts} from './organization-access-recipes.mjs';
import {dashboardEditorialContracts} from './dashboard-recipes.mjs';
import {memberEditorialContracts} from './member-recipes.mjs';
import {profileEditorialContracts} from './profile-recipes.mjs';
import {tagEditorialContracts} from './tag-recipes.mjs';
import {searchEditorialContracts} from './search-recipes.mjs';
import {referenceEditorialContracts} from './reference-recipes.mjs';
import {overviewEditorialContracts} from './overview-recipes.mjs';
import {
    communicationAuthEditorialSections, communicationAuthSourceContractKeys,
    communicationAuthSourceContracts, communicationAuthReviewedSources,
} from './communication-auth-reference-recipes.mjs';
import {
    memberAdditionalEditorialSections, memberAdditionalSourceContractKeys,
    memberAdditionalSourceContracts, memberAdditionalReviewedSources,
} from './member-additional-reference-recipes.mjs';
import {
    coursePaymentAdditionalEditorialSections, coursePaymentAdditionalSourceContractKeys,
    coursePaymentAdditionalSourceContracts, coursePaymentAdditionalReviewedSources,
} from './course-payment-additional-reference-recipes.mjs';
import {
    organizationAdditionalEditorialSections, organizationAdditionalSourceContractKeys,
    organizationAdditionalSourceContracts, organizationAdditionalReviewedSources,
} from './organization-additional-reference-recipes.mjs';
import {
    settingsAdditionalEditorialSections, settingsAdditionalSourceContractKeys,
    settingsAdditionalSourceContracts, settingsAdditionalReviewedSources,
} from './settings-additional-reference-recipes.mjs';

const chapters = [
    ['docs/manuale/communication-auth-reference-recipes.mjs', communicationAuthEditorialSections,
        communicationAuthSourceContractKeys, communicationAuthSourceContracts, communicationAuthReviewedSources],
    ['docs/manuale/member-additional-reference-recipes.mjs', memberAdditionalEditorialSections,
        memberAdditionalSourceContractKeys, memberAdditionalSourceContracts, memberAdditionalReviewedSources],
    ['docs/manuale/course-payment-additional-reference-recipes.mjs', coursePaymentAdditionalEditorialSections,
        coursePaymentAdditionalSourceContractKeys, coursePaymentAdditionalSourceContracts, coursePaymentAdditionalReviewedSources],
    ['docs/manuale/organization-additional-reference-recipes.mjs', organizationAdditionalEditorialSections,
        organizationAdditionalSourceContractKeys, organizationAdditionalSourceContracts, organizationAdditionalReviewedSources],
    ['docs/manuale/settings-additional-reference-recipes.mjs', settingsAdditionalEditorialSections,
        settingsAdditionalSourceContractKeys, settingsAdditionalSourceContracts, settingsAdditionalReviewedSources],
];

/**
 * Discover all original section identities through explicit chapter registries.
 * This metadata inventory never grants verification or proves screenshot coverage.
 * Returns fresh JSON-compatible objects; never calls a verified reference builder,
 * fabricates canonical hashes, reads current source files, or grants publication.
 * @returns {Array<{path:string,id:string,title:string,status:string,reason:string,
 * recipe_module:string,verified:false,source_contracts:Array<{path:string,symbol:string,
 * length:number,reviewed_sha256:string}>}>}
 */
export function editorialContracts() {
    const seen = new Set();
    const authored = chapters.flatMap(([recipe_module, sections, keys, contracts, reviewed]) => {
        if (keys.length !== contracts.length || new Set(keys).size !== keys.length)
            throw new Error('Invalid editorial contract-key map: ' + recipe_module);
        const byKey = new Map(keys.map((key, index) => [key, contracts[index]]));
        return sections.map(section => {
            const identity = section.path + '#' + section.id;
            if (seen.has(identity)) throw new Error('Duplicate editorial section: ' + identity);
            seen.add(identity);
            return {
                path: section.path, id: section.id, title: section.title,
                status: section.status, reason: section.reason || '', recipe_module, verified: false,
                source_contracts: section.contracts.map(key => {
                    const contract = byKey.get(key);
                    if (!contract) throw new Error('Unknown editorial source contract ' + key + ': ' + identity);
                    const [path, symbol, length] = contract;
                    const reviewed_sha256 = reviewed[path];
                    if (typeof path !== 'string' || typeof symbol !== 'string' ||
                        !Number.isInteger(length) || length < 1 ||
                        typeof reviewed_sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(reviewed_sha256))
                        throw new Error('Invalid recorded editorial source contract: ' + identity + ' / ' + key);
                    return {path, symbol, length, reviewed_sha256};
                }),
            };
        });
    });
    const legacy = [accountingBalanceEditorialContracts, paymentEditorialContracts, contentEditorialContracts, instructorEditorialContracts, attendanceCarnetEditorialContracts, campsCalendarEditorialContracts, registrationFormsEditorialContracts, organizationAccessEditorialContracts, dashboardEditorialContracts, memberEditorialContracts, profileEditorialContracts, tagEditorialContracts, searchEditorialContracts, referenceEditorialContracts, overviewEditorialContracts].flatMap(discover => discover());
    for (const section of legacy) {
        const identity = section.path + '#' + section.id;
        if (seen.has(identity)) throw new Error('Duplicate editorial section: ' + identity);
        seen.add(identity);
        if (section.verified !== false || !section.recipe_module.startsWith('docs/manuale/') ||
            !Array.isArray(section.source_contracts)) throw new Error('Invalid legacy draft descriptor: ' + identity);
        for (const contract of section.source_contracts)
            if (!contract.path.startsWith('BE/') && !contract.path.startsWith('UI/') ||
                typeof contract.symbol !== 'string' || !contract.symbol ||
                !Number.isInteger(contract.length) || contract.length < 1 ||
                !/^[a-f0-9]{64}$/.test(contract.reviewed_sha256))
                throw new Error('Invalid legacy source context: ' + identity);
    }
    return [...authored, ...legacy];
}
