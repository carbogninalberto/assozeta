// Source for the generated Python-facing recipes.json inventory.
import {authoredWorkflows} from './authored-workflows.mjs';

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
    return {format: 1, shared_dependencies: sharedDependencies,
        recipes: Object.entries(authoredWorkflows).sort(([a], [b]) => a.localeCompare(b)).map(([id, spec]) => {
            // Shared mutation helpers participate in both capture and reuse invalidation.
            const sources = [...spec.sources];
            const inputs = [...(spec.dependencies || []), ...sources];
            if (["BE/application/views/profile_views.py", "BE/application/views/two_fa_views.py"]
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
