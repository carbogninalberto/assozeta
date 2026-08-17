<script>
	import { X } from 'lucide-svelte';
    import EditButton from 'components/buttons/EditButton.svelte';
    import BKNDatatable from 'components/tables/BKNDatatable.svelte';
    import Portal from 'svelte-portal';
    import {sessionToken, userData} from 'store/stores.js';
    import {scale} from 'svelte/transition';
    import * as easing from 'svelte/easing';
    import {onMount, onDestroy} from 'svelte';
    import {replaceUID} from 'utils/ApiMiddleware.js';
    import {PlusCircle, Warning} from 'phosphor-svelte';
    import {getDataFromForm, waitForElementAndExecute} from 'utils/Functions';
    import {apiFetch} from 'utils/ApiMiddleware';
    import DeleteButton from 'components/buttons/DeleteButton.svelte';
    import PauseButton from 'components/buttons/PauseButton.svelte';
    import PlayButton from 'components/buttons/PlayButton.svelte';
    import {getAthletesEmails} from 'utils/Functions';
    import {push} from 'svelte-spa-router';
    import {canPerformAction} from 'utils/Permissions';
    import {toast} from 'svelte-sonner';
    const scrollToTop = () => window.scrollTo({ top: 0, behavior: 'smooth' });
import {blockPage, unblockPage} from 'store/loadingStore.js';
    import { initTooltips, destroyTooltips } from 'shim/tooltip.js';
    import { initPopovers, destroyPopovers } from 'shim/popover.js';
    import {hideModal} from 'shim/modal.js';

    sessionToken.useLocalStorage();

    let messageForm;
    let postForm;
    let type = 'EMAIL';
    let sendNow = false;

    const typeDictionary = {
        EMAIL: '<span class="label label-light-primary label-inline font-weight-bolder label-lg">Email</span>',
        INSIDE_APP: '<span class="label label-light-success label-inline font-weight-bolder label-lg">Post</span>',
    };

    function resetForm() {
        type = 'EMAIL';
        sendNow = false;
        // reset the form values
        document.getElementById('communications_form').reset();
        document.getElementById('post_form').reset();
        // reset the form validation
        messageForm = null;
        postForm = null;
    }

    async function create(data) {
        blockPage({
            overlayColor: '#000000',
            state: 'primary',
            message: 'Creazione...',
        });

        const url = sendNow
            ? __bakney.env.API.COMMUNICATIONS.SEND.EMAIL
            : __bakney.env.API.COMMUNICATIONS.MESSAGES.ADD;

        const res = await apiFetch(url, {
            method: 'POST',
            body: JSON.stringify(data),
        });
        // spinner stop
        unblockPage();

        if (res.status == 200) {
            datatable.reload();
            resetForm();

            toast.success('Creato con successo.');
        } else {
            swal.fire({
                text: 'Scusa, ho individuato degli errori, riprova.',
                icon: 'error',
                buttonsStyling: false,
                confirmButtonText: 'Ok, capito!',
                customClass: {
                    confirmButton: 'btn font-weight-bold btn-light-primary',
                },
            }).then(function () {
                scrollToTop();
            });
        }

        resetForm();
    }

    async function createPost(data) {
        blockPage({
            overlayColor: '#000000',
            state: 'primary',
            message: 'Creazione...',
        });

        const url = __bakney.env.API.COMMUNICATIONS.SEND.POST;

        const res = await apiFetch(url, {
            method: 'POST',
            body: JSON.stringify(data),
        });
        // spinner stop
        unblockPage();

        if (res.status == 200) {
            datatable.reload();
            resetForm();

            toast.success('Creato con successo.');
        } else {
            swal.fire({
                text: 'Scusa, ho individuato degli errori, riprova.',
                icon: 'error',
                buttonsStyling: false,
                confirmButtonText: 'Ok, capito!',
                customClass: {
                    confirmButton: 'btn font-weight-bold btn-light-primary',
                },
            }).then(function () {
                scrollToTop();
            });
        }
    }

    let datatable;

    const columns = [
        {
            field: 'name',
            title: 'Nome',
            sortable: true,
            width: 200,
            autoHide: false,
            template: function (row) {
                return '<p class="text-dark-75 font-weight-bolder mb-0">' + row.name + '</p>';
            },
        },
        {
            field: 'enabled',
            title: 'Stato',
            sortable: true,
            width: 150,
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            template: function (row) {
                // if row.enabled set label-success else label-primary
                return (
                    '<span class="label label-inline font-weight-bolder label-lg label-light-' +
                    (row.enabled ? 'success' : 'info') +
                    '">' +
                    (row.enabled ? 'Attiva' : 'Spenta') +
                    '</span>'
                );
            },
        },
        {
            field: 'created_at',
            title: 'Data',
            sortable: true,
            width: 150,
            responsive: {
                visible: 'xl',
                hidden: 'lg',
            },
            template: function (row) {
                return (
                    '<p class="text-dark-75 font-weight-bolder mb-0">' +
                    new Date(row.created_at).toLocaleString() +
                    '</p>'
                );
            },
        },
        {
            field: '',
            title: '',
            sortable: false,
            textAlign: 'right',
            autoHide: false,
            width: 110,
            minWidth: '100%',
            template: function (row) {
                waitForElementAndExecute(`#action-col-${row.automation_workflow_id}`, () => {
                    if (document.querySelector(`#action-col-${row.automation_workflow_id}`))
                        document.querySelector(`#action-col-${row.automation_workflow_id}`).innerHTML = '';

                    // play or pause button, if enabled show pause button else show play button

                    if (row.enabled) {
                        let pauseBtn = new PauseButton({
                            target: document.querySelector(`#action-col-${row.automation_workflow_id}`),
                            intro: true,
                            props: {
                                disabled: !canPerformAction('association.communication.workflows.update'),
                                // hidden: !row.editable,
                            },
                        });

                        pauseBtn.$on('open', data => {
                            swal.fire({
                                text: "Vuoi disattivare l'automazione?",
                                icon: 'warning',
                                buttonsStyling: true,
                                showCancelButton: true,
                                cancelButtonText: 'Annulla',
                                confirmButtonText: 'Disattiva',
                                reverseButtons: true,
                                confirmButtonColor: '#d63030',
                            }).then(async function (result) {
                                if (result.isConfirmed) {
                                    blockPage({
                                        overlayColor: '#000000',
                                        state: 'primary',
                                        message: 'Disattivazione in corso...',
                                    });

                                    const response = await apiFetch(
                                        replaceUID(
                                            __bakney.env.API.COMMUNICATIONS.WORKFLOWS.UPDATE,
                                            row.automation_workflow_id
                                        ),
                                        {
                                            method: 'PATCH',
                                            body: JSON.stringify({
                                                enabled: false,
                                            }),
                                        }
                                    );

                                    unblockPage();

                                    if (!response.error) {
toast.success('Disattivata!');
                                        datatable.reload();
                                    } else {
                                        toast.error('Qualcosa è andato storto.');
                                    }
                                }
                            });
                        });
                    } else {
                        let playBtn = new PlayButton({
                            target: document.querySelector(`#action-col-${row.automation_workflow_id}`),
                            intro: true,
                            props: {
                                disabled: !canPerformAction('association.communication.workflows.update'),
                                // hidden: !row.editable,
                            },
                        });

                        playBtn.$on('open', data => {
                            swal.fire({
                                text: "Vuoi attivare l'automazione?",
                                icon: 'warning',
                                buttonsStyling: true,
                                showCancelButton: true,
                                cancelButtonText: 'Annulla',
                                confirmButtonText: 'Attiva',
                                reverseButtons: true,
                                confirmButtonColor: '#d63030',
                            }).then(async function (result) {
                                if (result.isConfirmed) {
                                    blockPage({
                                        overlayColor: '#000000',
                                        state: 'primary',
                                        message: 'Attivazione in corso...',
                                    });

                                    const response = await apiFetch(
                                        replaceUID(
                                            __bakney.env.API.COMMUNICATIONS.WORKFLOWS.UPDATE,
                                            row.automation_workflow_id
                                        ),
                                        {
                                            method: 'PATCH',
                                            body: JSON.stringify({
                                                enabled: true,
                                            }),
                                        }
                                    );

                                    unblockPage();

                                    if (!response.error) {
toast.success('Attivata!');
                                        datatable.reload();
                                    } else {
                                        toast.error('Qualcosa è andato storto.');
                                    }
                                }
                            });
                        });
                    }

                    let editBtn = new EditButton({
                        target: document.querySelector(`#action-col-${row.automation_workflow_id}`),
                        intro: true,
                        props: {
                            disabled: !canPerformAction('association.communication.workflows.update'),
                            // hidden: !row.editable,
                        },
                    });

                    editBtn.$on('open', data => {
                        // go to edit page
                        push(`/communication/automation/editor/${row.automation_workflow_id}`);
                    });

                    let deleteBtn = new DeleteButton({
                        target: document.querySelector(`#action-col-${row.automation_workflow_id}`),
                        intro: true,
                        props: {
                            disabled: !canPerformAction('association.communication.workflows.delete'),
                            // hidden: !row.editable,
                        },
                    });

                    deleteBtn.$on('open', data => {
                        swal.fire({
                            text: "Vuoi eliminare l'automazione?",
                            icon: 'warning',
                            buttonsStyling: true,
                            showCancelButton: true,
                            cancelButtonText: 'Annulla',
                            confirmButtonText: 'Elimina',
                            reverseButtons: true,
                            confirmButtonColor: '#d63030',
                        }).then(async function (result) {
                            if (result.isConfirmed) {
                                blockPage({
                                    overlayColor: '#000000',
                                    state: 'primary',
                                    message: 'Eliminazione in corso...',
                                });

                                const response = await apiFetch(
                                    replaceUID(
                                        __bakney.env.API.COMMUNICATIONS.WORKFLOWS.DELETE,
                                        row.automation_workflow_id
                                    ),
                                    {
                                        method: 'DELETE',
                                    }
                                );

                                unblockPage();

                                if (!response.error) {
toast.success('Eliminato!');
                                    datatable.reload();
                                } else {
                                    toast.error('Qualcosa è andato storto.');
                                }
                            }
                        });
                    });
                });
                return `<div id="action-col-${row.automation_workflow_id}" class="action-column pr-4"></div>`;
            },
        },
    ];

    onMount(() => {
        initTooltips(document.body);
        initPopovers(document.body);
    });

    onDestroy(() => {
        document.querySelectorAll('.popover').forEach(popover => popover.remove());
    });

    function initForm() {
        let validationFields = {
                type: {
                    validators: {
                        notEmpty: {
                            message: 'Tipo obbligatorio',
                        },
                        callback: {
                            message: 'Tipo non valido',
                            callback: function (input) {
                                return input.value == 'EMAIL';
                            },
                        },
                    },
                },
                subject: {
                    validators: {
                        notEmpty: {
                            message: 'Oggetto obbligatorio',
                        },
                        stringLength: {
                            min: 5,
                            max: 100,
                            message: "L'oggetto deve essere almeno di 5 caratteri e non può superare i 100",
                        },
                    },
                },
                message: {
                    validators: {
                        notEmpty: {
                            message: 'Contenuto obbligatorio',
                        },
                        stringLength: {
                            min: 20,
                            max: 1000,
                            message: 'Il messaggio deve essere almeno di 20 caratteri e non può superare i 1000',
                        },
                    },
                },
        };

            if (sendNow) {
                validationFields['email'] = {
                    validators: {
                        notEmpty: {
                            message: 'Lista email obbligatoria',
                        },
                        regexp: {
                            regexp: /^([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})(,([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}))*$/,
                            message: 'Inserisci una lista di email valida',
                        },
                    },
                };
            }

        messageForm?.destroy();
        messageForm = FormValidation.formValidation(document.getElementById('communications_form'), {
            fields: validationFields,
            plugins: {
                trigger: new FormValidation.plugins.Trigger(),
                bootstrap: new FormValidation.plugins.Bootstrap(),
            },
        });
    }

    function initPostForm() {
        postForm?.destroy();
        postForm = FormValidation.formValidation(document.getElementById('post_form'), {
            fields: {
                message: {
                    validators: {
                        notEmpty: {
                            message: 'Contenuto obbligatorio',
                        },
                        stringLength: {
                            min: 20,
                            max: 1000,
                            message:
                                'Il messaggio non può superare i 1000 caratteri e deve essere almeno di 20 caratteri',
                        },
                    },
                },
            },
            plugins: {
                trigger: new FormValidation.plugins.Trigger(),
                bootstrap: new FormValidation.plugins.Bootstrap(),
                // submitButton: new FormValidation.plugins.SubmitButton(),
            },
        });
    }

    function handleValidation(e) {
        if (!messageForm) initForm();
        messageForm?.validate().then(function (status) {
            if (status === 'Valid') {
                create(getDataFromForm(e));
                hideModal('addModal');
            }
        });
    }

    function handlePostValidation(e) {
        if (!postForm) initPostForm();
        postForm?.validate().then(function (status) {
            if (status === 'Valid') {
                createPost(getDataFromForm(e));
                hideModal('addModalPost');
            }
        });
    }
</script>

<!--begin::Entry-->
<div
    
    class="d-flex flex-column-fluid font-weight-bold text-dark-50">
    <!--begin::Container-->
    <div class="container">
        <!--begin::Card-->
        <div class="card card-custom gutter-b">
            <div class="card-header flex-wrap border-0 p-0">
                <div class="card-title">
                    <h3 class="card-label font-size-h2">
                        Automazioni
                        <span class="d-block text-muted pt-2 font-size-sm"
                            >In questa sezione sono presenti le automazioni che permettono di semplificare le
                            comunicazioni con i soci.</span>
                    </h3>
                </div>
                <div class="card-toolbar">
                    <!-- svelte-ignore a11y-click-events-have-key-events -->
                    {#if canPerformAction('association.communication.workflows.create')}
                        <!-- svelte-ignore a11y-no-static-element-interactions -->
                        <span
                            class="btn btn-sm btn-primary font-weight-bolder m-2 d-flex align-items-center"
                            on:click={() => {
                                push('/communication/automation/editor');
                            }}>
                            <PlusCircle size={18} weight="duotone" />
                            <span class="ml-md-1 ml-0"><span class="d-none d-md-inline-block">Automazione</span></span>
                        </span>
                    {/if}
                </div>
            </div>
            <div class="card-body p-0">
                <BKNDatatable
                    bind:datatable
                    {columns}
                    url={__bakney.env.API.COMMUNICATIONS.WORKFLOWS.LIST}
                    serverPaging={false}
                    serverFiltering={false}
                    serverSorting={false}
                />
            </div>
        </div>
        <!--end::Card-->
    </div>
    <!--end::Container-->
</div>
<!--end::Entry-->

<!-- svelte-ignore missing-declaration -->
<Portal target="#portal-elements">
    <!-- Modal-->
    <form class="form" id="communications_form" on:submit|preventDefault={handleValidation}>
        <div
            class="modal fade"
            id="addModal"
            tabindex="-1"
            role="dialog"
            aria-labelledby="staticBackdrop"
            aria-hidden="true">
            <div class="modal-dialog modal-dialog-centered modal-dialog-scrollable" role="document">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title" id="exampleModalLabel">Creazione di un messaggio</h5>
                        <button type="button" class="close" data-dismiss="modal" aria-label="Close">
                            <X size={16} aria-hidden="true" />
                        </button>
                    </div>
                    <div class="modal-body pb-0">
                        <!-- select box Tipo messaggi -->
                        <div class="form-group mb-3">
                            <!-- svelte-ignore a11y-label-has-associated-control -->
                            <label class="font-size-h6 font-weight-bolder text-dark">Tipo messaggio</label>
                            <select
                                class="form-control form-control-solid form-control-lg mr-2"
                                name="type"
                                id="type"
                                bind:value={type}
                                required>
                                <option value="EMAIL">Email</option>
                            </select>
                        </div>
                            <!-- Subject -->
                            <div class="form-group mb-0">
                                <!-- svelte-ignore a11y-label-has-associated-control -->
                                <label>Oggetto<b>*</b></label>
                                <input
                                    type="text"
                                    name="subject"
                                    class="form-control form-control-solid form-control-lg margin-t-2"
                                    placeholder="Scrivi l'oggetto dell'email..." />
                            </div>
                            <!-- Content -->
                            <div class="form-group mb-0">
                                <!-- svelte-ignore a11y-label-has-associated-control -->
                                <label>Contenuto<b>*</b></label>
                                <textarea
                                    name="message"
                                    style="resize: none;"
                                    rows="4"
                                    class="form-control form-control-solid form-control-lg margin-t-2"
                                    placeholder="Scrivi cosa devi comunicare ai tuoi atleti..." />
                            </div>
                            <div class="msg-container">
                                <!-- add input to ask if sending message right away, if selected show another text area which will be a list of phone numbers -->
                                <div class="form-group d-flex align-items-center justify-content-between mb-2">
                                    <!-- svelte-ignore a11y-label-has-associated-control -->
                                    <label class="col-form-label font-weight-bold"
                                        >Inviare subito ad una lista email?</label>
                                    <div>
                                        <span class="switch switch-sm switch-icon">
                                            <label>
                                                <input type="checkbox" name="select" bind:checked={sendNow} />
                                                <span />
                                            </label>
                                        </span>
                                    </div>
                                </div>
                                {#if sendNow}
                                    <!-- list of phone numbers with validation on change, pattern should be +39NUMBER,+39NUMBER -->
                                    <div class="form-group">
                                        <!-- svelte-ignore a11y-label-has-associated-control -->
                                        <div class="d-flex justify-content-between">
                                            <label>Lista email<b>*</b></label>
                                            <button
                                                on:click|preventDefault={getAthletesEmails}
                                                class="btn btn-sm btn-light-primary font-weight-bold"
                                                >importa atleti</button>
                                        </div>
                                        <textarea
                                            id="email-textarea"
                                            name="email"
                                            style="resize: y;"
                                            rows="4"
                                            class="form-control form-control-solid form-control-lg margin-t-2"
                                            placeholder="Inserisci le email separate da una virgola...(ad esempio: asd@asd.com,xyz@xyz" />
                                        <!-- svelte-ignore a11y-label-has-associated-control -->
                                        <label style="font-size: 1rem;" class="text-muted text-center"
                                            >Scrivi una lista di email separate da virgola, ad esempio:
                                            asd@asd.com,xyz@xyz</label>
                                    </div>
                                {/if}
                            </div>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-light-primary font-weight-bold" data-dismiss="modal"
                            >Chiudi</button>
                        <button type="submit" class="btn btn-primary font-weight-bold"
                            >{sendNow ? 'Invia' : 'Salva'}</button>
                    </div>
                </div>
            </div>
        </div>
    </form>
</Portal>

<!-- svelte-ignore missing-declaration -->
<Portal target="#portal-elements">
    <!-- Modal-->
    <form class="form" id="post_form" on:submit|preventDefault={handlePostValidation}>
        <div
            class="modal fade"
            id="addModalPost"
            tabindex="-1"
            role="dialog"
            aria-labelledby="staticBackdrop"
            aria-hidden="true">
            <div class="modal-dialog modal-dialog-centered modal-dialog-scrollable" role="document">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title" id="exampleModalLabel">Crea un post</h5>
                        <button type="button" class="close" data-dismiss="modal" aria-label="Close">
                            <X size={16} aria-hidden="true" />
                        </button>
                    </div>
                    <div class="modal-body pb-0">
                        <!-- hidden input type INSIDE_APP -->
                        <input type="hidden" name="type" value="INSIDE_APP" />
                        <div
                            class="d-flex align-items-center text-bold text-warning bg-light-warning p-4 mb-4"
                            style="border-radius: .35rem;">
                            <Warning size={18} weight="duotone" class="mr-2" />
                            Il post sarà visibile a tutti gli atleti iscritti alla tua associazione sportiva.
                        </div>
                        <div class="form-group">
                            <!-- svelte-ignore a11y-label-has-associated-control -->
                            <label>Contenuto<b>*</b></label>
                            <textarea
                                name="message"
                                style="resize: none;"
                                rows="4"
                                class="form-control form-control-solid form-control-lg margin-t-2"
                                placeholder="Scrivi cosa devi comunicare ai tuoi atleti..." />
                        </div>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-light-primary font-weight-bold" data-dismiss="modal"
                            >Chiudi</button>
                        <button type="submit" class="btn btn-primary font-weight-bold">Pubblica</button>
                    </div>
                </div>
            </div>
        </div>
    </form>
</Portal>
