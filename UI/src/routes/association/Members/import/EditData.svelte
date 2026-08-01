<script>
	import { X } from 'lucide-svelte';
    import {onDestroy, onMount} from 'svelte';
    import {sessionToken} from 'store/stores.js';
    import {apiFetch, replaceUID} from 'utils/ApiMiddleware';
    import {getBornCity, getBornDate, getSex} from 'utils/TaxCode';
    import {Warning} from 'phosphor-svelte';
    import {toast} from 'svelte-sonner';
    import {createEventDispatcher} from 'svelte';
    import {blockPage, unblockPage} from 'store/loadingStore.js';
    import DateInput from 'components/inputs/DateInput.svelte';
    import {initSelectpicker, refreshSelectpicker} from 'shim/select.js';
    import {createDropzone} from 'shim/dropzone.js';
    import {hideModal} from 'shim/modal.js';

    const dispatch = createEventDispatcher();

    sessionToken.useLocalStorage();

    export let athlete = {};
    export let idx;
    export let certificate_expring_date = moment().format('DD/MM/YYYY');

    let submitButton;
    let dataForm;
    let isSubmitting = false;
    let validateTutorData = false;
    let checkingOn = true;
    let checkingOnTutor = true;
    let uploadedFile = false;
    let aiSuggestion = false;
    let ktDropzone = null;

    $: dataForm, (validateTutorData = isMinor(athlete?.associate?.born_date));

    $: {
        if (athlete?.associate?.tax_code) {
            checkTaxCode();
        }
        if (athlete?.associate_tutor?.tax_code) {
            checkTaxCodeTutor();
        }
    }

    function checkTaxCode() {
        if (!athlete.associate) return;
        if (
            String(athlete.associate.tax_code).length == 16 &&
            checkTaxCodeValidty(athlete.associate.tax_code) &&
            checkingOn == true
        ) {
            let extractedSex = String(getSex(athlete.associate.tax_code) || '').trim();
            let extractedBornCity = String(getBornCity(athlete.associate.tax_code) || '').trim();
            let extractedBornDate = String(getBornDate(athlete.associate.tax_code) || '').trim();
            if (extractedSex != '') {
                athlete.associate.sex = extractedSex;
                setTimeout(() => refreshSelectpicker(document.getElementById('sex_' + idx)), 500);
            }
            if (extractedBornCity != '') {
                athlete.associate.born_city = extractedBornCity;
            }
            if (extractedBornDate != '') {
                athlete.associate.born_date = extractedBornDate;
                athlete.associate.is_minor = isMinor(athlete.associate.born_date);
            }
            checkingOn = false;
        } else if (String(athlete.tax_code).length < 16) {
            checkingOn = true;
        }
    }

    function checkTaxCodeTutor() {
        if (
            String(athlete.associate_tutor.tax_code).length == 16 &&
            checkTaxCodeValidty(athlete.associate_tutor.tax_code) &&
            checkingOnTutor == true
        ) {
            let extractedSex = String(getSex(athlete.associate_tutor.tax_code) || '').trim();
            let extractedBornCity = String(getBornCity(athlete.associate_tutor.tax_code) || '').trim();
            let extractedBornDate = String(getBornDate(athlete.associate_tutor.tax_code) || '').trim();
            if (extractedSex != '') {
                athlete.associate_tutor.sex = extractedSex;
                setTimeout(() => refreshSelectpicker(document.getElementById('sexTutor_' + idx)), 500);
            }
            if (extractedBornCity != '') {
                athlete.associate_tutor.born_city = extractedBornCity;
            }
            if (extractedBornDate != '') {
                athlete.associate_tutor.born_date = extractedBornDate;
            }
            checkingOnTutor = false;
        } else if (String(athlete.associate_tutor.tax_code).length < 16) {
            checkingOnTutor = true;
        }
    }

    function checkTaxCodeValidty(taxCode) {
        return /^(?:[A-Z][AEIOU][AEIOUX]|[AEIOU]X{2}|[B-DF-HJ-NP-TV-Z]{2}[A-Z]){2}(?:[\dLMNP-V]{2}(?:[A-EHLMPR-T](?:[04LQ][1-9MNP-V]|[15MR][\dLMNP-V]|[26NS][0-8LMNP-U])|[DHPS][37PT][0L]|[ACELMRT][37PT][01LM]|[AC-EHLMPR-T][26NS][9V])|(?:[02468LNQSU][048LQU]|[13579MPRTV][26NS])B[26NS][9V])(?:[A-MZ][1-9MNP-V][\dLMNP-V]{2}|[A-M][0L](?:[1-9MNP-V][\dLMNP-V]|[0L][1-9MNP-V]))[A-Z]$/i.test(
            taxCode
        );
    }

    const saveTutorData = async function (e) {
        if (isSubmitting) return;
        isSubmitting = true;

        let payload = {
            edit_data: athlete,
        };

        if (!moment(certificate_expring_date, 'DD/MM/YYYY').isBefore(moment())) {
            payload.certificate_expiring_date = certificate_expring_date;
        }

        try {
            let res = await apiFetch(
                replaceUID(__bakney.env.API.SUBSCRIPTION.ASSOCIATES_DRAFT.EDIT, athlete.associate_import_draft_id),
                {
                    method: 'POST',
                    body: JSON.stringify(payload),
                }
            );

            if (!res.error) {
                toast.success('Dati aggiornati con successo.');
                hideModal(`edit-modal-${idx}`);

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
                        // location.reload();
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
        dataForm?.destroy();
        dataForm = FormValidation.formValidation(document.getElementById('dataForm'), {
            fields: {
                membership_number: {
                    validators: {
                        regexp: {
                            regexp: /^\d+$/,
                            message: 'Il numero non è valido.',
                        },
                    },
                },
                membership_start_date: {
                    validators: {
                        regexp: {
                            regexp: '^[0-9]{4}-[0-9]{2}-[0-9]{2}$',
                            flags: 'ig',
                            message: 'La data non è in un formato valido',
                        },
                        date: {
                            format: 'YYYY-MM-DD',
                            message: 'La data non è in un formato valido',
                        },
                    },
                },
                membership_end_date: {
                    validators: {
                        regexp: {
                            regexp: '^[0-9]{4}-[0-9]{2}-[0-9]{2}$',
                            flags: 'ig',
                            message: 'La data non è in un formato valido',
                        },
                        date: {
                            format: 'YYYY-MM-DD',
                            message: 'La data non è in un formato valido',
                        },
                    },
                },
                firstNameAssociate: {
                    validators: {
                        notEmpty: {
                            message: 'Il nome è obbligatorio.',
                        },
                    },
                },
                lastNameAssociate: {
                    validators: {
                        notEmpty: {
                            message: 'Il cognome è obbligatorio.',
                        },
                    },
                },
                sexAssociate: {
                    validators: {
                        notEmpty: {
                            message: 'Il sesso è obbligatorio.',
                        },
                    },
                },
                taxCodeAssociate: {
                    validators: {
                        notEmpty: {
                            message: 'Il codice fiscale è obbligatorio.',
                        },
                        regexp: {
                            regexp: '^[a-zA-Z]{6}[0-9]{2}[abcdehlmprstABCDEHLMPRST]{1}[0-9]{2}([a-zA-Z]{1}[0-9]{3})[a-zA-Z]{1}$',
                            flags: 'ig',
                            message: 'Il codice fiscale non è in un formato valido',
                        },
                    },
                },
                bornDateAssociate: {
                    validators: {
                        notEmpty: {
                            message: 'La data di nascita è obbligatoria.',
                        },
                        date: {
                            format: 'DD/MM/YYYY',
                            message: 'La data di nascita non è valida.',
                        },
                    },
                },
                bornCityAssociate: {
                    validators: {
                        notEmpty: {
                            message: 'La città di nascita è obbligatoria.',
                        },
                    },
                },
                addressAssociate: {
                    validators: {
                        notEmpty: {
                            message: "L'indirizzo di Residenza è obbligatorio.",
                        },
                    },
                },
                addressCityAssociate: {
                    validators: {
                        notEmpty: {
                            message: 'La città di Residenza è obbligatoria.',
                        },
                    },
                },
                capAssociate: {
                    validators: {
                        notEmpty: {
                            message: 'Il cap della città di Residenza è obbligatorio.',
                        },
                    },
                },
                firstNameAssociateTutor: {
                    enabled: validateTutorData,
                    validators: {
                        notEmpty: {
                            message: 'Il nome è obbligatorio.',
                        },
                    },
                },
                lastNameAssociateTutor: {
                    enabled: validateTutorData,
                    validators: {
                        notEmpty: {
                            message: 'Il cognome è obbligatorio.',
                        },
                    },
                },
                // sexAssociateTutor: {
                //     enabled: validateTutorData,
                //     validators: {
                //         notEmpty: {
                //             message: 'Il sesso è obbligatorio.',
                //         },
                //     },
                // },
                taxCodeAssociateTutor: {
                    enabled: validateTutorData,
                    validators: {
                        // notEmpty: {
                        //     message: 'Il codice fiscale è obbligatorio.',
                        // },
                        regexp: {
                            regexp: '^[a-zA-Z]{6}[0-9]{2}[abcdehlmprstABCDEHLMPRST]{1}[0-9]{2}([a-zA-Z]{1}[0-9]{3})[a-zA-Z]{1}$',
                            flags: 'ig',
                            message: 'Il codice fiscale non è in un formato valido',
                        },
                    },
                },
                bornDateAssociateTutor: {
                    enabled: validateTutorData,
                    validators: {
                        // notEmpty: {
                        //     message: 'La data di nascita è obbligatoria.',
                        // },
                        date: {
                            format: 'DD/MM/YYYY',
                            message: 'La data di nascita non è valida.',
                        },
                        notMinor: {
                            message: 'Il tutore deve essere maggiorenne.',
                        },
                    },
                },
                // bornCityAssociateTutor: {
                //     enabled: validateTutorData,
                //     validators: {
                //         notEmpty: {
                //             message: 'La città di nascita è obbligatoria.',
                //         },
                //     },
                // },
                // addressAssociateTutor: {
                //     enabled: validateTutorData,
                //     validators: {
                //         notEmpty: {
                //             message: "L'indirizzo di Residenza è obbligatorio.",
                //         },
                //     },
                // },
                // addressCityAssociateTutor: {
                //     enabled: validateTutorData,
                //     validators: {
                //         notEmpty: {
                //             message: 'La città di Residenza è obbligatoria.',
                //         },
                //     },
                // },
                // capAssociateTutor: {
                //     enabled: validateTutorData,
                //     validators: {
                //         notEmpty: {
                //             message: 'Il cap della città di Residenza è obbligatorio.',
                //         },
                //     },
                // },
            },
            plugins: {
                trigger: new FormValidation.plugins.Trigger(),
                bootstrap: new FormValidation.plugins.Bootstrap(),
                excluded: new FormValidation.plugins.Excluded(),
            },
        }).registerValidator('notMinor', function () {
            return {
                validate: function (input) {
                    let date = moment(input.value, 'DD/MM/YYYY');
                    let now = moment();
                    let years = now.diff(date, 'years');
                    return {
                        valid: years >= 18,
                    };
                },
            };
        });
    };

    const isMinor = function (input) {
        let date = moment(input, 'DD/MM/YYYY');
        let now = moment();
        let years = now.diff(date, 'years');
        return years < 18;
    };

    const updateBornDateTutor = function (event) {
        if (event && event == 'focus') {
            athlete.associate_tutor.born_date = null;
        }
    };

    const updateBornDate = function (event) {
        if (event && event == 'focus') {
            athlete.associate.born_date = null;
        }
    };

    onMount(() => {
        initSelectpicker(document.getElementById('sex_' + idx));

        initSelectpicker(document.getElementById('sexTutor_' + idx));

        ktDropzone = createDropzone(document.querySelector(`#dropzone_draft_upload`), {
                accept: 'image/*,application/pdf',
                multiple: false,
            });

            ktDropzone.on('success', function(file, response) {
                aiSuggestion = false;
                if (response.expiring_date) {
                    certificate_expring_date = moment(
                        response.expiring_date,
                        'YYYY-MM-DD'
                    ).format('DD/MM/YYYY');
                    aiSuggestion = true;
                }
            });
    });

    onDestroy(() => {
        dispatch('close');
    });

    async function handleValidation(e) {
        if (!dataForm) initForm();
        dataForm?.validate().then(function (status) {
            if (status === 'Valid') {
                // if (!moment(certificate_expring_date, 'DD/MM/YYYY').isBefore(moment())) setCertificateExpiration();
                ktDropzone.removeAllFiles();
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
                });
                // .then(function () {
                //     UiUtil.scrollTop();
                // });
            }
        });
    }

    function setCertificateExpiration() {
        apiFetch(replaceUID(__bakney.env.API.SUBSCRIPTION.MEDICAL_CERTIFICATE.SET_CERTIFICATE_EXPIRATION, idx), {
            method: 'POST',
            body: JSON.stringify({
                subscription_id: idx,
                certificate_expiring_date: certificate_expring_date,
            }),
        });
    }
</script>

<div class="modal-content">
    <!-- <div class="modal-header">
        <h5 class="modal-title">Modifica i dati</h5>
        <button type="button" class="close" data-dismiss="modal" aria-label="Close">
            <X size={16} data-dismiss="modal" />
        </button>
    </div> -->
    <form class="form" id="dataForm" on:submit|preventDefault={handleValidation}>
        <div class="modal-body px-sm-10 py-2">
            <h5 class="font-weight-bolder mb-6 mt-6 font-size-h3">Informazioni certificato medico</h5>
            <div class="row">
                <div class="col-xl-6">
                    <!-- svelte-ignore a11y-click-events-have-key-events -->
                    <div class="form-group" on:click={() => (aiSuggestion = false)}>
                        <!-- svelte-ignore a11y-label-has-associated-control -->
                        <label>Data di scadenza<b>*</b></label>
                        <DateInput id="expiring_certificate_{idx}" name="expiring_certificate_{idx}"
                            format="L" placeholder="Seleziona Data"
                            bind:value={certificate_expring_date} />
                    </div>
                    {#if aiSuggestion}
                        <!-- alert -->
                        <div
                            class="d-flex align-items-center text-bold text-warning bg-light-warning p-4 mb-4"
                            style="border-radius: 0.35rem;">
                            <Warning size={18} weight="duotone" class="mr-2" />
                            La data di scadenza è stata suggerita automaticamente dal sistema.
                        </div>
                    {/if}

                    {#if athlete.medical_certificate && athlete.medical_certificate.filename}
                        <div
                            class="font-weight-bolder mb-6 mt-6 font-size-h4 m-auto text-center"
                            style="margin-bottom:1rem!important;">
                            File Caricato: <span class="text-primary">{athlete.medical_certificate.filename}</span>
                        </div>
                    {/if}
                </div>
                <div class="col-xl-6">
                    <div class="dropzone dropzone-default" id="dropzone_draft_upload">
                        <div class="dropzone-msg dz-message needsclick">
                            <h3 class="dropzone-msg-title">
                                {#if athlete.medical_certificate && athlete.medical_certificate.filename}
                                    Per sostituire il file già caricato premi o trascina il nuovo file.
                                {:else}
                                    Trascina o premi per caricare il Certificato Medico.
                                {/if}
                            </h3>
                            <span class="dropzone-msg-desc"
                                >Sono supportati file <b>pdf</b> e <b>immagini</b> di grandezza inferiore a
                                <b>5MB</b>.</span>
                        </div>
                    </div>
                </div>
            </div>
            <h5 class="font-weight-bolder mb-6 mt-6 font-size-h3">Dati dell'associato</h5>
            <div class="row">
                <div class="col-xl-6">
                    <div class="form-group">
                        <!-- svelte-ignore a11y-label-has-associated-control -->
                        <label>Nome <b>*</b></label>
                        <input
                            bind:value={athlete.associate.first_name}
                            name="firstNameAssociate"
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
                            bind:value={athlete.associate.last_name}
                            name="lastNameAssociate"
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
                            bind:value={athlete.associate.sex}
                            name="sexAssociate"
                            class="form-control selectpicker form-control-solid form-control-lg"
                            id="sex_{idx}">
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
                            bind:value={athlete.associate.tax_code}
                            name="taxCodeAssociate"
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
                        <DateInput id="bkn_datetimepicker_bornDate_{idx}" inputId="date_input_born_date_{idx}"
                            name="bornDateAssociate" format="L" placeholder="GG/MM/AAAA"
                            bind:value={athlete.associate.born_date} />
                    </div>
                </div>
                <div class="col-xl-6">
                    <div class="form-group">
                        <!-- svelte-ignore a11y-label-has-associated-control -->
                        <label>Luogo di Nascita <b>*</b></label>
                        <input
                            bind:value={athlete.associate.born_city}
                            name="bornCityAssociate"
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
                            bind:value={athlete.associate.address}
                            id="address"
                            name="addressAssociate"
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
                            bind:value={athlete.associate.address_city}
                            name="addressCityAssociate"
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
                            bind:value={athlete.associate.address_cap}
                            name="cap"
                            type="text"
                            inputmode="numeric"
                            maxlength="5"
                            pattern="[0-9]{5}"
                            class="form-control form-control-solid form-control-lg"
                            id="bkn_inputmask_cap_{idx}"
                            placeholder="CAP di Residenza" />
                    </div>
                </div>
                <div class="col-xl-6">
                    <div class="form-group">
                        <!-- svelte-ignore a11y-label-has-associated-control -->
                        <label>Cellulare (opzionale)</label>
                        <input
                            bind:value={athlete.associate.phone}
                            name="phone"
                            type="tel"
                            inputmode="tel"
                            class="form-control form-control-solid form-control-lg"
                            id="bkn_inputmask_phone_{idx}"
                            placeholder="Numero italiano" />
                    </div>
                </div>
            </div>

            <div class="form-group">
                <!-- svelte-ignore a11y-label-has-associated-control -->
                <label>Email (opzionale)</label>
                <input
                    bind:value={athlete.associate.email}
                    name="emailAssociate"
                    type="email"
                    class="form-control form-control-solid form-control-lg margin-tb-2"
                    placeholder="Inserisci un email..." />
            </div>
            <div style="display: {isMinor(athlete.associate.born_date) ? 'block' : 'none'}">
                <h5 class="font-weight-bolder mb-6 mt-12 font-size-h3">Dati del tutore</h5>

                <div class="row">
                    <div class="col-xl-6">
                        <div class="form-group">
                            <!-- svelte-ignore a11y-label-has-associated-control -->
                            <label>Nome</label>
                            <input
                                bind:value={athlete.associate_tutor.first_name}
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
                            <label>Cognome</label>
                            <input
                                bind:value={athlete.associate_tutor.last_name}
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
                            <label>Sesso</label>
                            <select
                                bind:value={athlete.associate_tutor.sex}
                                name="sexAssociateTutor"
                                class="form-control form-control-solid form-control-lg"
                                id="sexTutor_{idx}">
                                <option value="F">Femmina</option>
                                <option value="M">Maschio</option>
                            </select>
                        </div>
                    </div>

                    <div class="col-xl-6">
                        <div class="form-group">
                            <!-- svelte-ignore a11y-label-has-associated-control -->
                            <label>Codice Fiscale</label>
                            <input
                                bind:value={athlete.associate_tutor.tax_code}
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
                            <label>Data di Nascita</label>
                            <DateInput id="bkn_datetimepicker_bornDate_tutor_{idx}"
                                inputId="date_input_born_date_tutor_{idx}"
                                name="bornDateAssociateTutor" format="L" placeholder="GG/MM/AAAA"
                                bind:value={athlete.associate_tutor.born_date} />
                        </div>
                    </div>
                    <div class="col-xl-6">
                        <div class="form-group">
                            <!-- svelte-ignore a11y-label-has-associated-control -->
                            <label>Luogo di Nascita</label>
                            <input
                                bind:value={athlete.associate_tutor.born_city}
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
                            <label>Indirizzo di Residenza, Numero</label>
                            <input
                                bind:value={athlete.associate_tutor.address}
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
                            <label>Città di Residenza</label>
                            <input
                                bind:value={athlete.associate_tutor.address_city}
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
                            <label>Cap</label>
                            <input
                                bind:value={athlete.associate_tutor.address_cap}
                                name="capAssociateTutor"
                                type="text"
                                inputmode="numeric"
                                maxlength="5"
                                pattern="[0-9]{5}"
                                class="form-control form-control-solid form-control-lg"
                                id="bkn_inputmask_cap_tutor_{idx}"
                                placeholder="CAP di Residenza" />
                        </div>
                    </div>
                    <div class="col-xl-6">
                        <div class="form-group">
                            <!-- svelte-ignore a11y-label-has-associated-control -->
                            <label>Cellulare (opzionale)</label>
                            <input
                                bind:value={athlete.associate_tutor.phone}
                                name="phoneAssociateTutor"
                                type="tel"
                                inputmode="tel"
                                class="form-control form-control-solid form-control-lg"
                                id="bkn_inputmask_phone_tutor_{idx}"
                                placeholder="Numero italiano" />
                        </div>
                    </div>
                </div>

                <div class="form-group">
                    <!-- svelte-ignore a11y-label-has-associated-control -->
                    <label>Email (opzionale)</label>
                    <input
                        bind:value={athlete.associate_tutor.email}
                        name="emailAssociateTutor"
                        type="email"
                        class="form-control form-control-solid form-control-lg margin-tb-2"
                        placeholder="Inserisci un email..." />
                </div>
            </div>
            <div class="col-12">
                <h1 class="text-dark font-weight-boldest mb-8">Tesseramento</h1>
                <div class="row">
                    <div class="form-group col-12 col-md-3">
                        <!-- svelte-ignore a11y-label-has-associated-control -->
                        <label class="font-weight-bolder">Numero tessera</label>
                        <input
                            bind:value={athlete.associate.membership_number}
                            name="membership_number"
                            type="text"
                            class="form-control form-control-solid form-control-lg margin-tb-2"
                            placeholder="Numero tessera" />
                    </div>

                    <div class="form-group col-12 col-md-3">
                        <!-- svelte-ignore a11y-label-has-associated-control -->
                        <label class="font-weight-bolder">Data tesseramento</label>
                        <input
                            bind:value={athlete.associate.membership_start_date}
                            name="membership_start_date"
                            type="date"
                            class="form-control form-control-solid form-control-lg margin-tb-2"
                            placeholder="Data della tessera" />
                    </div>

                    <div class="form-group col-12 col-md-3">
                        <!-- svelte-ignore a11y-label-has-associated-control -->
                        <label class="font-weight-bolder">Data fine tesseramento</label>
                        <input
                            bind:value={athlete.associate.membership_end_date}
                            name="membership_end_date"
                            type="date"
                            class="form-control form-control-solid form-control-lg margin-tb-2"
                            placeholder="Data scadenza tessera" />
                    </div>

                    <div class="form-group col-12 col-md-3">
                        <!-- svelte-ignore a11y-label-has-associated-control -->
                        <label class="font-weight-bolder">Tipo di tessera</label>
                        <input
                            bind:value={athlete.associate.membership_type}
                            name="membership_type"
                            type="text"
                            class="form-control form-control-solid form-control-lg margin-tb-2"
                            placeholder="Tipologia tesseramento" />
                    </div>
                </div>
            </div>
        </div>
        <div
            class="modal-footer bg-white p-0 pt-12 d-flex align-items-center justify-content-center shadow-lg"
            style="position:sticky;bottom:0;">
            <!-- <button type="button" class="btn btn-light-primary font-weight-bold" data-dismiss="modal">Chiudi</button> -->
            <button bind:this={submitButton} type="submit" class="btn btn-primary my-4 font-weight-boldest"
                disabled={isSubmitting}>
                {#if isSubmitting}
                    <span class="spinner spinner-white spinner-right pr-15"></span>
                {/if}
                {isSubmitting ? 'In attesa...' : 'Salva Modifiche'}</button>
        </div>
    </form>
</div>
