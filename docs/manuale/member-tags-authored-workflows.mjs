// Execution contracts only. Prepared prose/placeholders do not establish runtime verification.
const page = 'docs/libro-soci.mdx';
const sources = [
    'UI/src/components/Sidebar.svelte', 'UI/src/routes.js', 'UI/src/utils/Permissions.js',
    'UI/src/utils/ApiMiddleware.js', 'UI/src/store/stores.js', 'UI/src/shim/ui.js', 'UI/src/shim/dropdown.js',
    'UI/src/components/tables/BKNDatatable.svelte', 'UI/src/components/dropdowns/basic-dropdown.svelte',
    'UI/src/routes/association/Members/MembersList.svelte',
    'UI/src/routes/association/Members/MembersBook.svelte',
    'UI/src/routes/association/Members/shared/NavigationTab.svelte',
    'BE/application/views/subscriptions_views.py', 'BE/application/services/subscription_service.py',
    'BE/application/models/subscriptions_models.py', 'BE/application/serializers/subscriptions_serializers.py',
    'BE/application/permissions_registry.py', 'BE/application/impersonation.py',
    'BE/application/impersonation_scope.py', 'BE/application/urls.py',
];
const points = [
    ['tag-assignment-selected-people', 'Giulia e Sara selezionate; Luca escluso dalla modifica.'],
    ['tag-menu-initially-unchecked', 'Menu riaperto con caselle disattivate anche per il tag già assegnato.'],
    ['tag-complete-set-before-apply', 'Gruppo A e Laboratorio selezionati prima di APPLICA.'],
    ['tag-batch-assignment-after-reload', 'Entrambi i tag su Giulia e Sara dopo il ricaricamento.'],
    ['tag-personal-removal-preserves-other-tag', 'Solo Gruppo A selezionato per conservare il tag di Giulia.'],
    ['tag-personal-removal-after-reload', 'Laboratorio tolto da Giulia, ancora assegnato a Sara.'],
    ['tag-reader-write-action-disabled', 'Collaboratore in lettura con Assegna Tag disattivato.'],
    ['tag-global-delete-confirmation', 'Vuoi eliminare il tag? prima della scelta Annulla o Elimina.'],
    ['tag-cancelled-delete-preserves-definition', 'Laboratorio ancora disponibile dopo Annulla.'],
    ['tag-global-delete-clears-remaining-assignment', 'Eliminazione confermata: Laboratorio assente anche dalla riga di Sara.'],
    ['tag-global-delete-unavailable-after-reopen', 'Menu riaperto: Gruppo A conservato, Laboratorio non più disponibile.'],
];
const image = checkpoint => ({from: '/images/libro-soci/tag-operazioni/'
    + (points.findIndex(([id]) => id === checkpoint) + 1) + '.placeholder.svg', checkpoint});
export const memberTagsAuthoredWorkflows = Object.freeze({
    'member-tags': {
        version: 1, script: 'selfhost/tests/browser/manuale/member-tags.mjs',
        prefix: 'images/libro-soci/tag-operazioni/', pages: [page], sources,
        dependencies: [...sources, 'selfhost/tests/browser/manuale/member-tags.mjs',
            'docs/manuale/member-tags-authored-workflows.mjs'],
        checkpoints: points.map(([id, caption]) => ({id, caption})),
        sections: [
            {path: page, id: 'assegnazione-di-tag-ai-soci', title: 'Assegnazione di Tag ai Soci',
                checkpoints: points.slice(0, 4).map(([id]) => id).concat('tag-reader-write-action-disabled'),
                images: [...points.slice(0, 4).map(([id]) => image(id)), image('tag-reader-write-action-disabled')], insert_images: []},
            {path: page, id: 'eliminare-un-tag', title: 'Eliminare un Tag',
                checkpoints: points.slice(4).map(([id]) => id),
                images: points.slice(4).filter(([id]) => id !== 'tag-reader-write-action-disabled').map(([id]) => image(id)), insert_images: []},
        ],
        outcome: {field: 'member_tags_authored_workflow', expected: {
            baseline_members: 3, baseline_tags: 0, prepared_tags: 2,
            menu_reopen_resets_selection: true, assigned_people: 2,
            complete_tag_set_saved_after_reload: true, unselected_member_preserved: true,
            individual_removal_keeps_definition: true, individual_removal_keeps_other_person: true,
            individual_removal_keeps_selected_other_tag: true,
            reader_tags_readable: true, reader_assignment_disabled: true,
            reader_create_status: 403, reader_update_status: 403, reader_assign_status: 403,
            reader_unassign_status: 403, reader_delete_status: 403, reader_denials_preserve_state: true,
            missing_tag_delete_status: 404, missing_tag_assignment_status: 404,
            missing_subscription_assignment_status: 404, missing_subscription_unassignment_status: 404,
            missing_targets_preserve_state: true,
            cancelled_delete_preserves_definition_and_assignments: true,
            global_delete_removes_definition: true, global_delete_removes_all_its_assignments: true,
            global_delete_preserves_other_tag: true,
            baseline_non_tag_member_data_preserved: true, owned_tags_removed: true,
            baseline_members_and_tags_restored: true,
        }},
    },
});
