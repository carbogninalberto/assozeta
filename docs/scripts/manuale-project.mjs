// Pure projection of reviewed prose onto existing capture slots. No browser or reports are created.
import fs from 'node:fs';
import {authoredWorkflows} from '../manuale/authored-workflows.mjs';
import {authoredSection, projectWorkflowSection} from '../manuale/manual-content.mjs';

const {manual, application, report, sections} = JSON.parse(fs.readFileSync(0, 'utf8'));
const spec = authoredWorkflows[report.id];
if (!spec) throw new Error('Unknown workflow for editorial projection: ' + report.id);
const result = sections.map(item => {
    const section = spec.sections.find(section => section.path === item.path && section.id === item.id);
    if (!section) throw new Error('Unknown workflow section: ' + item.path + '#' + item.id);
    const body = authoredSection(manual, application, section.path, section.id, section.title);
    return {path: section.path, id: section.id, ...projectWorkflowSection(body, spec, report, section)};
});
process.stdout.write(JSON.stringify(result));
