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
        recipes: Object.entries(authoredWorkflows).sort(([a], [b]) => a.localeCompare(b)).map(([id, spec]) => {
            // Shared mutation helpers participate in both capture and reuse invalidation.
            const sources = [...spec.sources];
            const inputs = [...(spec.dependencies || []), ...sources];
            // payment_views also contains unrelated, non-atomic workflows;
            // only category mutations use the strict audit helper there.
            const categoryMutation = ["payment-categories-manage", "payment-classification-local"].includes(id);
            if (categoryMutation || ["BE/application/views/profile_views.py", "BE/application/views/two_fa_views.py"]
                .some(path => inputs.some(prefix => path.startsWith(prefix))))
                sources.push("BE/application/audit_context.py", "BE/application/signals.py");
            const captureSources = [...new Set(sources)];
            return ({
            id, version: spec.capture_version || spec.version, script: spec.script,
            pages: [...new Set([...spec.pages, ...spec.sections.map(section => section.path)])],
            dependencies: [...new Set([...spec.dependencies, ...captureSources, spec.script])].sort(),
            capture_prefix: spec.prefix, sources: captureSources, checkpoints: spec.checkpoints,
            publication_sections: spec.sections.map(section => section.path + '#' + section.id),
            ...(spec.fixture_profile ? {fixture: spec.fixture_profile} : {}),
        });})};
}
