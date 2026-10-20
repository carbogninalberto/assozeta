import {permissions} from 'store/stores.js';

export const allPermissions = [
    // dashboard
    'association.dashboard.read',

    // calendar
    'association.calendar.read',

    // personas
    'association.personas.read',
    'association.personas.create',
    'association.personas.update',
    'association.personas.delete',

    // templates
    'association.templates.read',
    'association.templates.create',
    'association.templates.update',
    'association.templates.delete',

    // members
    'association.members.read',
    'association.members.create',
    'association.members.update',
    'association.members.delete',

    // members archive
    'association.members.archive.read',
    'association.members.archive.update',

    // modules
    'association.modules.read',
    'association.modules.create',
    'association.modules.update',
    'association.modules.delete',

    // courses
    'association.courses.read',
    'association.courses.create',
    'association.courses.update',
    'association.courses.delete',

    // camps and retreats
    'association.campsandretreats.read',
    'association.campsandretreats.create',
    'association.campsandretreats.update',
    'association.campsandretreats.delete',

    // courses archive
    // 'association.courses.archive.read',
    // 'association.courses.archive.update',

    // carnet
    'association.carnet.read',
    'association.carnet.create',
    'association.carnet.update',
    'association.carnet.delete',

    // instructor
    'association.instructor.read',
    'association.instructor.create',
    'association.instructor.update',
    'association.instructor.delete',

    // instructor hours
    'association.instructor.hours.read',
    'association.instructor.hours.create',
    'association.instructor.hours.update',
    'association.instructor.hours.delete',

    // attendance
    'association.courses.attendance.read',
    'association.courses.attendance.update',

    // archive
    'association.archive.read',
    'association.archive.update',

    // report
    'association.report.read',

    // events
    'association.events.read',
    'association.events.create',
    'association.events.update',
    'association.events.delete',

    // events archive
    // 'association.events.archive.read',
    // 'association.events.archive.update',

    // communication
    'association.communication.messages.read',
    'association.communication.messages.create',
    'association.communication.messages.update',
    'association.communication.messages.delete',

    // communication workflows
    'association.communication.workflows.read',
    'association.communication.workflows.create',
    'association.communication.workflows.update',
    'association.communication.workflows.delete',

    // communication settings
    'association.communication.settings.read',
    'association.communication.settings.update',

    // payments
    'bookeeping.payments.read',
    'bookeeping.payments.create',
    'bookeeping.payments.update',
    'bookeeping.payments.delete',

    // payments archive
    'bookeeping.payments.archive.read',
    'bookeeping.payments.archive.update',

    // invoices
    'bookeeping.documents.invoices.read',
    'bookeeping.documents.invoices.create',
    'bookeeping.documents.invoices.update',
    'bookeeping.documents.invoices.delete',

    // invoice archive
    'bookeeping.documents.invoices.archive.read',
    'bookeeping.documents.invoices.archive.update',

    // client invoices
    'bookeeping.documents.clientinvoices.read',
    'bookeeping.documents.clientinvoices.create',
    'bookeeping.documents.clientinvoices.update',
    'bookeeping.documents.clientinvoices.delete',

    // supplier invoices
    'bookeeping.documents.supplierinvoices.read',
    'bookeeping.documents.supplierinvoices.create',
    'bookeeping.documents.supplierinvoices.update',
    'bookeeping.documents.supplierinvoices.delete',

    // suppliers
    'bookeeping.management.suppliers.read',
    'bookeeping.management.suppliers.create',
    'bookeeping.management.suppliers.update',
    'bookeeping.management.suppliers.delete',

    // accounts
    'bookeeping.management.accounts.read',
    'bookeeping.management.accounts.create',
    'bookeeping.management.accounts.update',
    'bookeeping.management.accounts.delete',

    // accounts transfers
    'bookeeping.management.accountstransfers.read',
    'bookeeping.management.accountstransfers.create',
    'bookeeping.management.accountstransfers.update',
    'bookeeping.management.accountstransfers.delete',

    // balance sheet
    'bookeeping.management.balancesheet.read',
    'bookeeping.management.balancesheet.update',

    // collaborators
    'other.users.collaborators.read',
    'other.users.collaborators.create',
    'other.users.collaborators.update',
    'other.users.collaborators.delete',

    // settings
    'other.settings.read',
    'other.settings.update',

    // notifications
    'other.notifications.read',

    // audit
    'other.audit.read',
];

permissions.useLocalStorage();
/** Set role permissions after userData has been published to local storage. */
export const setPermissions = function (role) {
    localStorage.removeItem('permissions');
    const userData = JSON.parse(localStorage.getItem('userData') || '{}');
    if (role !== 'association') {
        permissions.set([]);
        return;
    }
    if (userData.collaborator_role && Number(userData.collaborator_role) !== 1) {
        const grants = Array.isArray(userData.collaborator_permissions) ? userData.collaborator_permissions : [];
        permissions.set(grants.length ? grants : ['other.settings.read']);
        return;
    }
    permissions.set(allPermissions);
};

/**
 * this function is used to check if the user can perform an action
 * @param {*} action is a resource name like 'association.dashboard'
 * @returns true if the user can perform the action, false otherwise
 */
export const canPerformAction = function (action, perms = []) {
    if (perms.length === 0) {
        let permissions = [];
        try {
            permissions = JSON.parse(localStorage.getItem('permissions')) || [];
        } catch (error) {
            console.error('Permissions not valid', error);
        }

        if (!permissions || permissions.length == 0) {
            const userRole = JSON.parse(localStorage.getItem('role'));
            setTimeout(() => {
                if (userRole === 'athlete') {
                    return;
                }
                if (location.href != '/welcome') {
                    return;
                }
                window.location.reload();
            }, 500);
            // Athletes don't use the permission system - they should be blocked by route-level role checks
            return false;
        }
        //console.info('permissions', permissions, permissions.length);
        return permissions.includes(action);
    }
    return perms.includes(action);
};

// export const canCollaboratorPerformAction = function (action) {
//     // get collaborators permissions list from local storage userData
//     const userData = localStorage.getItem('userData');
//     // get collaborator permissions if set in userData
//     const collaboratorPermissions = (userData && JSON.parse(userData).collaborator_permissions) || null;
//     if (collaboratorPermissions) {
//         return canPerformAction(action, collaboratorPermissions);
//     } else {
//         return true;
//     }
// };
