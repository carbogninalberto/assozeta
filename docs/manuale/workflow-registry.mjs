// Source for the generated Python-facing recipes.json inventory.
import {authoredWorkflows} from './authored-workflows.mjs';
import {editorialContracts} from './editorial-contracts.mjs';

// Restore has reviewed reference prose, but no captured procedure uses its
// implementation. Explicit recipe dependencies still take precedence.
export const referenceOnlyDependencies = ['BE/instance/restore/'];

export const sharedDependencies = [
    "BE/core/",
    "BE/instance/",
    "BE/application/permissions",
    "BE/application/impersonation",
    "BE/application/management/commands/seed_",
    "BE/application/management/commands/run_manuale_instance.py",
    "UI/src/App.svelte",
    "UI/src/components/Sidebar.svelte",
    "UI/src/routes.js",
    "UI/src/utils/",
    "UI/src/store/",
    "UI/src/shim/",
    "UI/src/components/tables/",
    "UI/package",
    "UI/vite.config.js",
    "docs/manuale/",
    "docs/scripts/manuale",
    "selfhost/",
    "BE/application/urls.py"
];

export function workflowRegistry() {
    const references = editorialContracts();
    return {format: 1, shared_dependencies: sharedDependencies,
        non_capture_dependencies: referenceOnlyDependencies,
        reference_dependencies: [...new Set(references.flatMap(section => section.source_contracts.map(source => source.path)))].sort(),
        reference_modules: [...new Set(references.map(section => section.recipe_module))].sort(),
        recipes: Object.entries(authoredWorkflows).sort(([a], [b]) => a.localeCompare(b)).map(([id, spec]) => ({
            id, version: spec.capture_version || spec.version, script: spec.script,
            pages: [...new Set([...spec.pages, ...spec.sections.map(section => section.path)])],
            dependencies: [...new Set([...spec.dependencies, ...spec.sources, spec.script])].sort(),
            capture_prefix: spec.prefix, sources: spec.sources, checkpoints: spec.checkpoints,
            publication_sections: spec.sections.map(section => section.path + '#' + section.id),
            ...(spec.fixture_profile ? {fixture: spec.fixture_profile} : {}),
        }))};
}
