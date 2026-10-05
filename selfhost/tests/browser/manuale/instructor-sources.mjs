// Importing reviewed dependencies never launches a browser or fabricates evidence.
export const instructorWorkflowSources = [
    'UI/src/components/Sidebar.svelte', 'UI/src/routes.js', 'UI/src/utils/Permissions.js',
    'UI/src/utils/Functions.js', 'UI/src/components/InplaceTabs.svelte',
    'UI/src/components/buttons/EditButton.svelte', 'UI/src/components/tables/BKNDatatable.svelte',
    'UI/src/routes/association/course/instructor/InstructorList.svelte',
    'UI/src/routes/association/course/instructor/add/AddInstructor.svelte',
    'UI/src/routes/association/course/instructor/add/sections/Section1.svelte',
    'UI/src/routes/association/course/instructor/modals/EditModal.svelte',
    'UI/src/routes/association/course/instructor/info/Instructor.svelte',
    'UI/src/routes/association/course/instructor/info/LessonsHoursCard.svelte',
    'UI/src/routes/association/course/instructor/info/modals/AddEditModal.svelte',
    'UI/src/routes/association/course/instructor/info/modals/instructorPeriod.js',
    'BE/application/views/instructor_views.py', 'BE/application/utils/instructors_utils.py',
    'BE/application/serializers/user_serializers.py', 'BE/application/models/user_models.py',
    'BE/application/permissions_registry.py', 'BE/core/settings.py',
];
