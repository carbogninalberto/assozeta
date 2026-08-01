<script>
	import { X } from 'lucide-svelte';
    import {onMount} from 'svelte';
    import {toast} from 'svelte-sonner';
    import {apiFetch, replaceUID} from 'utils/ApiMiddleware';
    const scrollToTop = () => window.scrollTo({ top: 0, behavior: 'smooth' });
    import DateInput from 'components/inputs/DateInput.svelte';
    import {initSelectpicker} from 'shim/select.js';
    import {hideModal} from 'shim/modal.js';

    export let athlete = {};
    export let idx;

    let submitButton;
    let tutorForm;
    let isSubmitting = false;

    const saveTutorData = async function (e) {
        if (isSubmitting) return;
        isSubmitting = true;

        try {
            let res = await apiFetch(replaceUID(__bakney.env.API.SUBSCRIPTION.EDIT, athlete.associate_id), {
                method: 'POST',
                body: JSON.stringify({
                    associate_tutor_data: athlete.associate_tutor_data,
                }),
            });

            if (!res.error) {
                toast.success('Dati tutore aggiornati con successo.');
                hideModal(`tutor-${idx}`);

                setTimeout(async () => {
                    let redirectToUpdateTutors = false;
                    let resInco = await apiFetch(__bakney.env.API.CHECK_INCONSISTENCIES, {method: 'GET'});
                    // if (!resInco.error && resInco.response.inconsistencies) {
                    //     redirectToUpdateTutors = true;
                    //     sessionStorage.setItem('inconsistencies', JSON.stringify(resInco.response.missing_tutors));
                    // }

                    if (redirectToUpdateTutors) {
                        location.href = '/#/update-tutors';
                    } else {
                        sessionStorage.removeItem('inconsistencies');
                        location.reload();
                    }
                }, 1000);
            } else {
                toast.error('Qualcosa è andato storto. Ricontrolla i dati!');
            }
        } finally {
            isSubmitting = false;
        }
    };

    const initForm = function () {
        tutorForm?.destroy();
        tutorForm = FormValidation.formValidation(document.getElementById('tutorForm'), {
            fields: {
                firstNameAssociateTutor: {
                    validators: {
                        notEmpty: {
                            message: 'Il nome è obbligatorio.',
                        },
                    },
                },
                lastNameAssociateTutor: {
                    validators: {
                        notEmpty: {
                            message: 'Il cognome è obbligatorio.',
                        },
                    },
                },
                sexAssociateTutor: {
                    validators: {
                        notEmpty: {
                            message: 'Il sesso è obbligatorio.',
                        },
                    },
                },
                taxCodeAssociateTutor: {
                    validators: {
                        notEmpty: {
                            message: 'Il codice fiscale è obbligatorio.',
                        },
                    },
                },
                bornDateAssociateTutor: {
                    validators: {
                        notEmpty: {
                            message: 'La data di nascita è obbligatoria.',
                        },
                        date: {
                            format: 'DD/MM/YYYY',
                            message: 'La data di nascita non è valida.',
                        },
                        notMinor: {
                            message: 'Il tutore deve essere maggiorenne.',
                        },
                    },
                },
                bornCityAssociateTutor: {
                    validators: {
                        notEmpty: {
                            message: 'La città di nascita è obbligatoria.',
                        },
                    },
                },
                addressAssociateTutor: {
                    validators: {
                        notEmpty: {
                            message: "L'indirizzo di Residenza è obbligatorio.",
                        },
                    },
                },
                addressCityAssociateTutor: {
                    validators: {
                        notEmpty: {
                            message: 'La città di Residenza è obbligatoria.',
                        },
                    },
                },
                capAssociateTutor: {
                    validators: {
                        notEmpty: {
                            message: 'Il cap della città di Residenza è obbligatorio.',
                        },
                    },
                },
            },
            plugins: {
                trigger: new FormValidation.plugins.Trigger(),
                bootstrap: new FormValidation.plugins.Bootstrap(),
            },
        }).registerValidator('notMinor', function () {
            return {
                validate: function (input) {
                    let date = moment(input.value, 'DD/MM/YYYY');
                    let now = moment();
                    let years = now.diff(date, 'years');
                    console.log(years);
                    return {
                        valid: years >= 18,
                    };
                },
            };
        });
    };

    const updateBornDateTutor = function (event) {
        if (event && event == 'focus') {
            athlete.associate_tutor_data.born_date = null;
        }
    };

    onMount(() => {
        initSelectpicker(document.getElementById('sexTutor'));
    });

    async function handleValidation(e) {
        if (!tutorForm) initForm();
        tutorForm?.validate().then(function (status) {
            if (status === 'Valid') {
                saveTutorData(e);
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
</script>

<div class="modal-content">
    <div class="modal-header">
        <h5 class="modal-title">Inserisci dati del Tutore</h5>
        <button type="button" class="close" data-dismiss="modal" aria-label="Close">
            <X size={16} data-dismiss="modal" />
        </button>
    </div>
    <form class="form" id="tutorForm" on:submit|preventDefault={handleValidation}>
        <div class="modal-body px-sm-10 py-2">
            <div class="row">
                <div class="col-xl-6">
                    <div class="form-group">
                        <!-- svelte-ignore a11y-label-has-associated-control -->
                        <label>Nome <b>*</b></label>
                        <input
                            bind:value={athlete.associate_tutor_data.first_name}
                            name="firstNameAssociateTutor"
                            type="text"
                            class="form-control form-control-solid form-control-lg margin-tb-2"
                            placeholder="Nome"
                            style="text-transform:capitalize" />
                    </div>
                </div>
                <div class="col-xl-6">
                    <div class="form-group">
                        <!-- svelte-ignore a11y-label-has-associated-control -->
                        <label>Cognome <b>*</b></label>
                        <input
                            bind:value={athlete.associate_tutor_data.last_name}
                            name="lastNameAssociateTutor"
                            type="text"
                            class="form-control form-control-solid form-control-lg margin-tb-2"
                            placeholder="Cognome"
                            style="text-transform:capitalize" />
                    </div>
                </div>
            </div>

            <div class="row">
                <div class="col-xl-6">
                    <div class="form-group">
                        <!-- svelte-ignore a11y-label-has-associated-control -->
                        <label>Sesso <b>*</b></label>
                        <select
                            bind:value={athlete.associate_tutor_data.sex}
                            name="sexAssociateTutor"
                            class="form-control selectpicker form-control-solid form-control-lg"
                            id="sexTutor">
                            <option value="F">Femmina</option>
                            <option value="M">Maschio</option>
                        </select>
                    </div>
                </div>

                <div class="col-xl-6">
                    <div class="form-group">
                        <!-- svelte-ignore a11y-label-has-associated-control -->
                        <label>Codice Fiscale <b>*</b></label>
                        <input
                            bind:value={athlete.associate_tutor_data.tax_code}
                            name="taxCodeAssociateTutor"
                            type="text"
                            class="form-control form-control-solid form-control-lg"
                            placeholder="Codice fiscale"
                            style="text-transform:uppercase" />
                    </div>
                </div>
            </div>

            <div class="row">
                <div class="col-xl-6">
                    <div class="form-group">
                        <!-- svelte-ignore a11y-label-has-associated-control -->
                        <label>Data di Nascita <b>*</b></label>
                        <DateInput id="bkn_datetimepicker_bornDate_tutor" inputId="date_input_born_date_tutor"
                            name="bornDateAssociateTutor" format="L" placeholder="GG/MM/AAAA"
                            bind:value={athlete.associate_tutor_data.born_date} />
                    </div>
                </div>
                <div class="col-xl-6">
                    <div class="form-group">
                        <!-- svelte-ignore a11y-label-has-associated-control -->
                        <label>Luogo di Nascita <b>*</b></label>
                        <input
                            bind:value={athlete.associate_tutor_data.born_city}
                            name="bornCityAssociateTutor"
                            type="text"
                            class="form-control form-control-solid form-control-lg"
                            placeholder="Luogo di Nascita"
                            style="text-transform:capitalize" />
                    </div>
                </div>
            </div>
            <div class="row">
                <div class="col-xl-6">
                    <div class="form-group">
                        <!-- svelte-ignore a11y-label-has-associated-control -->
                        <label>Indirizzo di Residenza, Numero <b>*</b></label>
                        <input
                            bind:value={athlete.associate_tutor_data.address}
                            id="addressAssociateTutor"
                            name="addressAssociateTutor"
                            type="text"
                            class="form-control form-control-solid form-control-lg"
                            placeholder="Indirizzo di Residenza, numero"
                            style="text-transform:capitalize" />
                    </div>
                </div>
                <div class="col-xl-6">
                    <div class="form-group">
                        <!-- svelte-ignore a11y-label-has-associated-control -->
                        <label>Città di Residenza <b>*</b></label>
                        <input
                            bind:value={athlete.associate_tutor_data.address_city}
                            name="addressCityAssociateTutor"
                            type="text"
                            class="form-control form-control-solid form-control-lg"
                            placeholder="Città di Residenza"
                            style="text-transform:capitalize" />
                    </div>
                </div>
            </div>

            <div class="row">
                <div class="col-xl-6">
                    <div class="form-group">
                        <!-- svelte-ignore a11y-label-has-associated-control -->
                        <label>Cap <b>*</b></label>
                        <input
                            bind:value={athlete.associate_tutor_data.address_cap}
                            name="capAssociateTutor"
                            type="text"
                            inputmode="numeric"
                            maxlength="5"
                            pattern="[0-9]{5}"
                            class="form-control form-control-solid form-control-lg"
                            id="bkn_inputmask_cap_tutor"
                            placeholder="CAP di Residenza" />
                    </div>
                </div>
                <div class="col-xl-6">
                    <div class="form-group">
                        <!-- svelte-ignore a11y-label-has-associated-control -->
                        <label>Cellulare (opzionale)</label>
                        <input
                            bind:value={athlete.associate_tutor_data.phone}
                            name="phoneAssociateTutor"
                            type="tel"
                            inputmode="tel"
                            class="form-control form-control-solid form-control-lg"
                            id="bkn_inputmask_phone_tutor"
                            placeholder="Numero italiano" />
                    </div>
                </div>
            </div>

            <div class="form-group">
                <!-- svelte-ignore a11y-label-has-associated-control -->
                <label>Email (opzionale)</label>
                <input
                    bind:value={athlete.associate_tutor_data.email}
                    name="emailAssociateTutor"
                    type="email"
                    class="form-control form-control-solid form-control-lg margin-tb-2"
                    placeholder="Inserisci un email..." />
            </div>
        </div>
        <div class="modal-footer">
            <button type="button" class="btn btn-light-primary font-weight-bold" data-dismiss="modal">Chiudi</button>
            <button bind:this={submitButton} type="submit" class="btn btn-primary font-weight-bold ml-2"
                disabled={isSubmitting}>
                {#if isSubmitting}
                    <span class="spinner spinner-white spinner-right pr-15"></span>
                {/if}
                {isSubmitting ? 'In attesa...' : 'Salva Informazioni'}</button>
        </div>
    </form>
</div>
