import {dashboardListsAuthoredWorkflows} from './dashboard-lists-authored-workflows.mjs';
import {paymentClassificationAuthoredWorkflows} from './payment-classification-authored-workflows.mjs';
import {registrationInitialAuthoredWorkflows} from './registration-initial-authored-workflows.mjs';
import {campsPersonasAuthoredWorkflows} from './camps-personas-authored-workflows.mjs';
import {automaticAttendanceAuthoredWorkflows} from './automatic-attendance-authored-workflows.mjs';
import {accountingDocumentsAuthoredWorkflows} from './accounting-documents-authored-workflows.mjs';
import {fiscalSettingsAuthoredWorkflows} from './fiscal-settings-authored-workflows.mjs';
import {organizationBasicsAuthoredWorkflows} from './organization-basics-authored-workflows.mjs';
import {dashboardAuthoredWorkflows} from './dashboard-authored-workflows.mjs';
import {accountingCalendarAuthoredWorkflows} from './accounting-calendar-authored-workflows.mjs';
import {coreLegacyAuthoredWorkflows} from './core-legacy-authored-workflows.mjs';
import {stripeLocalAuthoredWorkflows} from './stripe-local-authored-workflows.mjs';
import {attendanceCarnetAuthoredWorkflows} from './attendance-carnet-authored-workflows.mjs';
import {communicationCompleteAuthoredWorkflows} from './communication-complete-authored-workflows.mjs';
import {twoFactorLoginAuthoredWorkflows} from './two-factor-login-authored-workflows.mjs';
import {organizationMaintenanceAuthoredWorkflows} from './organization-maintenance-authored-workflows.mjs';
import {memberTagsAuthoredWorkflows} from './member-tags-authored-workflows.mjs';
// Procedures consume reviewed MDX; these registries contain execution metadata.
import {memberAuthoredWorkflows} from './member-authored-workflows.mjs';
import {courseSettingsAuthoredWorkflows} from './course-settings-authored-workflows.mjs';
import {communicationAuthAuthoredWorkflows} from './communication-auth-authored-workflows.mjs';
import {paymentMaintenanceAuthoredWorkflows} from './payment-maintenance-authored-workflows.mjs';
import {organizationAuthoredWorkflows} from './organization-authored-workflows.mjs';
import {settingsPrintAuthoredWorkflows} from './settings-print-authored-workflows.mjs';
import {instructorMaintenanceAuthoredWorkflows} from './instructor-maintenance-authored-workflows.mjs';
import {medicalFollowupsAuthoredWorkflows} from './medical-followups-authored-workflows.mjs';
import {membershipLinksCardsAuthoredWorkflows} from './membership-links-cards-authored-workflows.mjs';
import {courseCalendarMaintenanceAuthoredWorkflows} from './course-calendar-maintenance-authored-workflows.mjs';
import {isDeepStrictEqual} from 'node:util';

export const authoredWorkflows = {};
for (const chapter of [memberAuthoredWorkflows, courseSettingsAuthoredWorkflows, communicationAuthAuthoredWorkflows,
    paymentMaintenanceAuthoredWorkflows, organizationAuthoredWorkflows, settingsPrintAuthoredWorkflows, instructorMaintenanceAuthoredWorkflows, medicalFollowupsAuthoredWorkflows,
    membershipLinksCardsAuthoredWorkflows, courseCalendarMaintenanceAuthoredWorkflows, memberTagsAuthoredWorkflows,
    organizationMaintenanceAuthoredWorkflows, twoFactorLoginAuthoredWorkflows, communicationCompleteAuthoredWorkflows, attendanceCarnetAuthoredWorkflows, stripeLocalAuthoredWorkflows, coreLegacyAuthoredWorkflows, accountingCalendarAuthoredWorkflows, dashboardAuthoredWorkflows, organizationBasicsAuthoredWorkflows, fiscalSettingsAuthoredWorkflows, accountingDocumentsAuthoredWorkflows, automaticAttendanceAuthoredWorkflows, campsPersonasAuthoredWorkflows, registrationInitialAuthoredWorkflows, paymentClassificationAuthoredWorkflows, dashboardListsAuthoredWorkflows])
    for (const [id, spec] of Object.entries(chapter)) {
        if (authoredWorkflows[id]) throw new Error('Duplicate authored workflow: ' + id);
        authoredWorkflows[id] = {...spec, dependencies: [...new Set([...(spec.dependencies || []), ...spec.sources])]};
    }
Object.freeze(authoredWorkflows);

export function validateAuthoredWorkflow(report, spec) {
    if (!spec || spec.version !== 1) throw new Error('Unknown authored workflow specification');
    if (report.fixture_version !== 8 || report.fixture_profile !== (spec.fixture_profile || 'baseline'))
        throw new Error('Authored workflow requires the current baseline fixture or its explicitly selected profile: ' + report.id);
    // File numbers bind manual slots, while execution order may differ (for
    // example a dashboard must add widgets before documenting each widget).
    if (report.screenshots.length !== spec.checkpoints.length)
        throw new Error('Missing or duplicate authored workflow checkpoints: ' + report.id);
    for (const [index, point] of spec.checkpoints.entries()) {
        const matches = report.screenshots.filter(image => image.checkpoint === point.id);
        if (matches.length !== 1 || (spec.prefix && matches[0].path !== spec.prefix + (index + 1) + '.png'))
            throw new Error('Missing, duplicate, or misbound authored workflow checkpoint: ' + report.id + ':' + point.id);
    }
    const actual = report[spec.outcome.field];
    for (const [key, expected] of Object.entries(spec.outcome.expected))
        if (!actual || !isDeepStrictEqual(actual[key], expected))
            throw new Error('Incomplete authored workflow outcome: ' + report.id + ':' + key);
    for (const relative of spec.sources)
        if (!report.source_hashes[relative]) throw new Error('Missing authored workflow source: ' + relative);
}
