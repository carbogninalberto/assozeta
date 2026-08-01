<script>
	import { X } from 'lucide-svelte';
    import {apiFetch, replaceUID} from 'utils/ApiMiddleware';
    import Portal from 'svelte-portal';
    import {getDataFromForm} from 'utils/Functions';
    import {onMount} from 'svelte';
    import Section1 from '../add/sections/Section1.svelte';
    import Select from 'svelte-select/Select.svelte';
    import {toast} from 'svelte-sonner';
    import {blockPage, unblockPage} from 'store/loadingStore.js';
    const scrollToTop = () => window.scrollTo({ top: 0, behavior: 'smooth' });
    import {initSelectpicker} from 'shim/select.js';
    import {hideModal} from 'shim/modal.js';

    export let id;
    export let row;
    export let datatableHandle;

    let editForm;
    let hideGenerateInvoice = false;

    function initForm() {
        editForm?.destroy();
        editForm = FormValidation.formValidation(document.getElementById('form_edit_' + id), {
            fields: {
                first_name: {
                    validators: {
                        notEmpty: {
                            message: 'Il nome è obbligatorio.',
                        },
                    },
                },
                last_name: {
                    validators: {
                        notEmpty: {
                            message: 'Il cognome è obbligatorio.',
                        },
                    },
                },
                email: {
                    validators: {
                        notEmpty: {
                            message: "L'email è obbligatoria.",
                        },
                        emailAddress: {
                            message: "L'email non è in formato valido.",
                        },
                    },
                },
                phone: {
                    validators: {
                        regexp: {
                            regexp: /^\+?\d{7,15}$/,
                            message: 'Il numero di telefono non è valido.',
                        },
                    },
                },
                phone_2: {
                    validators: {
                        regexp: {
                            regexp: /^\+?\d{7,15}$/,
                            message: 'Il numero di telefono non è valido.',
                        },
                    },
                },
                phone_3: {
                    validators: {
                        regexp: {
                            regexp: /^\+?\d{7,15}$/,
                            message: 'Il numero di telefono non è valido.',
                        },
                    },
                },
                phone_4: {
                    validators: {
                        regexp: {
                            regexp: /^\+?\d{7,15}$/,
                            message: 'Il numero di telefono non è valido.',
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

    async function update(data) {
        try {
            blockPage({
                overlayColor: '#000000',
                state: 'primary',
                message: 'Modifica in corso...',
            });

            if (data.complex_item) data.complex_item = JSON.parse(data.complex_item);

            if (
                data.associated_user_id == '' ||
                data.associated_user_id == null ||
                data.associated_user_id == undefined ||
                data.associated_user_id == 'null' ||
                data.associated_user_id == 'undefined'
            )
                data.associated_user_id = null;

            if (data.born_date) data.born_date = moment(data.born_date).format('DD/MM/YYYY');
            else data.born_date = null;
            if (data.stipulated_contract_in)
                data.stipulated_contract_in = moment(data.stipulated_contract_in).format('DD/MM/YYYY');
            else data.stipulated_contract_in = null;
            const url = replaceUID(__bakney.env.API.INSTRUCTOR.UPDATE, id);

            const res = await apiFetch(url, {
                method: 'PATCH',
                body: JSON.stringify(data),
            });

            if (res.status == 200) {
                toast.success('Modificato con successo.');
                datatableHandle.reload();
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
        } finally {
            unblockPage();
        }
    }

    function handleValidation(e) {
        if (!editForm) initForm();
        editForm?.validate().then(function (status) {
            if (status === 'Valid') {
                update(getDataFromForm(e));
                hideModal('editModal-' + id);
            } else {
                swal.fire({
                    text: 'Per favore, inserisci tutti i dati e riprova.',
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
        });
    }

    onMount(() => {
        initSelectpicker(document.getElementById('type_' + id));
    });
</script>

<!-- svelte-ignore missing-declaration -->
<Portal>
    <!-- Modal-->
    <form class="form" id="form_edit_{id}" on:submit|preventDefault={handleValidation}>
        <div
            class="modal fade"
            id="editModal-{id}"
            tabindex="-1"
            role="dialog"
            aria-labelledby="staticBackdrop"
            aria-hidden="true">
            <div class="modal-dialog modal-dialog-centered modal-lg modal-dialog-scrollable" role="document">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title" id="exampleModalLabel">Modifica Istruttore</h5>
                        <button type="button" class="close" data-dismiss="modal" aria-label="Close">
                            <X size={16} aria-hidden="true" />
                        </button>
                    </div>
                    <div class="modal-body" style="min-height: 370px;">
                        <Section1 inModal={true} bind:data={row} />
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-light-primary font-weight-bold" data-dismiss="modal"
                            >Chiudi</button>
                        <button type="submit" class="btn btn-primary font-weight-bold">Salva</button>
                    </div>
                </div>
            </div>
        </div>
    </form>
</Portal>
