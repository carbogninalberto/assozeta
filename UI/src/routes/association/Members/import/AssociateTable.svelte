<script>
    import ApproveButton from 'components/buttons/ApproveButton.svelte';
    import {sessionToken, userData} from 'store/stores.js';
    import {apiFetch, replaceUID} from 'utils/ApiMiddleware';
    import {onDestroy, onMount} from 'svelte';
    import {writable} from 'svelte/store';
    import {slide} from 'svelte/transition';
    import {ArrowCounterClockwise, CloudArrowUp} from 'phosphor-svelte';
    import {toast} from 'svelte-sonner';
    import {waitForElementAndExecute} from 'utils/Functions';
    import EditButton from 'components/buttons/EditButton.svelte';
    import DeleteButton from 'components/buttons/DeleteButton.svelte';
    import {canPerformAction} from 'utils/Permissions';
    import ApproveModal from './modals/ApproveModal.svelte';
    import EditDataDrawer from './EditDataDrawer.svelte';
    import BKNDatatable from 'components/tables/BKNDatatable.svelte';
    import { initTooltips, destroyTooltips } from 'shim/tooltip.js';
    import { initPopovers, destroyPopovers } from 'shim/popover.js';
    import {initSelectpicker} from 'shim/select.js';
	import { UiApp, UiUtil } from 'shim/ui.js';
    sessionToken.useLocalStorage();

    const statusTextDictionary = {
        0: '<span class="label label-light-warning label-inline font-weight-bolder label-lg">Dati Mancanti</span>',
        1: '<span class="label label-light-success label-inline font-weight-bolder label-lg">Valido</span>',
    };

    let rowData = writable([]);
    let datatable;
    let visibleMultiaction = false;
    let selectedCounter = 0;
    let approving = false;
    let importing = false;

    let ready = false;
    let datatableKey = 0;

    const columns = [
        {
            field: 'associate_import_draft_id',
            title: '#',
            sortable: false,
            autoHide: false,
            width: 20,
            selector: {
                class: '',
            },
            textAlign: 'center',
        },
        {
            field: 'associate',
            title: 'Socio',
            autoHide: false,
            fireClick: true,
            sortable: false,
            template: function (row) {
                return (
                    `<span class="navi-text font-weight-bolder text-hover-primary">` +
                    row.associate.first_name +
                    ' ' +
                    row.associate.last_name +
                    '</span>'
                );
            },
        },
        {
            field: 'valid',
            title: 'Stato',
            width: 120,
            fireClick: true,
            minWidth: '100%',
            sortCallback: function (data, sort, column) {
                let statusArray = Object.values(data);

                statusArray.sort(function (a, b) {
                    let timeA = parseInt(a['valid'] ? 1 : 0);
                    let timeB = parseInt(b['valid'] ? 1 : 0);
                    if (sort === 'asc') {
                        return timeA > timeB ? 1 : timeA < timeB ? -1 : 0;
                    } else {
                        return timeA < timeB ? 1 : timeA > timeB ? -1 : 0;
                    }
                });
                let newData = {};
                for (let i = 0; i < statusArray.length; i++) {
                    newData[i] = statusArray[i];
                }

                return newData;
            },
            template: function (row) {
                return statusTextDictionary[row.valid ? 1 : 0];
            },
        },
        {
            field: 'Azioni',
            title: '',
            width: 120,
            sortable: false,
            overflow: 'visible',
            textAlign: 'right',
            autoHide: false,
            minWidth: '100%',
            template: function (row) {
                let disable = row.valid ? false : true;
                waitForElementAndExecute(`#action-col-${row.associate_import_draft_id}`, () => {
                    if (document.querySelector(`#action-col-${row.associate_import_draft_id}`))
                        document.querySelector(`#action-col-${row.associate_import_draft_id}`).innerHTML = '';

                    let approveBtn = new ApproveButton({
                        target: document.querySelector(`#action-col-${row.associate_import_draft_id}`),
                        intro: true,
                        props: {
                            hidden: false,
                            disabled: disable,
                            popover_text: 'Approva Bozza',
                        },
                    });
                    approveBtn.$on('open', data => {
                        let approveModal = new ApproveModal({
                            target: document.querySelector(`#action-col-${row.associate_import_draft_id}`),
                            intro: true,
                            props: {
                                id: row.associate_import_draft_id,
                                show: true,
                            },
                        });

                        approveModal.$on('confirm', data => {
                            window.approveAssociate(row.associate_import_draft_id, false, false, data.detail);
                        });
                    });

                    let editBtn = new EditButton({
                        target: document.querySelector(`#action-col-${row.associate_import_draft_id}`),
                        intro: true,
                        props: {
                            hidden: false,
                            disabled: false,
                            popover_text: 'Modifica Bozza',
                        },
                    });

                    editBtn.$on('open', data => {
                        let basicDrawer = new EditDataDrawer({
                            target: document.querySelector(`#drawer-elements`),
                            intro: true,
                            props: {
                                athlete: row,
                                idx: row.associate_import_draft_id,
                                title: `${row.associate?.first_name || ''} ${row.associate?.last_name || ''}`,
                            },
                        });
                        basicDrawer.$on('close', () => {
                            datatable.reload();
                        });
                    });

                    let deleteBtn = new DeleteButton({
                        target: document.querySelector(`#action-col-${row.associate_import_draft_id}`),
                        intro: true,
                        props: {
                            hidden: false,
                            disabled: false,
                            popover_text: 'Elimina Bozza',
                        },
                    });

                    deleteBtn.$on('open', data => {
                        window.deleteAssociate(row.associate_import_draft_id);
                    });
                });

                return `<div id="action-col-${row.associate_import_draft_id}" class="info-column pr-4"></div>`;
            },
        },
    ];

    async function checkImportingApproving() {
        let res = await apiFetch(__bakney.env.API.SUBSCRIPTION.ASSOCIATES_DRAFT.LIST);
        if (res.status == 200) {
            console.log(res.response);
            approving = res.response.approving;
            importing = res.response.importing;
        }
    }

    onMount(async function () {
        await checkImportingApproving();
        if (!approving && !importing) {
            ready = true;
        }
        initTooltips(document.body);
        initPopovers(document.body);
    });

    onDestroy(() => {
        document.querySelectorAll('.popover').forEach(popover => popover.remove());
    });

    function deleteSelected() {
        swal.fire({
            text: `Vuoi eliminare ${selectedCounter} bozze selezionate?`,
            icon: 'question',
            buttonsStyling: true,
            showCancelButton: true,
            cancelButtonText: 'Annulla',
            confirmButtonText: 'Elimina',
            reverseButtons: true,
        }).then(async function (result) {
            if (result.isConfirmed) {
                let records = datatable.getSelectedRecords().dataSet;
                let checkedNodes = datatable?.getSelectedRecords();
                let count = checkedNodes.length;
                let associate_import_draft_ids = [];
                if (count > 0) {
                    for (let i = 0; i < count; i++) {
                        let id = checkedNodes[i].dataset.row;
                        associate_import_draft_ids.push(records[id].associate_import_draft_id);
                    }
                }
                UiApp.blockPage({
                    overlayColor: '#000000',
                    state: 'primary',
                    message: 'Eliminazione in corso...',
                });
                let res = await apiFetch(__bakney.env.API.SUBSCRIPTION.ASSOCIATES_DRAFT.BULK_DELETE, {
                    method: 'DELETE',
                    body: JSON.stringify({associate_import_draft_ids: associate_import_draft_ids}),
                });

                UiApp.unblockPage();
                if (res.status == 200) {
                    if (res.response?.deleted_count == res.response?.total_count) {
                        toast.success(res.response.msg ?? 'Bozze Eliminate.');
                    } else {
                        toast.warning(
                            res.response.msg ??
                                `${res.response?.delete_count} Bozze Eliminate su ${associate_import_draft_ids.length}.`
                        );
                    }
                } else {
                    toast.error(res.response.msg ?? "Errore durante l'eliminazione delle bozze.");
                }
                datatable.reload();
            }
        });
    }

    async function approveSelected(all = false) {
        let approveModal = new ApproveModal({
            target: document.querySelector('body'),
            intro: true,
            props: {
                id: 'selected',
                show: true,
            },
        });

        approveModal.$on('confirm', async data => {
            let records = datatable.getSelectedRecords().dataSet;
            let checkedNodes = datatable?.getSelectedRecords();
            let count = checkedNodes.length;
            UiApp.blockPage({
                overlayColor: '#000000',
                state: 'primary',
                message: 'Approvazione in corso...',
            });
            if (count > 0 && !all) {
                // put on array all records[id].associate_import_draft_id
                let associate_import_draft_ids = [];
                for (let i = 0; i < count; i++) {
                    let id = checkedNodes[i].dataset.row;
                    associate_import_draft_ids.push(records[id].associate_import_draft_id);
                }

                let res = await apiFetch(__bakney.env.API.SUBSCRIPTION.ASSOCIATES_DRAFT.APPROVE, {
                    method: 'POST',
                    body: JSON.stringify({
                        associate_import_draft_ids: associate_import_draft_ids,
                        only_valid: false,
                        subscription_details: data.detail,
                    }),
                });
                datatable?.reload();

                if (res.status == 400) {
                    toast.warning(res.response.msg ?? 'Dati mancanti, ricontrolla la bozza.');
                } else {
                    toast.success(res.response.msg ?? 'Atleti approvati.');
                }
            } else if (all) {
                let res = await apiFetch(__bakney.env.API.SUBSCRIPTION.ASSOCIATES_DRAFT.APPROVE, {
                    method: 'POST',
                    body: JSON.stringify({
                        all: true,
                        associate_import_draft_ids: null,
                        only_valid: false,
                        subscription_details: data.detail,
                    }),
                });

                datatable?.reload();

                if (res.status == 400) {
                    toast.warning(res.response.msg ?? 'Dati mancanti, ricontrolla la bozza.');
                } else {
                    toast.success(res.response.msg ?? 'Atleti approvati.');
                }
            }
            UiApp.unblockPage();
        });
    }

    async function deleteRow(id, skipReload = false, skipToast = false) {
        if (!skipToast)
            UiApp.blockPage({
                overlayColor: '#000000',
                state: 'primary',
                message: 'Eliminazione in corso...',
            });

        const url = replaceUID(__bakney.env.API.SUBSCRIPTION.ASSOCIATES_DRAFT.DELETE, id);
        const res = await window.fetch(url, {
            method: 'POST',
            headers: {
                Accept: 'application/json',
                'Content-Type': 'application/json',
                Authorization: 'Bearer ' + $sessionToken,
            },
        });
        // spinner stop
        if (!skipToast) UiApp.unblockPage();
        if (res.status == 200) {
            if (!skipToast) toast.success('Associato Eliminato.');
            if (!skipReload) datatable?.reload();
        } else {
            let modalText =
                res.status == 403 ? 'Operazione non permessa.' : 'Scusa, ho individuato degli errori, riprova.';
            swal.fire({
                text: modalText,
                icon: 'error',
                buttonsStyling: false,
                confirmButtonText: 'Ok!',
                customClass: {
                    confirmButton: 'btn font-weight-bold btn-light-primary',
                },
            }).then(function () {
                UiUtil.scrollTop();
            });
        }
        return res;
    }

    window.deleteAssociate = (id, skip = false) => {
        if (skip) return deleteRow(id, true, true);
        swal.fire({
            text: "Vuoi eliminare l'associato?",
            icon: 'question',
            buttonsStyling: true,
            showCancelButton: true,
            cancelButtonText: 'Annulla',
            confirmButtonText: 'Elimina',
            reverseButtons: true,
        }).then(function (result) {
            if (result.isConfirmed) {
                deleteRow(id, skip, true);
            }
        });
    };

    window.approveAssociate = async (id, skipReload = false, skipToast = false, otherData = {}) => {
        if (!skipToast)
            UiApp.blockPage({
                overlayColor: '#000000',
                state: 'primary',
                message: 'Approvazione in corso...',
            });

        let athlete;

        for (let i = 0; i < $rowData.length; i++) {
            if ($rowData[i].associate_import_draft_id == id) {
                athlete = $rowData[i];
            }
        }

        const url = __bakney.env.API.SUBSCRIPTION.ADD;

        // check if new_user_account is in athlete
        if (!athlete.new_user_account) {
            athlete.new_user_account = {
                new_member: false,
            };
        }

        // check if medical_certificate is in athlete
        if (!athlete.medical_certificate) {
            athlete.medical_certificate = {
                medical_id: null,
                filename: '',
            };
        }
        // transform certificate_expring_date if in medical_certificate in YYYY-MM-DD with moment
        if (athlete.medical_certificate.certificate_expring_date) {
            athlete.medical_certificate.certificate_expring_date = moment(
                athlete.medical_certificate.certificate_expring_date,
                'DD/MM/YYYY'
            ).format('YYYY-MM-DD');
        }

        if (!athlete.signature) {
            athlete.signature = {
                there_is_signature: false,
                data: '',
            };
        }

        let bodyRequest = {
            new_user_account: athlete.new_user_account,
            associate_data: athlete.associate,
            associate_tutor_data: athlete.associate_tutor,
            medical_certificate: athlete.medical_certificate,
            signature: athlete.signature,
            ...otherData,
        };

        bodyRequest.associate_data.type = bodyRequest.type ?? 1;
        bodyRequest.associate_data.role = bodyRequest.role ?? 1;

        const res = await apiFetch(url, {
            method: 'POST',
            body: JSON.stringify(bodyRequest),
        });
        // spinner stop
        if (!skipToast) UiApp.unblockPage();

        if (res.status == 200) {
            location.heref = '/#/members/list';
            if (!skipToast) toast.success('Nuova iscrizione creata con successo.');
            window.deleteAssociate(id, true);
            if (!skipReload) {
                datatable?.reload();
            }
        } else if (res.status == 400) {
            swal.fire({
                html:
                    res?.response?.msg ??
                    'Alcuni dati inseriti non sono validi, ricontrollali: <br> <code>' +
                        JSON.stringify(res?.response) +
                        '</code>',
                icon: 'warning',
                buttonsStyling: false,
                confirmButtonText: 'Ok',
                customClass: {
                    confirmButton: 'btn font-weight-bold btn-light-primary',
                },
            }).then(function () {
                UiUtil.scrollTop();
            });
        } else {
            swal.fire({
                text: 'Scusa, ho individuato degli errori, riprova.',
                icon: 'error',
                buttonsStyling: false,
                confirmButtonText: 'Ok!',
                customClass: {
                    confirmButton: 'btn font-weight-bold btn-light-primary',
                },
            }).then(function () {
                UiUtil.scrollTop();
            });
        }
        return res;
    };
</script>

<!--begin::Card-->
<div class="card card-custom gutter-b">
    <div class="card-header flex-wrap border-0 p-0">
        <div class="card-title">
            <h1 class="card-label font-size-h1 font-weight-bolder">
                Bozze
                <span class="d-block text-muted pt-2 font-size-sm">Bozze delle iscrizioni.</span>
            </h1>
        </div>
        <div class="card-toolbar">
            {#if canPerformAction('association.members.create')}
                <a href="/#/members/import" class="btn btn-primary font-weight-boldest m-2 d-flex align-items-center">
                    <CloudArrowUp size="19" weight="duotone" class="mr-2" />
                    Importa atleti
                </a>
            {/if}
        </div>
    </div>
    <div class="card-body p-0">
        <!--begin: Search Form-->
        <!--begin: Datatable-->
        <!-- svelte-ignore a11y-click-events-have-key-events -->
        <!-- svelte-ignore a11y-missing-attribute -->
        {#if approving}
            <div class="alert alert-custom alert-light-warning fade show" role="alert">
                <div class="alert-text font-weight-boldest font-size-h4 d-flex justify-content-start">
                    <div class="spinner spinner-track spinner-warning mr-10" />
                    Approvazione in corso, attendi qualche istante...
                </div>
                <a
                    on:click={() => window.location.reload()}
                    class="btn btn-sm btn-warning font-weight-bold font-size-sm"
                    ><ArrowCounterClockwise size={14} weight="bold" /> ricarica
                </a>
            </div>
        {/if}
        <!-- svelte-ignore a11y-click-events-have-key-events -->
        <!-- svelte-ignore a11y-missing-attribute -->
        {#if importing}
            <div class="alert alert-custom alert-light-warning fade show" role="alert">
                <div class="alert-text font-weight-boldest font-size-h4 d-flex justify-content-start">
                    <div class="spinner spinner-track spinner-warning mr-10" />
                    Inserimento atleti in corso, attendi qualche istante...
                </div>
                <a
                    on:click={() => window.location.reload()}
                    class="btn btn-sm btn-warning font-weight-bold font-size-sm"
                    ><ArrowCounterClockwise size={14} weight="bold" /> ricarica
                </a>
            </div>
        {:else}
            {#if ready}
            {#key datatableKey}
            <BKNDatatable
                bind:datatable
                bind:selectedCounter
                bind:visibleMultiaction
                id="bkn_datatable_imported"
                searchId="bkn_datatable_search_query_imported"
                {columns}
                url={__bakney.env.API.SUBSCRIPTION.ASSOCIATES_DRAFT.LIST}
                serverPaging={false}
                serverFiltering={false}
                serverSorting={false}
                pageSizeSelect={[10, 20, 30, 50, 100, 200]}
                showDividerFilter={false}
                mapFunction={function (raw) {
                    var dataSet = raw;
                    if (typeof raw.data !== 'undefined') {
                        dataSet = raw.data;
                        $rowData = [...Object.values(dataSet)];
                    }
                    if (raw.approving || raw.importing) {
                        dataSet = {};
                        $rowData = [];
                    }
                    approving = raw.approving;
                    importing = raw.importing;
                    return dataSet;
                }}
                clicked={function (td, row) {
                    let basicDrawer = new EditDataDrawer({
                        target: document.querySelector('#drawer-elements'),
                        intro: true,
                        props: {
                            athlete: row,
                            idx: row.associate_import_draft_id,
                            title: `${row.associate?.first_name || ''} ${row.associate?.last_name || ''}`,
                        },
                    });
                    basicDrawer.$on('close', () => {
                        datatable.reload();
                    });
                }}
                loadFilters={() => {
                    const statusEl = document.getElementById('bkn_datatable_search_status_imported');
                    statusEl?.addEventListener('change', function (e) {
                        datatable.search(e.currentTarget.value.toLowerCase(), 'valid');
                    });
                    initSelectpicker(statusEl);

                    const currentYearEl = document.getElementById('bkn_datatable_search_current_year');
                    currentYearEl?.addEventListener('change', function (e) {
                        datatable.search(e.currentTarget.value.toLowerCase(), 'current_year');
                    });
                    initSelectpicker(currentYearEl);
                }}>
                <div slot="search-header">
                    <div class="my-1 my-md-0 mr-2">
                        <!-- svelte-ignore a11y-label-has-associated-control -->
                        <select class="form-control form-control-solid mb-0" id="bkn_datatable_search_status_imported">
                            <option value="">Tutti gli stati</option>
                            <option value="false">Dati Mancanti</option>
                            <option value="true">Valido</option>
                        </select>
                    </div>
                </div>
                <div slot="multiactions">
                    {#if visibleMultiaction}
                        <div in:slide={{duration: 100}} class="mt-10 mb-5" id="bkn_datatable_group_action_form_imported">
                            <!-- svelte-ignore a11y-click-events-have-key-events -->
                            <!-- svelte-ignore a11y-no-static-element-interactions -->
                            <div class="d-flex align-items-center">
                                <div class="font-weight-bold mr-3" style="font-size:1.1rem;">
                                    {selectedCounter} selezionati
                                </div>
                                <span
                                    on:click={deleteSelected}
                                    class="btn btn-sm btn-light-danger font-weight-bolder"
                                    style="margin:0;"
                                    type="button"
                                    id="bkn_datatable_delete_all">
                                    Elimina
                                </span>
                                <span
                                    on:click={() => approveSelected(false)}
                                    class="btn btn-sm btn-light-success font-weight-bolder m-0 ml-2"
                                    type="button"
                                    id="bkn_datatable_approve_selected">
                                    Approva selezionati
                                </span>
                                <span
                                    on:click={() => approveSelected(true)}
                                    class="btn btn-sm btn-light-primary font-weight-bolder m-0 ml-2"
                                    type="button"
                                    id="bkn_datatable_approve_all">
                                    Approva tutti
                                </span>
                            </div>
                        </div>
                    {/if}
                </div>
            </BKNDatatable>
            {/key}
            {/if}
        {/if}
        <!--end: Datatable-->
    </div>
</div>
