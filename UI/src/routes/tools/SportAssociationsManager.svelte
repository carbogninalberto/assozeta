<script>
    import EditSportAssociationButton from './../../components/buttons/EditSportAssociationButton.svelte';
    import {onMount} from 'svelte';
    import {scale} from 'svelte/transition';
    import * as easing from 'svelte/easing';
    import {sessionToken, userData, role, permissions, billingData, selectedGroup, currentPage, subPage} from 'store/stores.js';
    import UserSwitch from 'components/buttons/UserSwitch.svelte';
    import BillingInvoiceUploadButton from 'components/buttons/BillingInvoiceUploadButton.svelte';
    import {waitForElementAndExecute} from 'utils/Functions';
    const scrollToTop = () => window.scrollTo({ top: 0, behavior: 'smooth' });
import {push} from 'svelte-spa-router';
    import NotesButton from 'components/buttons/NotesButton.svelte';
    import notificationService from 'utils/NotificationService.js';
    import healthService from 'utils/HealthService.js';
    import {blockPage, unblockPage} from 'store/loadingStore.js';
    import BKNDatatable from 'components/tables/BKNDatatable.svelte';
    import { initTooltips } from 'shim/tooltip.js';
    import { initPopovers } from 'shim/popover.js';

    userData.useLocalStorage();

    let datatable;

    const columns = [
        {
            field: 'logo',
            title: '',
            sortable: true,
            autoHide: true,
            width: 25,
            template: function (row) {
                return (
                    '<img src="' +
                    String(
                        row?.logo ||
                            'data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI1MiIgaGVpZ2h0PSI1MiIgZmlsbD0iIzE4MjEzNSIgdmlld0JveD0iMCAwIDI1NiAyNTYiPjxwYXRoIGQ9Ik0yMjQsMTI4YTk1Ljc2LDk1Ljc2LDAsMCwxLTMxLjgsNzEuMzdBNzIsNzIsMCwwLDAsMTI4LDE2MGE0MCw0MCwwLDEsMC00MC00MCw0MCw0MCwwLDAsMCw0MCw0MCw3Miw3MiwwLDAsMC02NC4yLDM5LjM3aDBBOTYsOTYsMCwxLDEsMjI0LDEyOFoiIG9wYWNpdHk9IjAuMiI+PC9wYXRoPjxwYXRoIGQ9Ik0xMjgsMjRBMTA0LDEwNCwwLDEsMCwyMzIsMTI4LDEwNC4xMSwxMDQuMTEsMCwwLDAsMTI4LDI0Wk03NC4wOCwxOTcuNWE2NCw2NCwwLDAsMSwxMDcuODQsMCw4Ny44Myw4Ny44MywwLDAsMS0xMDcuODQsMFpNOTYsMTIwYTMyLDMyLDAsMSwxLDMyLDMyQTMyLDMyLDAsMCwxLDk2LDEyMFptOTcuNzYsNjYuNDFhNzkuNjYsNzkuNjYsMCwwLDAtMzYuMDYtMjguNzUsNDgsNDgsMCwxLDAtNTkuNCwwLDc5LjY2LDc5LjY2LDAsMCwwLTM2LjA2LDI4Ljc1LDg4LDg4LDAsMSwxLDEzMS41MiwwWiI+PC9wYXRoPjwvc3ZnPg=='
                    ) +
                    '" style="height:2rem;border-radius:2rem;" />'
                );
            },
        },
        {
            field: 'denomination',
            title: 'Nome',
            sortable: true,
            autoHide: false,
            width: 120,
            template: function (row) {
                return (
                    '<p class="text-dark-75 font-weight-bolder font-size-sm mb-0">' +
                    String(row?.denomination).toUpperCase() +
                    '</p>'
                );
            },
        },
        {
            field: 'date_joined',
            title: 'CREATO',
            width: 70,
            minWidth: '100%',
            autoHide: true,
            sortable: true,
            sortCallback: function (data, sort, column) {
                return [...data].sort(function (a, b) {
                    var aDate = a.date_joined ? new Date(a.date_joined).getTime() : 0;
                    var bDate = b.date_joined ? new Date(b.date_joined).getTime() : 0;
                    return sort === 'asc' ? aDate - bDate : bDate - aDate;
                });
            },
            template: function (row) {
                return (
                    '<p class="text-dark-75 font-weight-bolder font-size-sm mb-0">' +
                    (row.date_joined ? moment(row.date_joined).format('DD/MM/YYYY') : '-') +
                    '</p>'
                );
            },
        },
        {
            field: 'billing_subscription.ends_on',
            title: 'Termina il',
            sortable: true,
            minWidth: '100%',
            width: 150,
            autoHide: true,
            sortCallback: function (data, sort, column) {
                var field = column['field'];
                return [...data].sort(function (a, b) {
                    var aField =
                        a.billing_subscription && a.billing_subscription.ends_on
                            ? moment(a.billing_subscription.ends_on, moment.ISO_8601, true).isValid()
                                ? moment(a.billing_subscription.ends_on).valueOf()
                                : Number.MAX_SAFE_INTEGER
                            : Number.MAX_SAFE_INTEGER;
                    var bField =
                        b.billing_subscription && b.billing_subscription.ends_on
                            ? moment(b.billing_subscription.ends_on, moment.ISO_8601, true).isValid()
                                ? moment(b.billing_subscription.ends_on).valueOf()
                                : Number.MAX_SAFE_INTEGER
                            : Number.MAX_SAFE_INTEGER;
                    if (sort === 'asc') {
                        return aField - bField;
                    } else {
                        return bField - aField;
                    }
                });
            },
            template: function (row) {
                if (!row?.billing_subscription?.ends_on) return '<small><strong>MANCANTE</strong></small>';
                // format date with moment
                const endDate = moment(row?.billing_subscription?.ends_on);
                const now = moment();
                const daysUntilExpiry = endDate.diff(now, 'days');
                let colorClass = '';

                if (endDate.isBefore(now)) {
                    colorClass = 'text-danger';
                } else if (daysUntilExpiry <= 3) {
                    colorClass = 'text-warning';
                } else {
                    colorClass = 'text-success';
                }

                return (
                    '<p class="font-weight-boldest font-size-sm mb-0 ' +
                    colorClass +
                    '">' +
                    endDate.format('DD/MM/YYYY (HH:mm)') +
                    '</p>' +
                    '<p class="font-size-xs font-weight-bolder mb-0 ' +
                    colorClass +
                    '">' +
                    (daysUntilExpiry < 0
                        ? 'Scaduto da ' + Math.abs(daysUntilExpiry) + ' giorni'
                        : 'Scade tra ' + daysUntilExpiry + ' giorni') +
                    '</p>'
                );
            },
        },
        {
            field: 'user_onboarding',
            title: 'Onboarding',
            sortable: true,
            autoHide: true,
            template: function (row) {
                /* Onboarding keys:
                   create_membership
                   view_membership
                   approve_payment
                   download_invoice
                   view_collaborators
                   view_settings
                */
                let showKeys = [
                    'create_membership',
                    'view_membership',
                    'approve_payment',
                    'download_invoice',
                    'view_collaborators',
                    'view_settings',
                ];
                let onboarding = row?.user_onboarding;
                try {
                    let completedKeys = showKeys.filter(key => onboarding[key]);
                    let html = showKeys
                        .map(key => {
                            let isCompleted = onboarding[key];
                            let icon = isCompleted ? '✓' : '✗';
                            let color = isCompleted ? 'text-success' : 'text-danger';
                            let status = isCompleted ? 'Completato' : 'Non completato';
                            return `<span style="cursor: pointer;" class="${color}" data-toggle="tooltip" title="${key}: ${status}">${icon}</span>`;
                        })
                        .join(' ');
                    return `<p class="font-weight-bold mb-0">${html}</p>
                               <p class="font-size-xs mb-0 font-weight-boldest">${completedKeys.length}/${showKeys.length} completati</p>`;
                } catch (e) {
                    return '-';
                }
            },
        },
        {
            field: 'address_and_tax',
            title: 'Info fiscali',
            autoHide: true,
            sortable: true,
            template: function (row) {
                return (
                    '<p class="text-dark-75 font-weight-bolder mb-0 font-size-xs">' +
                    String(row.address || '-').toUpperCase() +
                    ', ' +
                    String(row.address_city || '-').toUpperCase() +
                    ', ' +
                    String(row.address_cap || '-').toUpperCase() +
                    '</p>' +
                    '<p class="text-dark-75 font-weight-bolder font-size-sm mb-0 mt-1">' +
                    String(row?.tax_code).toUpperCase() +
                    '</p>'
                );
            },
        },
        {
            field: 'email',
            title: 'Contatto',
            sortable: true,
            minWidth: '100%',
            autoHide: true,
            width: 200,
            template: function (row) {
                return (
                    '<p class="text-dark-75 font-size-sm font-weight-bolder mb-0">' +
                    String(row?.email).toUpperCase() +
                    `<br /> <span class="mt-1 font-size-xs">${row?.phone || '-'}, ${row?.role || '-'}, ${
                        row?.size || '-'
                    }</span>` +
                    '</p>'
                );
            },
        },
        {
            field: 'first_name',
            title: 'Responsabile',
            sortable: true,
            minWidth: '100%',
            autoHide: true,
            width: 220,
            template: function (row) {
                return (
                    '<p class="text-dark-75 font-size-sm font-weight-bolder mb-0" style="flex-wrap: wrap;">' +
                    String(row.first_name).toUpperCase() +
                    ' ' +
                    String(row?.last_name).toUpperCase() +
                    '</p>' +
                    '<p class="text-muted font-size-xs m-0">' +
                    new Date(row.last_login).toLocaleString('it-IT').replace(' ', ' ') +
                    '</p>'
                );
            },
        },
        {
            field: '',
            title: 'Azioni',
            sortable: false,
            overflow: 'visible',
            textAlign: 'right',
            minWidth: '100%',
            autoHide: false,
            fireClick: false,
            width: 170,
            template: function (row) {
                waitForElementAndExecute(`#action-col-${row.user_id}`, () => {
                    if (document.querySelector(`#action-col-${row.user_id}`))
                        document.querySelector(`#action-col-${row.user_id}`).innerHTML = '';

                    if (row.notes) {
                        let notesButton = new NotesButton({
                            target: document.querySelector(`#action-col-${row.user_id}`),
                            intro: true,
                            props: {
                                notes: row.notes,
                            },
                        });
                    }

                    let editBtn = new EditSportAssociationButton({
                        target: document.querySelector(`#action-col-${row.user_id}`),
                        intro: true,
                        props: {
                            disabled: false,
                            sport_association_id: row.sport_association_id,
                            data: row,
                        },
                    });

                    editBtn.$on('saved', () => {
                        datatable.reload();
                    });

                    let uploadInvoiceBtn = new BillingInvoiceUploadButton({
                        target: document.querySelector(`#action-col-${row.user_id}`),
                        intro: true,
                        props: {
                            disabled: false,
                            sport_association_id: row?.sport_association_id,
                        },
                    });

                    let switchBtn = new UserSwitch({
                        target: document.querySelector(`#action-col-${row?.user_id}`),
                        intro: true,
                        props: {
                            disabled: false,
                            // hidden: !row.editable,
                        },
                    });

                    switchBtn.$on('open', data => {
                        scrollToTop();
                        blockPage({
                            overlayColor: '#000000',
                            type: 'v2',
                            state: 'primary',
                            message: 'Switching user...',
                        });

                        // Disconnect WebSockets before switching user
                        notificationService.disconnect();
                        healthService.disconnect();

                        // clear all local storage keys except for sessionToken
                        Object.keys(localStorage).forEach(key => {
                            if (key !== 'sessionToken' && key !== 'refreshToken' && key !== 'expires')
                                localStorage.removeItem(key);
                        });
                        // set local storage key switched_superuser to true
                        localStorage.setItem('switched_superuser', true);
                        // set local storage USER_ID to row.user_id
                        localStorage.setItem('USER_ID', row.user_id);

                        // Reset Svelte stores to clear in-memory state
                        userData.set({});
                        role.set(null);
                        permissions.set([]);
                        billingData.set({});
                        selectedGroup.set(null);
                        currentPage.set('dashboard');
                        subPage.set('');

                        setTimeout(() => {
                            unblockPage();
                            // Use full page reload to ensure clean state
                            window.location.href = '/#/';
                        }, 500);
                    });
                });
                return `<div id="action-col-${row.user_id}" class="action-column pr-4"></div>`;
            },
        },
    ];

    onMount(() => {
        localStorage.removeItem('bkn_datatable-1-meta');
        setTimeout(() => {
            initTooltips(document.body);
            initPopovers(document.body);
        }, 500);
    });
</script>

<div  class="d-flex flex-column-fluid">
    <!--begin::Container-->
    <div class="container">
        <div class="row px-8 mb-6">
            <div
                class="label label-light-danger font-weight-bolder py-8 px-12 font-size-h6"
                style="width: fit-content;">
                Procedi con cautela, questa sezione è riservata agli utenti con privilegi di amministratore.
            </div>
        </div>
        {#if $userData.is_superuser}
            <div class="px-4 bg-white">
                <h1 class="mb-12">Amministrazione clienti</h1>

                <BKNDatatable
                    bind:datatable
                    {columns}
                    url={__bakney.env.API.SPORT_ASSOCIATIONS}
                    responsive={false}
                    serverPaging={false}
                    serverFiltering={false}
                    serverSorting={false}
                    pageSizeSelect={[10, 20, 30, 50, 100, 200, 500, 1000]}
                    showDividerFilter={false}
                    loadFilters={() => {
                        setTimeout(() => {
                            initTooltips(document.body);
                            initPopovers(document.body);
                        }, 300);
                    }}
                />
            </div>
        {:else}
            <h1>403</h1>
        {/if}
    </div>
</div>
