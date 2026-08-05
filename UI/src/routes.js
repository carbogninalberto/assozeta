// Components - using lazy loading with dynamic imports
import {get} from 'svelte/store';
import {wrap} from 'svelte-spa-router/wrap';
import {currentPage, role, subPage, sessionToken} from './store/stores.js';
import {canPerformAction} from 'utils/Permissions.js';

currentPage.useLocalStorage();
subPage.useLocalStorage();
role.useLocalStorage();
sessionToken.useLocalStorage();

const clearDatatableMeta = function () {
    localStorage.removeItem('bkn_datatable-1-meta');
};

// Helper function to check association-only access with athlete redirect
function checkAssociationAccess(permission, redirectPath = '/#/subscription/list') {
    if (!canPerformAction(permission)) {
        if (isLogged() && get(role) === 'athlete') {
            window.location.href = redirectPath;
        }
        return false;
    }

    if (!isPlanActive()) return false;

    return isLogged() && get(role) === 'association';
}

function isLogged() {
    // check if there is a session token
    if (localStorage.getItem('sessionToken') == 'null') {
        localStorage.clear();
        window.location.href = '/#/login';
    }
    return (
        // parseInt(localStorage.getItem('expires') || 0) > new Date().getTime() &&
        localStorage.getItem('sessionToken') && localStorage.getItem('sessionToken') != 'null'
    );
}

function isPlanActive() {
    // if expired show the renewal page
    subPage.set('');
    const isExpired = localStorage.getItem('isExpired') === 'true';
    if (isExpired) window.location.href = '/#/subscription/upgrade';
    return !isExpired || localStorage.getItem('role') === 'athlete';
}

function isAssociation() {
    return localStorage.getItem('role') === '"association"';
}

// Export the route definition object
export default {
    // Exact path
    '/': wrap({
        // Dashboard Component
        asyncComponent: async () => {
            // if get(role) is null we should wait with a while loop
            while (!get(role)) {
                await new Promise(resolve => setTimeout(resolve, 100));
            }

            if (!canPerformAction('association.dashboard.read')) {
                if (get(role) == 'association') {
                    return (await import('./routes/dashboard/EmptyDasbhoard.svelte')).default;
                }
            }

            return get(role) == 'association'
                ? (await import('./routes/dashboard/Dashboard.svelte')).default
                : (await import('./routes/userdashboard/Dashboard.svelte')).default;
        },

        // Condition is logged
        conditions: [
            () => {
                if (!isPlanActive()) return false;

                if (isLogged()) {
                    currentPage.set('dashboard');
                    return true;
                }
            },
            // add another call
        ],
    }),

    '/calendar': wrap({
        asyncComponent: () => import('./routes/calendar/Calendar.svelte'),

        // Condition is logged and plan is active
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('association.calendar.read')) return false;

                if (!isPlanActive()) return false;

                if (isLogged() && get(role) == 'association') {
                    currentPage.set('calendar');
                    return true;
                }
            },
            // add another call
        ],
    }),

    '/connected-collaborators': wrap({
        asyncComponent: () => import('./routes/connectedCollaborators/ConnectedCollaborators.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!isPlanActive()) return false;

                if (isLogged()) {
                    currentPage.set('users');
                    subPage.set('connected-collaborators');
                    return true;
                }
            },
            // add another call
        ],
    }),

    '/profile': wrap({
        asyncComponent: () => import('./routes/profile/Profile.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (isLogged()) {
                    currentPage.set('profile');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/search': wrap({
        asyncComponent: () => import('./routes/search/mobile/Search.svelte'),

        // Condition is logged
        conditions: [
            () => {
                if (isLogged()) {
                    currentPage.set('dashboard');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/search/profile/:username?/:subscribe?': wrap({
        asyncComponent: () => import('./routes/search/profile/Profile.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isPlanActive()) return false;

                if (isLogged()) {
                    currentPage.set('search');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/personas/list': wrap({
        asyncComponent: () => import('./routes/association/personas/PersonasList.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('association.personas.read')) return false;

                if (!isPlanActive()) return false;

                if (isLogged()) {
                    currentPage.set('members');
                    subPage.set('personas-list');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/members/list': wrap({
        asyncComponent: () => import('./routes/association/Members/MembersList.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (checkAssociationAccess('association.members.read')) {
                    clearDatatableMeta();
                    currentPage.set('members');
                    subPage.set('members-list');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/members/members-book': wrap({
        asyncComponent: () => import('routes/association/Members/MembersBook.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (checkAssociationAccess('association.members.read')) {
                    currentPage.set('members');
                    subPage.set('members-list');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/members/list-draft': wrap({
        asyncComponent: () => import('routes/association/Members/MembersDraft.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (checkAssociationAccess('association.members.read')) {
                    currentPage.set('members');
                    subPage.set('members-list');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/members/list/detail/:subscriptionId/:page?': wrap({
        asyncComponent: () => import('./routes/association/Members/detail/Detail.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (checkAssociationAccess('association.members.read')) {
                    clearDatatableMeta();
                    currentPage.set('members');
                    subPage.set('detail');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/card/:subscriptionId/:token?/:print?': wrap({
        asyncComponent: () => import('routes/association/Members/detail/sections/subcomponents/Tessera.svelte'),
    }),
    '/members/archive': wrap({
        asyncComponent: () => import('./routes/association/Members/MembersListArchive.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (checkAssociationAccess('association.members.read')) {
                    clearDatatableMeta();
                    currentPage.set('members');
                    subPage.set('members-list');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/members/import': wrap({
        asyncComponent: () => import('./routes/association/Members/import/ImportMembers.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (checkAssociationAccess('association.members.read')) {
                    currentPage.set('members');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/members/modules': wrap({
        asyncComponent: () => import('./routes/association/Members/Modules.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (checkAssociationAccess('association.modules.read')) {
                    subPage.set('modules');
                    currentPage.set('members');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/members/modules/composer/:id?': wrap({
        asyncComponent: () => import('./routes/association/Members/composer/ModuleComposer.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (checkAssociationAccess('association.modules.read')) {
                    subPage.set('modules');
                    currentPage.set('members');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/members/modules/overview/:module_id?': wrap({
        asyncComponent: () => import('./routes/association/Members/overview/ModuleOverview.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (checkAssociationAccess('association.modules.read')) {
                    subPage.set('modules');
                    currentPage.set('members');
                    return true;
                }
            },
            // add another call
        ],
    }),

    '/forms/:custom_link/:response_id?': wrap({
        asyncComponent: () => import('./routes/forms/PublicModule.svelte'),
    }),
    '/members/add': wrap({
        asyncComponent: () => import('./routes/association/Members/add/AddMember.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('association.members.create')) return false;

                if (!isPlanActive()) return false;

                if (isLogged()) {
                    currentPage.set('members');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/members/subscription/template': wrap({
        asyncComponent: () => import('./routes/association/Members/subscription/Template.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('association.members.read')) return false;

                if (!isPlanActive()) return false;

                if (isLogged()) {
                    subPage.set('members-template');
                    currentPage.set('members');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/members/subscription/share-module-link': wrap({
        asyncComponent: () => import('./routes/association/Members/ShareModuleLink.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('association.members.read')) return false;

                if (!isPlanActive()) return false;

                if (isLogged()) {
                    subPage.set('members-link');
                    currentPage.set('members');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/payment/list/:id?': wrap({
        asyncComponent: async () => {
            return get(role) == 'association'
                ? (await import('./routes/accounting/payment/PaymentList.svelte')).default
                : (await import('./routes/activities/payment/PaymentList.svelte')).default;
        },

        // Condition is logged (both athlete and association)
        conditions: [
            () => {
                // Athletes can access without permission/plan checks
                if (get(role) === 'athlete' && isLogged()) {
                    clearDatatableMeta();
                    currentPage.set('payment');
                    subPage.set('payment-list');
                    return true;
                }
                if (!isAssociation()) return false;
                // Associations need permission and plan checks
                if (!canPerformAction('bookeeping.payments.read')) return false;
                if (!isPlanActive()) return false;

                if (isLogged()) {
                    clearDatatableMeta();
                    currentPage.set('payment');
                    subPage.set('payment-list');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/payment/archive': wrap({
        asyncComponent: () => import('./routes/accounting/payment/PaymentListArchive.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('bookeeping.payments.archive.read')) return false;

                if (!isPlanActive()) return false;

                if (isLogged()) {
                    clearDatatableMeta();
                    currentPage.set('payment');
                    subPage.set('payment-archive');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/payment/category/list': wrap({
        asyncComponent: () => import('./routes/accounting/payment/category/Categories.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('bookeeping.payments.read')) return false;
                if (!isPlanActive()) return false;

                if (isLogged()) {
                    clearDatatableMeta();
                    currentPage.set('payment');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/subscribe/:sportAssociationUsername?': wrap({
        asyncComponent: () => import('./routes/subscribe/Subscribe.svelte'),
    }),
    '/subscribe/:sportAssociationUsername/:preregistration': wrap({
        asyncComponent: () => import('./routes/subscribe/Subscribe.svelte'),
    }),
    '/subscribe-multiple/:sportAssociationUsername?/:preregistration?/:token?': wrap({
        asyncComponent: () => import('routes/subscribe-multiple/SubscribeMultiple.svelte'),
    }),
    '/invite/:token': wrap({
        asyncComponent: () => import('./routes/invite/Invite.svelte'),
    }),
    '/error': wrap({
        asyncComponent: () => import('./routes/errors/RuntimeErrors.svelte'),
    }),
    '/subscription/list/:tab?': wrap({
        asyncComponent: () => import('./routes/activities/subscription/SubscriptionList.svelte'),

        // Condition is logged and athlete
        conditions: [
            () => {
                if (isLogged() && get(role) == 'athlete') {
                    currentPage.set('subscription');
                    return true;
                }
                return false;
            },
            // add another call
        ],
    }),

    '/carnet/list': wrap({
        asyncComponent: () => import('./routes/activities/carnet/AllCarnetList.svelte'),

        // Condition is logged and athlete
        conditions: [
            () => {
                if (isLogged() && get(role) == 'athlete') {
                    currentPage.set('carnet');
                    return true;
                }
                return false;
            },
            // add another call
        ],
    }),

    '/subscription/detail/:subscriptionId': wrap({
        asyncComponent: () => import('./routes/activities/subscription/EditSubscription.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('association.members.read')) return false;

                if (!isPlanActive()) return false;

                if (isLogged()) {
                    clearDatatableMeta();
                    currentPage.set('members');
                    subPage.set('detail');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/subscription/list/detail/:subscriptionId/attendance?': wrap({
        asyncComponent: () => import('./routes/activities/subscription/Attendance.svelte'),

        // Condition is logged and athlete
        conditions: [
            () => {
                if (isLogged() && get(role) == 'athlete') {
                    currentPage.set('subscription');
                    return true;
                }
                return false;
            },
            // add another call
        ],
    }),
    '/subscription/list/detail/:subscriptionId/calendar?': wrap({
        asyncComponent: () => import('./routes/activities/subscription/Calendar.svelte'),

        // Condition is logged and athlete
        conditions: [
            () => {
                if (isLogged() && get(role) == 'athlete') {
                    currentPage.set('subscription');
                    return true;
                }
                return false;
            },
            // add another call
        ],
    }),
    '/subscription/list/detail/:subscriptionId/carnet?': wrap({
        asyncComponent: () => import('./routes/activities/subscription/CarnetList.svelte'),

        // Condition is logged and athlete
        conditions: [
            () => {
                if (isLogged() && get(role) == 'athlete') {
                    currentPage.set('subscription');
                    return true;
                }
                return false;
            },
            // add another call
        ],
    }),
    '/invoice/list': wrap({
        asyncComponent: () => import('./routes/accounting/receipts/ReceiptList.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('bookeeping.documents.invoices.read')) return false;

                if (!isPlanActive()) return false;

                if (isLogged()) {
                    clearDatatableMeta();
                    currentPage.set('invoice');
                    subPage.set('invoice-list');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/invoice/archive': wrap({
        asyncComponent: () => import('./routes/accounting/receipts/ReceiptListArchive.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('bookeeping.documents.invoices.archive.read')) return false;

                if (!isPlanActive()) return false;

                if (isLogged()) {
                    clearDatatableMeta();
                    currentPage.set('invoice');
                    subPage.set('invoice-list');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/customers-invoice/list': wrap({
        asyncComponent: () => import('./routes/accounting/customersInvoice/CustomersInvoiceList.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('bookeeping.documents.clientinvoices.read')) return false;

                if (!isPlanActive()) return false;

                if (isLogged()) {
                    clearDatatableMeta();
                    currentPage.set('invoice');
                    subPage.set('customers-invoice-list');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/suppliers-invoice/list': wrap({
        asyncComponent: () => import('./routes/accounting/suppliersInvoice/SuppliersInvoiceList.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('bookeeping.documents.supplierinvoices.read')) return false;

                if (!isPlanActive()) return false;

                if (isLogged()) {
                    clearDatatableMeta();
                    currentPage.set('invoice');
                    subPage.set('suppliers-invoice-list');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/balance-sheet/list': wrap({
        asyncComponent: () => import('./routes/accounting/balanceSheet/BalanceSheet.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('bookeeping.management.balancesheet.read')) return false;

                if (!isPlanActive()) return false;

                if (isLogged()) {
                    clearDatatableMeta();
                    currentPage.set('balance-sheet');
                    subPage.set('balance-sheet-manage');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/accounting/list': wrap({
        asyncComponent: () => import('./routes/accounting/accounts/Accounts.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!canPerformAction('bookeeping.management.accounts.read')) return false;

                if (!isPlanActive()) return false;

                if (isLogged()) {
                    clearDatatableMeta();
                    currentPage.set('balance-sheet');
                    subPage.set('accounting-list');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/suppliers-and-customers/list': wrap({
        asyncComponent: () => import('./routes/accounting/suppliers-and-customers/SuppliersAndCustomers.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('bookeeping.management.suppliers.read')) return false;

                if (!isPlanActive()) return false;
                if (isLogged()) {
                    clearDatatableMeta();
                    currentPage.set('balance-sheet');
                    subPage.set('suppliers-list');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/accounting-transfer/list': wrap({
        asyncComponent: () => import('./routes/accounting/accountsTransfer/AccountsTransfer.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('bookeeping.management.accountstransfers.read')) return false;

                if (!isPlanActive()) return false;
                if (isLogged()) {
                    clearDatatableMeta();
                    currentPage.set('balance-sheet');
                    subPage.set('accounting-transfer-list');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/course/list': wrap({
        asyncComponent: () => import('./routes/association/course/CourseList.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('association.courses.read')) return false;

                if (!isPlanActive()) return false;

                if (isLogged()) {
                    currentPage.set('course');
                    subPage.set('course-list');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/course/archive': wrap({
        asyncComponent: () => import('./routes/association/course/CourseListArchive.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('association.courses.read')) return false;

                if (!isPlanActive()) return false;

                if (isLogged()) {
                    currentPage.set('course');
                    subPage.set('course-list');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/course/locations': wrap({
        asyncComponent: () => import('routes/association/course/CourseLocations.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('association.courses.read')) return false;

                if (!isPlanActive()) return false;

                if (isLogged()) {
                    currentPage.set('course');
                    subPage.set('course-list');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/camps-and-retreats/forms/:id?': wrap({
        asyncComponent: () => import('./routes/forms/CampsAndRetreatsForm.svelte'),
    }),
    '/course/camps-and-retreats/list': wrap({
        asyncComponent: () => import('./routes/association/course/campsAndRetreats/CampsAndRetreatsList.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('association.campsandretreats.read')) return false;

                if (!isPlanActive()) return false;

                if (isLogged()) {
                    currentPage.set('course');
                    subPage.set('camps-and-retreats-list');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/course/camps-and-retreats/overview/:id?': wrap({
        asyncComponent: () => import('./routes/association/course/campsAndRetreats/overview/CampsAndRetreatsOverview.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('association.campsandretreats.read')) return false;

                if (!isPlanActive()) return false;

                if (isLogged()) {
                    currentPage.set('course');
                    subPage.set('camps-and-retreats-list');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/course/camps-and-retreats/overview/:id?/detail/:periodId?': wrap({
        asyncComponent: () => import('./routes/association/course/campsAndRetreats/overview/CampsAndRetreatsPeriodOverview.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('association.campsandretreats.read')) return false;

                if (!isPlanActive()) return false;

                if (isLogged()) {
                    currentPage.set('course');
                    subPage.set('camps-and-retreats-list');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/course/carnet/list': wrap({
        asyncComponent: () => import('./routes/association/course/carnet/CarnetList.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('association.carnet.read')) return false;

                if (!isPlanActive()) return false;

                if (isLogged()) {
                    currentPage.set('course');
                    subPage.set('carnet-list');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/course/carnet/list/detail/:carnetId/:page?': wrap({
        asyncComponent: () => import('./routes/association/course/carnet/detail/CarnetDetail.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('association.carnet.read')) return false;

                if (!isPlanActive()) return false;

                if (isLogged()) {
                    clearDatatableMeta();
                    currentPage.set('course');
                    subPage.set('carnet-list');
                    return true;
                }
            },
            // add another call
        ],
    }),

    '/course/instructor/list': wrap({
        asyncComponent: () => import('./routes/association/course/instructor/InstructorList.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('association.instructor.read')) return false;

                if (!isPlanActive()) return false;

                if (isLogged()) {
                    currentPage.set('course');
                    subPage.set('instructor-list');
                    return true;
                }
            },
            // add another call
        ],
    }),

    '/course/instructor/info/:id': wrap({
        asyncComponent: () => import('./routes/association/course/instructor/info/Instructor.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('association.instructor.read')) return false;

                if (!isPlanActive()) return false;

                if (isLogged()) {
                    currentPage.set('course');
                    subPage.set('instructor-list');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/course/instructor/add': wrap({
        asyncComponent: () => import('./routes/association/course/instructor/add/AddInstructor.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('association.instructor.create')) return false;

                if (!isPlanActive()) return false;

                if (isLogged()) {
                    currentPage.set('course');
                    subPage.set('instructor-list');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/archive': wrap({
        asyncComponent: () => import('routes/association/archive/Archive.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('association.archive.read')) return false;

                if (!isPlanActive()) return false;

                if (isLogged()) {
                    currentPage.set('archive');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/audit/list': wrap({
        asyncComponent: () => import('./routes/association/audit/AuditList.svelte'),

        // Condition is logged and association
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('other.audit.read')) return false;
                if (!isPlanActive()) return false;

                if (isLogged() && get(role) === 'association') {
                    currentPage.set('audit');
                    return true;
                }
            },
        ],
    }),
    '/templates/list': wrap({
        asyncComponent: () => import('routes/association/archive/TemplatesList.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('association.templates.read')) return false;

                if (!isPlanActive()) return false;

                if (isLogged()) {
                    currentPage.set('archive');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/report': wrap({
        asyncComponent: () => import('./routes/association/report/Report.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('association.report.read')) return false;

                if (!isPlanActive()) return false;

                if (isLogged()) {
                    currentPage.set('report');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/saved-reports': wrap({
        asyncComponent: () => import('./routes/association/reports/SavedReports.svelte'),

        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('association.report.read')) return false;
                if (!isPlanActive()) return false;

                if (isLogged()) {
                    currentPage.set('saved-reports');
                    return true;
                }
            },
        ],
    }),
    '/course/carnet/add': wrap({
        asyncComponent: () => import('./routes/association/course/carnet/add/AddCarnet.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('association.carnet.create')) return false;

                if (!isPlanActive()) return false;

                if (isLogged()) {
                    currentPage.set('course');
                    subPage.set('carnet-list');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/course/overview/:id?/:page?': wrap({
        asyncComponent: () => import('./routes/association/course/overview/OverviewCourse.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('association.courses.read')) return false;

                if (!isPlanActive()) return false;

                if (isLogged()) {
                    clearDatatableMeta();
                    currentPage.set('course');
                    subPage.set('course-list');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/communication/messages': wrap({
        asyncComponent: () => import('./routes/association/communication/messages/Messages.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('association.communication.messages.read')) return false;

                if (!isPlanActive()) return false;

                if (isLogged()) {
                    currentPage.set('communication');
                    subPage.set('messages-list');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/communication/automation': wrap({
        asyncComponent: () => import('./routes/association/communication/automation/Automation.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('association.communication.workflows.read')) return false;

                if (!isPlanActive()) return false;

                if (isLogged()) {
                    currentPage.set('communication');
                    subPage.set('message-automation');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/communication/automation/editor/:workflow_id?': wrap({
        asyncComponent: () => import('./routes/association/communication/automation/editor/AutomationEditor.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('association.communication.workflows.read')) return false;

                if (!isPlanActive()) return false;

                if (isLogged()) {
                    currentPage.set('automation');
                    subPage.set('message-automation');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/communication/configuration': wrap({
        asyncComponent: () => import('./routes/association/communication/settings/Settings.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (!canPerformAction('association.communication.settings.read')) return false;

                if (!isPlanActive()) return false;

                if (isLogged()) {
                    currentPage.set('communication');
                    subPage.set('message-configuration');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/shared-calendar/:id?': wrap({
        asyncComponent: () => import('./routes/calendar/SharedCalendar.svelte'),
    }),
    '/third-party-licenses': wrap({
        asyncComponent: () => import('./routes/legal/ThirdPartyLicenses.svelte'),
        conditions: [
            () => {
                currentPage.set('third-party-licenses');
                return true;
            },
        ],
    }),
    '/login': wrap({
        asyncComponent: () => import('./routes/login/Login.svelte'),
        conditions: [
            () => {
                currentPage.set('login');
                return true;
            },
        ],
    }),
    '/stripe/onboarding': wrap({
        asyncComponent: () => import('./routes/stripe/Onboarding.svelte'),
        conditions: [
            () => {
                if (!isPlanActive()) return false;

                if (isLogged()) {
                    currentPage.set('onboarding');
                    return true;
                } else {
                    sessionStorage.setItem('check_onboarding', true);
                    currentPage.set('login');
                    return false;
                }
            },
            // add another call
        ],
    }),
    '/stripe/onboarded': wrap({
        asyncComponent: () => import('./routes/stripe/Onboarded.svelte'),
        conditions: [
            () => {
                if (!isPlanActive()) return false;

                if (isLogged()) {
                    currentPage.set('onboarded');
                    return true;
                } else {
                    sessionStorage.setItem('check_onboarded', true);
                    currentPage.set('login');
                    return false;
                }
            },
            // add another call
        ],
    }),
    '/stripe/pay/:id?/:one_fee_payment?': wrap({
        asyncComponent: () => import('./routes/stripe/Checkout.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                currentPage.set('payment');
                return true;
            },
            // add another call
        ],
    }),
    '/stripe/cart-pay': wrap({
        asyncComponent: () => import('./routes/stripe/CartCheckout.svelte'),
        conditions: [
            () => {
                currentPage.set('payment');
                return true;
            },
            // add another call
        ],
    }),
    '/stripe/payment/done': wrap({
        asyncComponent: () => import('./routes/stripe/PaymentLanding.svelte'),
        conditions: [
            () => {
                currentPage.set('payment');
                return true;
            },
            // add another call
        ],
    }),
    '/update-tutors': wrap({
        asyncComponent: () => import('./routes/update-tutors/UpdateTutors.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isPlanActive()) return false;

                if (isLogged()) {
                    currentPage.set('update-tutors');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/subscription': wrap({
        asyncComponent: () => import('./routes/subscription/Subscription.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (isLogged()) {
                    currentPage.set('profile');
                    return true;
                }
            },
            // add another call
        ],
    }),
    '/billing-checkout': wrap({
        asyncComponent: () => import('./routes/stripe/BillingCheckout.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (isLogged()) {
                    currentPage.set('profile');
                    return true;
                }
            },
            // add another call
        ],
    }),

    '/subscription/upgrade': wrap({
        asyncComponent: () => import('./routes/Upgrade.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (isLogged()) {
                    return true;
                }
            },
            // add another call
        ],
    }),

    '/tools/sport-associations-manager': wrap({
        asyncComponent: () => import('./routes/tools/SportAssociationsManager.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (JSON.parse(localStorage.getItem('userData')).is_superuser) {
                    return true;
                }
            },
            // add another call
        ],
    }),

    // '/affiliate': wrap({
    //     asyncComponent: () => import('./routes/affiliate/Affiliate.svelte'),

    //     // Condition is logged and admin
    //     conditions: [
    //         () => {
    //             if (!isAssociation()) return false;
    //             if (!isPlanActive()) return false;

    //             if (isLogged()) {
    //                 currentPage.set('affiliate');
    //                 subPage.set('affiliate-details');
    //                 return true;
    //             }
    //         },
    //         // add another call
    //     ],
    // }),

    '/welcome': wrap({
        asyncComponent: () => import('routes/onboarding/Welcome.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (isLogged()) {
                    return true;
                }
            },
            // add another call
        ],
    }),

    '/attendance-scanner-mode': wrap({
        asyncComponent: () => import('routes/scanners/AttendanceScannerMode.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (isLogged()) {
                    return true;
                }
            },
            // add another call
        ],
    }),

    '/email-builder/:id/:workflow_id': wrap({
        asyncComponent: () => import('components/inputs/email-builder/EmailBuilder.svelte'),

        // Condition is logged and admin
        conditions: [
            () => {
                if (!isAssociation()) return false;
                if (isLogged()) {
                    return true;
                }
            },
            // add another call
        ],
    }),

    '/subscribefamily/:sportAssociationUsername/:token': wrap({
        asyncComponent: () => import('routes/subscribefamily/SubscribeFamily.svelte'),
    }),

    '/reset': wrap({
        asyncComponent: () => import('./routes/login/Reset.svelte'),
    }),

    // Catch-all, must be last
    '*': wrap({
        asyncComponent: () => import('./routes/error/NotFound.svelte'),
    }),
};
