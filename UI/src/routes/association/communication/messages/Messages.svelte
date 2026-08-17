<script>
	import { X } from 'lucide-svelte';
    import Portal from 'svelte-portal';
    import {sessionToken} from 'store/stores.js';
    import {scale} from 'svelte/transition';
    import * as easing from 'svelte/easing';
    import {onMount, onDestroy} from 'svelte';
    import {replaceUID} from 'utils/ApiMiddleware.js';
    import {ChatCircle, NotePencil, Warning} from 'phosphor-svelte';
    import {getDataFromForm, waitForElementAndExecute} from 'utils/Functions';
    import {apiFetch} from 'utils/ApiMiddleware';
    import DeleteButton from 'components/buttons/DeleteButton.svelte';
    import SendButton from 'components/buttons/SendButton.svelte';
    import Send from './modals/Send.svelte';
    import {getAthletesEmails, getAthletesPhones} from 'utils/Functions';
    import {canPerformAction} from 'utils/Permissions';
    import {toast} from 'svelte-sonner';
    const scrollToTop = () => window.scrollTo({ top: 0, behavior: 'smooth' });
import {blockPage, unblockPage} from 'store/loadingStore.js';
    import BKNDatatable from 'components/tables/BKNDatatable.svelte';
    import { initTooltips, destroyTooltips } from 'shim/tooltip.js';
    import { initPopovers, destroyPopovers } from 'shim/popover.js';
    import {initSelectpicker} from 'shim/select.js';
    import {showModal, hideModal} from 'shim/modal.js';

    sessionToken.useLocalStorage();

    let messageForm;
    let postForm;
    let type = 'EMAIL';
    let sendNow = false;

    const typeDictionary = {
        EMAIL: '<span class="label label-light-primary label-inline font-weight-bolder label-lg">Email</span>',
        SMS: '<span class="label label-light-warning label-inline font-weight-bolder label-lg">SMS</span>',
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

        let url = null;

        if (data.type == 'SMS' && sendNow) {
            url = __bakney.env.API.COMMUNICATIONS.SEND.SMS;
        } else if (data.type == 'SMS' && !sendNow) {
            url = __bakney.env.API.COMMUNICATIONS.MESSAGES.ADD;
        } else if (data.type == 'EMAIL' && sendNow) {
            url = __bakney.env.API.COMMUNICATIONS.SEND.EMAIL;
        } else if (data.type == 'EMAIL' && !sendNow) {
            url = __bakney.env.API.COMMUNICATIONS.MESSAGES.ADD;
        }

        if (!url) {
            unblockPage();
            return swal
                .fire({
                    text: 'Scusa, ho individuato degli errori, riprova.',
                    icon: 'error',
                    buttonsStyling: false,
                    confirmButtonText: 'Ok, capito!',
                    customClass: {
                        confirmButton: 'btn font-weight-bold btn-light-primary',
                    },
                })
                .then(function () {
                    scrollToTop();
                });
        }

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
            field: 'message',
            title: 'Contenuto',
            sortable: true,
            width: 300,
            template: function (row) {
                let message = String(row.message || '-').toUpperCase();
                // sanitize message
                // slice message if it's too long
                if (message.length > 25) message = message.slice(0, 25) + '...';
                return (
                    '<p class="text-dark-75 font-weight-bolder mb-0" data-toggle="tooltip" data-placement="bottom" title="' +
                    row.message +
                    '">' +
                    message +
                    '</p>'
                );
            },
        },
        {
            field: 'type',
            title: 'Tipo',
            sortable: true,
            width: 60,
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            template: function (row) {
                return typeDictionary[row.type];
            },
        },
        {
            field: 'subject',
            title: 'Oggetto',
            sortable: true,
            width: 150,
            responsive: {
                visible: 'xl',
                hidden: 'lg',
            },
            template: function (row) {
                let subject = String(row.subject || '-').toUpperCase();
                // slice message if it's too long
                if (subject.length > 15) subject = subject.slice(0, 15) + '...';
                return (
                    '<p class="text-dark-75 font-weight-bolder mb-0"data-toggle="tooltip" data-placement="bottom" title="' +
                    row.subject +
                    '">' +
                    subject +
                    '</p>'
                );
            },
        },
        {
            field: 'created_at',
            title: 'Data',
            sortable: true,
            width: 150,
            responsive: {
                visible: 'lg',
                hidden: 'md',
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
            width: 100,
            minWidth: '100%',
            template: function (row) {
                waitForElementAndExecute(`#action-col-${row.message_id}`, () => {
                    if (document.querySelector(`#action-col-${row.message_id}`))
                        document.querySelector(`#action-col-${row.message_id}`).innerHTML = '';

                    let sendBtn = new SendButton({
                        target: document.querySelector(`#action-col-${row.message_id}`),
                        intro: true,
                        props: {
                            disabled:
                                row.type == 'INSIDE_APP' ||
                                !canPerformAction('association.communication.messages.update'),
                            // hidden: !row.editable,
                        },
                    });

                    let sendModal = new Send({
                        target: document.querySelector(`#action-col-${row.message_id}`),
                        intro: true,
                        props: {
                            id: row.message_id,
                            type: row.type,
                        },
                    });

                    sendBtn.$on('open', data => {
                        showModal(`sendModal${row.message_id}`);
                    });

                    let deleteBtn = new DeleteButton({
                        target: document.querySelector(`#action-col-${row.message_id}`),
                        intro: true,
                        props: {
                            disabled: !canPerformAction('association.communication.messages.delete'),
                            // hidden: !row.editable,
                        },
                    });

                    deleteBtn.$on('open', data => {
                        swal.fire({
                            text: 'Vuoi eliminare il messaggio?',
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
                                    replaceUID(__bakney.env.API.COMMUNICATIONS.MESSAGES.DELETE, row.message_id),
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
                return `<div id="action-col-${row.message_id}" class="action-column pr-4"></div>`;
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
        // check type of message
        let validationFields = {};

        if (type == 'SMS') {
            // type can only be SMS or EMAIL
            validationFields = {
                type: {
                    validators: {
                        notEmpty: {
                            message: 'Tipo obbligatorio',
                        },
                        callback: {
                            message: 'Tipo non valido',
                            callback: function (input) {
                                return input.value == 'SMS' || input.value == 'EMAIL';
                            },
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
                            max: 160,
                            message: 'Il messaggio deve essere almeno di 20 caratteri e non può superare i 160',
                        },
                    },
                },
            };
            if (sendNow) {
                validationFields['phone_number'] = {
                    validators: {
                        notEmpty: {
                            message: 'Lista numeri obbligatoria',
                        },
                        regexp: {
                            regexp: /^[0-9,+\s]*$/,
                            message: 'Inserisci una lista di numeri valida',
                        },
                    },
                };
            }
        } else {
            validationFields = {
                type: {
                    validators: {
                        notEmpty: {
                            message: 'Tipo obbligatorio',
                        },
                        callback: {
                            message: 'Tipo non valido',
                            callback: function (input) {
                                return input.value == 'SMS' || input.value == 'EMAIL';
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
                        Messaggi
                        <span class="d-block text-muted pt-2 font-size-sm"
                            >In questa sezione sono presenti tutte le comunicazioni inviate ai tuoi atleti.</span>
                    </h3>
                </div>
                <div class="card-toolbar">
                    {#if canPerformAction('association.communication.messages.create')}
                        <span
                            data-toggle="modal"
                            data-target="#addModalPost"
                            class="btn btn-sm btn-primary font-weight-bolder m-2 d-flex align-items-center">
                            <NotePencil size={18} weight="duotone" />
                            <span class="ml-md-1 ml-0"><span class="d-none d-md-inline-block">Post</span></span>
                        </span>
                        <span
                            data-toggle="modal"
                            data-target="#addModal"
                            class="btn btn-sm btn-primary font-weight-bolder m-2 d-flex align-items-center">
                            <ChatCircle size={18} weight="duotone" />
                            <span class="ml-md-1 ml-0"><span class="d-none d-md-inline-block">Messaggio</span></span>
                        </span>
                    {/if}
                </div>
            </div>
            <div class="card-body p-0">
                <BKNDatatable
                    bind:datatable
                    {columns}
                    url={__bakney.env.API.COMMUNICATIONS.MESSAGES.LIST}
                    serverPaging={false}
                    serverFiltering={false}
                    serverSorting={false}
                    showDividerFilter={false}
                    loadFilters={() => {
                        const statusEl = document.getElementById('bkn_datatable_search_status');
                        statusEl?.addEventListener('change', function (e) {
                            datatable.search(e.currentTarget.value.toLowerCase(), 'status_flag');
                        });
                        initSelectpicker(statusEl);
                    }} />
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
                                <option value="SMS">SMS</option>
                            </select>
                        </div>
                        {#if type == 'SMS'}
                            <div class="form-group mb-0">
                                <!-- svelte-ignore a11y-label-has-associated-control -->
                                <label>Contenuto (max 160 caratteri)<b>*</b></label>
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
                                        >Inviare subito ad una lista numeri?</label>
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
                                            <label>Lista numeri<b>*</b></label>
                                            <button
                                                on:click|preventDefault={getAthletesPhones}
                                                class="btn btn-sm btn-light-primary font-weight-bold"
                                                >importa atleti</button>
                                        </div>
                                        <textarea
                                            id="phone-textarea"
                                            name="phone_number"
                                            style="resize: y;"
                                            on:keypress={e => {
                                                if (e.key == 'Enter') e.preventDefault();
                                                // check pattern is valid, only numbers and commas and + are allowed
                                                if (!/^[0-9,+\s]*$/.test(e.target.value)) e.target.value = '';
                                                // check is valid +39NUMBER,+39NUMBER,...etc pattern
                                            }}
                                            on:change={e => {
                                                // check pattern is valid, only numbers and commas and + are allowed
                                                if (!/^[0-9,+\s]*$/.test(e.target.value)) e.target.value = '';
                                                // check if there is a comma at the end of the string
                                                if (e.target.value.slice(-1) == ',')
                                                    e.target.value = e.target.value.slice(0, -1);
                                            }}
                                            rows="4"
                                            class="form-control form-control-solid form-control-lg margin-t-2"
                                            placeholder="Inserisci i numeri di telefono separati da una virgola...(ad esempio: +393406601516,+390000000000" />
                                        <!-- svelte-ignore a11y-label-has-associated-control -->
                                        <label style="font-size: 1rem;" class="text-muted text-center"
                                            >Scrivi una lista di numeri col +39 separati da virgola, ad esempio:
                                            +393406601516,+390000000000</label>
                                    </div>
                                {/if}
                            </div>
                        {:else}
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
                        {/if}
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
