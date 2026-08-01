<script>
    import {getBornCity, getBornDate, getSex} from 'utils/TaxCode';
    import Step1minor from './Step1minor.svelte';
    import {ArrowLeft, ArrowRight} from 'phosphor-svelte';
    import {onMount} from 'svelte';
    import DateInput from 'components/inputs/DateInput.svelte';
    import {initSelectpicker, refreshSelectpicker} from 'shim/select.js';
    import GenerateTaxCodeButton from 'components/buttons/GenerateTaxCodeButton.svelte';
    import FederationSocietyData from './partials/federation-society-data.svelte';
    import FederationAthleteData from './partials/federation-athlete-data.svelte';
    import AdditionalFields from './partials/additional-fields.svelte';

    export let wizardData;
    let checkingOn = true;
    let checkingOnTutor = true;
    let formValidator = null;
    let validationSchema = {};

    $: wizardData.formData.associate_data.tax_code, checkTaxCode();
    $: wizardData.formData.associate_tutor_data.tax_code, checkTaxCodeTutor();

    function updateBornDate(event) {
        // if (event && event == 'focus') {
        //     wizardData.formData.associate_data.born_date = null;
        // }
        wizardData.formData.associate_data.is_minor = isMinor(wizardData.formData.associate_data.born_date);
    }

    function handleBornDateChange() {
        wizardData.formData = updateIsMinor(wizardData.formData, 'date_input_born_date', __bakney.env.DEBUG);
    }

    onMount(() => {
        initSelectpicker(document.getElementById('sex'));

        // mandatory fields based on configuration
            let configurationMandatoryEmail = wizardData.sportAssociationData.user.sport_association.configuration
                ?.mandatory_email
                ? {
                      notEmpty: {message: "L'indirizzo email è obbligatorio."},
                      emailAddress: {message: 'Non è un indirizzo email valido.'},
                  }
                : {};
            let configurationMandatoryPhone = wizardData.sportAssociationData.user.sport_association.configuration
                ?.mandatory_phone
                ? {
                      notEmpty: {message: 'Il numero di telefono è obbligatorio.'},
                      phone: {country: 'IT', message: 'Non è un numero di telefono valido.'},
                  }
                : {};

            let configurationMandatoryTutorEmail = wizardData.sportAssociationData.user.sport_association.configuration
                ?.mandatory_tutor_email
                ? {
                      notEmpty: {message: "L'indirizzo email è obbligatorio."},
                      emailAddress: {message: 'Non è un indirizzo email valido.'},
                  }
                : {};
            let configurationMandatoryTutorPhone = wizardData.sportAssociationData.user.sport_association.configuration
                ?.mandatory_tutor_phone
                ? {
                      notEmpty: {message: 'Il numero di telefono è obbligatorio.'},
                      phone: {country: 'IT', message: 'Non è un numero di telefono valido.'},
                  }
                : {};

            let formElement = document.querySelector('#subscribe-form');

            const additionalFieldValidations = wizardData?.formData?.additional_fields
                ?.filter(field => field.props.required)
                .reduce((acc, field) => {
                    acc[field.props.name] = {
                        validators: {
                            notEmpty: {
                                message: `Il campo ${field.props.label} è obbligatorio.`,
                            },
                        },
                    };
                    return acc;
                }, {});

            console.log(additionalFieldValidations);

            formValidator = FormValidation.formValidation(formElement, {
                fields: {
                    // expand validationSchema
                    ...validationSchema,
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
                    phoneAssociate: {
                        validators: configurationMandatoryPhone,
                    },
                    emailAssociate: {
                        validators: configurationMandatoryEmail,
                    },
                    phoneAssociateTutor: {
                        validators: configurationMandatoryTutorPhone,
                    },
                    emailAssociateTutor: {
                        validators: configurationMandatoryTutorEmail,
                    },
                    ...additionalFieldValidations,
                },
                plugins: {
                    excluded: new FormValidation.plugins.Excluded({
                        excluded: function (field, ele, eles) {
                            if (!wizardData.formData.associate_data.is_minor && String(field).includes('Tutor')) {
                                return true;
                            }
                            return false;
                            // if (wizardData.formData.associate_data.is_minor &&
                            //     field == 'emailAssociate' &&
                            // )
                            // if (!wizardData.formData.associate_data.is_minor && !__bakney.env.DEBUG) {
                            //     // build the condition for emailAssociate and phoneAssociate based on configuration
                            //     let emailAssociate = wizardData.sportAssociationData.user.sport_association.configuration
                            //         ?.mandatory_email
                            //         ? wizardData.formData.associate_data.email !== ''
                            //         : true;
                            //     let phoneAssociate = wizardData.sportAssociationData.user.sport_association.configuration
                            //         ?.mandatory_phone
                            //         ? wizardData.formData.associate_data.phone !== ''
                            //         : true;
                            //     return (
                            //         wizardData.formData.associate_data.first_name !== '' &&
                            //         wizardData.formData.associate_data.last_name !== '' &&
                            //         wizardData.formData.associate_data.sex !== '' &&
                            //         wizardData.formData.associate_data.tax_code !== '' &&
                            //         wizardData.formData.associate_data.born_date !== '' &&
                            //         wizardData.formData.associate_data.born_city !== '' &&
                            //         wizardData.formData.associate_data.address !== '' &&
                            //         wizardData.formData.associate_data.address_city !== '' &&
                            //         wizardData.formData.associate_data.address_cap !== '' &&
                            //         emailAssociate &&
                            //         phoneAssociate
                            //     );
                            // }
                            // return __bakney.env.DEBUG;
                        },
                    }),
                    trigger: new FormValidation.plugins.Trigger(),
                    bootstrap: new FormValidation.plugins.Bootstrap(),
                },
            });

            console.log(formValidator);
    });

    function checkTaxCodeValidty(taxCode) {
        return /^(?:[A-Z][AEIOU][AEIOUX]|[AEIOU]X{2}|[B-DF-HJ-NP-TV-Z]{2}[A-Z]){2}(?:[\dLMNP-V]{2}(?:[A-EHLMPR-T](?:[04LQ][1-9MNP-V]|[15MR][\dLMNP-V]|[26NS][0-8LMNP-U])|[DHPS][37PT][0L]|[ACELMRT][37PT][01LM]|[AC-EHLMPR-T][26NS][9V])|(?:[02468LNQSU][048LQU]|[13579MPRTV][26NS])B[26NS][9V])(?:[A-MZ][1-9MNP-V][\dLMNP-V]{2}|[A-M][0L](?:[1-9MNP-V][\dLMNP-V]|[0L][1-9MNP-V]))[A-Z]$/i.test(
            taxCode
        );
    }

    function checkTaxCode() {
        if (
            String(wizardData.formData.associate_data.tax_code).length == 16 &&
            checkTaxCodeValidty(wizardData.formData.associate_data.tax_code) &&
            checkingOn == true
        ) {
            let extractedSex = String(getSex(wizardData.formData.associate_data.tax_code) || '').trim();
            let extractedBornCity = String(getBornCity(wizardData.formData.associate_data.tax_code) || '').trim();
            let extractedBornDate = String(getBornDate(wizardData.formData.associate_data.tax_code) || '').trim();
            if (extractedSex != '') {
                wizardData.formData.associate_data.sex = extractedSex;
                setTimeout(() => refreshSelectpicker(document.getElementById('sex')), 500);
            }
            if (extractedBornCity != '') {
                wizardData.formData.associate_data.born_city = extractedBornCity;
            }
            if (extractedBornDate != '') {
                wizardData.formData.associate_data.born_date = extractedBornDate;
                wizardData.formData.associate_data.is_minor = isMinor(wizardData.formData.associate_data.born_date);
            }
            checkingOn = false;
        } else if (String(wizardData.formData.associate_data.tax_code).length < 16) {
            checkingOn = true;
        }
    }

    function checkTaxCodeTutor() {
        if (
            String(wizardData.formData.associate_tutor_data.tax_code).length == 16 &&
            checkTaxCodeValidty(wizardData.formData.associate_tutor_data.tax_code) &&
            checkingOnTutor == true
        ) {
            let extractedSex = String(getSex(wizardData.formData.associate_tutor_data.tax_code) || '').trim();
            let extractedBornCity = String(getBornCity(wizardData.formData.associate_tutor_data.tax_code) || '').trim();
            let extractedBornDate = String(getBornDate(wizardData.formData.associate_tutor_data.tax_code) || '').trim();
            if (extractedSex != '') {
                wizardData.formData.associate_tutor_data.sex = extractedSex;
                setTimeout(() => refreshSelectpicker(document.getElementById('sexTutor')), 500);
            }
            if (extractedBornCity != '') {
                wizardData.formData.associate_tutor_data.born_city = extractedBornCity;
            }
            if (extractedBornDate != '') {
                wizardData.formData.associate_tutor_data.born_date = extractedBornDate;
            }
            checkingOnTutor = false;
        } else if (String(wizardData.formData.associate_tutor_data.tax_code).length < 16) {
            checkingOnTutor = true;
        }
    }

    const isMinor = function (input) {
        let date = moment(input, 'DD/MM/YYYY');
        let now = moment();
        let years = now.diff(date, 'years');
        return years < 18;
    };

    function validateNextStep() {
        if (!formValidator) return wizardData.nextStep();
        formValidator?.validate()?.then(function (status) {
            // if it is valid or if I don't decide to create a new user
            if (status == 'Valid') {
                wizardData.nextStep();
            } else {
                Swal.fire({
                    text: 'Ops, sembra ci siano degli errori, riprova per favore.',
                    icon: 'error',
                    buttonsStyling: false,
                    confirmButtonText: 'Ok, capito!',
                    customClass: {
                        confirmButton: 'btn font-weight-bold btn-light',
                    },
                });
            }
        });
    }
</script>

{#if wizardData?.sportAssociationData?.isFederation && ['scuola-asc', 'asd-o-ssd', 'societa'].includes(wizardData.formData.custom_data.type_of_associate)}
    <h4 class="font-weight-bold text-dark wizard-title-info">Dati del presidente</h4>
    <h6 class="mb-10">Inserisci le informazioni sulla persona fisica che rappresenta la tua associazione/società.</h6>
{:else}
    <h4 class="font-weight-bold text-dark wizard-title-info">Associato</h4>
    <h6 class="mb-10">Inserisci le informazioni dell'atleta che fisicamente praticherà l'attività sportiva.</h6>
{/if}

<div class="form-group">
    <!-- svelte-ignore a11y-label-has-associated-control -->
    <label>Nome <b>*</b></label>
    <input
        bind:value={wizardData.formData.associate_data.first_name}
        name="firstNameAssociate"
        type="text"
        class="form-control form-control-solid form-control-lg margin-tb-2"
        placeholder="Nome"
        style="text-transform:capitalize" />
    <!-- <span class="form-text text-muted">Per favore inserisci il nome.</span> -->
</div>
<!--end::Input-->
<!--begin::Input-->
<div class="form-group">
    <!-- svelte-ignore a11y-label-has-associated-control -->
    <label>Cognome <b>*</b></label>
    <input
        bind:value={wizardData.formData.associate_data.last_name}
        name="lastNameAssociate"
        type="text"
        class="form-control form-control-solid form-control-lg margin-tb-2"
        placeholder="Cognome"
        style="text-transform:capitalize" />
    <!-- <span class="form-text text-muted">Per favore inserisci il cognome.</span> -->
</div>

<div class="row">
    <div class="col-12 col-xl-6">
        <div class="form-group">
            <!-- svelte-ignore a11y-label-has-associated-control -->
            <label>Codice Fiscale <b>*</b></label>

            <div class="d-flex align-items-center justify-content-between">
                <input
                    bind:value={wizardData.formData.associate_data.tax_code}
                    name="taxCodeAssociate"
                    maxlength="16"
                    type="text"
                    class="form-control form-control-solid form-control-lg mb-0"
                    placeholder="Codice fiscale"
                    style="text-transform:uppercase" />

                <GenerateTaxCodeButton
                    bind:data={wizardData.formData.associate_data}
                    on:codice={e => (wizardData.formData.associate_data.tax_code = e.detail)} />
            </div>
            <!-- <span class="form-text text-muted">Per favore inserisci il codice fiscale.</span> -->
        </div>
    </div>
    <div class="col-12 col-xl-6">
        <div class="form-group">
            <!-- svelte-ignore a11y-label-has-associated-control -->
            <label>Sesso <b>*</b></label>
            <select
                bind:value={wizardData.formData.associate_data.sex}
                name="sexAssociate"
                class="form-control selectpicker form-control-solid form-control-lg"
                id="sex">
                <option value="F">Femmina</option>
                <option value="M">Maschio</option>
            </select>
            <!-- <span class="form-text text-muted">Per favore inserisci il sesso.</span> -->
        </div>
    </div>
</div>

<div class="row">
    <div class="col-12 col-xl-6">
            <div class="form-group">
                <!-- svelte-ignore a11y-label-has-associated-control -->
                <label>Data di Nascita <b>*</b></label>
                <DateInput id="bkn_datetimepicker_bornDate" inputId="date_input_born_date" name="bornDateAssociate"
                    format="L" placeholder="GG/MM/AAAA"
                    bind:value={wizardData.formData.associate_data.born_date}
                    on:change={handleBornDateChange} />
                <!-- <span class="form-text text-muted">Per favore inserisci la data di nascita.</span> -->
            </div>
    </div>
    <div class="col-12 col-xl-6">
        <div class="form-group">
            <!-- svelte-ignore a11y-label-has-associated-control -->
            <label>Luogo di Nascita <b>*</b></label>
            <input
                bind:value={wizardData.formData.associate_data.born_city}
                name="bornCityAssociate"
                type="text"
                class="form-control form-control-solid form-control-lg"
                placeholder="Luogo di Nascita"
                style="text-transform: capitalize;" />
            <!-- <span class="form-text text-muted">Per favore inserisci il luogo di nascita.</span> -->
        </div>
    </div>
</div>

<div class="form-group">
    <!-- svelte-ignore a11y-label-has-associated-control -->
    <label>Indirizzo di Residenza, Numero <b>*</b></label>
    <input
        bind:value={wizardData.formData.associate_data.address}
        name="addressAssociate"
        type="text"
        class="form-control form-control-solid form-control-lg"
        placeholder="Indirizzo di Residenza, numero"
        style="text-transform: capitalize;" />
    <!-- <span class="form-text text-muted">Per favore inserisci Indirizzo di Residenza, numero.</span> -->
</div>

<div class="form-group">
    <!-- svelte-ignore a11y-label-has-associated-control -->
    <label>Città di Residenza <b>*</b></label>
    <input
        bind:value={wizardData.formData.associate_data.address_city}
        name="addressCityAssociate"
        type="text"
        class="form-control form-control-solid form-control-lg"
        placeholder="Città di Residenza"
        style="text-transform: capitalize;" />
    <!-- <span class="form-text text-muted">Per favore inserisci la città di residenza.</span> -->
</div>

<div class="row">
    <div class="col-12 col-xl-6">
        <div class="form-group">
            <!-- svelte-ignore a11y-label-has-associated-control -->
            <label>Cap <b>*</b></label>
            <input
                bind:value={wizardData.formData.associate_data.address_cap}
                name="capAssociate"
                type="text"
                inputmode="numeric"
                maxlength="5"
                pattern="[0-9]{5}"
                class="form-control form-control-solid form-control-lg"
                id="bkn_inputmask_cap"
                placeholder="CAP di Residenza" />
            <!-- <span class="form-text text-muted">Per favore inserisci il CAP.</span> -->
        </div>
    </div>
    <div class="col-12 col-xl-6">
        <div class="form-group">
            <!-- svelte-ignore a11y-label-has-associated-control -->
            <label
                >Cellulare {wizardData.sportAssociationData.user.sport_association.configuration.mandatory_phone
                    ? '*'
                    : '(opzionale)'}</label>
            <input
                bind:value={wizardData.formData.associate_data.phone}
                name="phoneAssociate"
                type="tel"
                inputmode="tel"
                class="form-control form-control-solid form-control-lg"
                id="bkn_inputmask_phone"
                placeholder="Numero di telefono" />
            <!-- <span class="form-text text-muted">Per favore inserisci il numero cellulare, sono validi solo numeri italiani.</span> -->
        </div>
    </div>
</div>

<!--begin::Input-->
<div class="form-group">
    <!-- svelte-ignore a11y-label-has-associated-control -->
    <label
        >Email {wizardData.sportAssociationData.user.sport_association.configuration.mandatory_email
            ? '*'
            : '(opzionale)'}</label>
    <input
        bind:value={wizardData.formData.associate_data.email}
        name="emailAssociate"
        type="email"
        class="form-control form-control-solid form-control-lg margin-tb-2"
        placeholder="Inserisci un'email..." />
    <!-- <span class="form-text text-muted">Per favore inserisci un indirizzo email.</span> -->
</div>
<AdditionalFields bind:formData={wizardData.formData} />

<!--end::Input-->
{#if wizardData?.sportAssociationData?.isFederation}
    {#if ['scuola-asc', 'asd-o-ssd', 'societa'].includes(wizardData.formData.custom_data.type_of_associate)}
        <FederationSocietyData bind:formData={wizardData.formData} bind:validationSchema />
    {:else}
        <Step1minor bind:wizardData />
        <FederationAthleteData bind:formData={wizardData.formData} bind:validationSchema />
    {/if}
{:else}
    <Step1minor bind:wizardData />
{/if}

<hr class="mt-10" />
<div class="d-flex justify-content-between align-items-center">
    <button
        type="button"
        class="btn btn-sm btn-ghost font-weight-bolder font-size-h6 px-8 py-4 my-3 mr-3"
        on:click={wizardData.prevStep}>
        <ArrowLeft size={24} /> Indietro
    </button>
    <button
        type="button"
        class="btn btn-sm btn-primary font-weight-bolder font-size-h6 px-8 py-4 my-3 mr-4"
        on:click={validateNextStep}>
        Continua <ArrowRight size={24} />
    </button>
</div>
