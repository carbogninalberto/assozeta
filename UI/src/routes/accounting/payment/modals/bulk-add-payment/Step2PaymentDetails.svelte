<script>
    import moment from 'moment';
    import {createEventDispatcher, onMount} from 'svelte';
    import {apiFetch} from 'utils/ApiMiddleware.js';
    import {toast} from 'svelte-sonner';
    import {userData} from 'store/stores.js';
    import {
        Datepicker,
        Currency,
        SmartSelect,
        TextArea,
        TextInput,
    } from 'components/formBuilder/preview-blocks/index.js';
    import MetaPaymentCategories from '../../modals/partials/meta-payment-categories.svelte';

    const dispatch = createEventDispatcher();

    // Local state for form fields
    let amount = '';
    let type = 'cash';
    let subject = 0;
    let expense = false;
    let description = '';
    let creation_date = moment().format('YYYY-MM-DD');
    let payment_date = null;
    let paid = false;
    let payment_category = null;
    let custom_accounts = null;
    let notes = '';
    let course = null;

    let loading = true;
    let categories = [];
    let accounts = [];
    let courses = [];

    // Helper to dispatch current state
    function notifyChange() {
        dispatch('change', {
            amount,
            type,
            subject,
            expense,
            description,
            creation_date,
            payment_date,
            paid,
            payment_category,
            custom_accounts,
            notes,
            meta_payment_categories: metaData.meta_payment_categories,
            course,
        });
    }

    async function fetchCategories() {
        const res = await apiFetch(__bakney.env.API.PAYMENT.CATEGORY.LIST);
        if (!res.error) {
            categories = res.response.data || [];
        } else if (res.status != 403 && res.status != 401) {
            toast.error('Errore nel caricamento delle causali.');
        }
    }

    async function fetchCustomAccounts() {
        const res = await apiFetch(__bakney.env.API.BALANCE_SHEET_ACCOUNTS.LIST + '?related=false', {
            method: 'GET',
        });
        if (!res.error) {
            accounts = Array.from(res?.response?.data || []);
            // Set default account if not set
            if (!custom_accounts && accounts.length > 0) {
                custom_accounts = accounts[0].custom_account_id;
                notifyChange();
            }
        } else if (res.status != 403 && res.status != 401) {
            toast.error('Errore nel caricamento dei conti.');
        }
    }

    async function fetchCourses() {
        const res = await apiFetch(__bakney.env.API.COURSE.LIST + '?all=1&optimized=1');
        if (!res.error) {
            courses = Object.values(res.response.data || {}).map(x => ({
                label: x.title,
                value: x.course_id,
                description: x.description,
            }));
        } else if (res.status != 403 && res.status != 401) {
            toast.error('Errore nel caricamento dei corsi.');
        }
    }

    onMount(async () => {
        loading = true;
        // Set default paid value from user settings
        if ($userData?.auto_paid_payment !== undefined) {
            paid = $userData.auto_paid_payment;
        }
        await Promise.all([fetchCategories(), fetchCustomAccounts(), fetchCourses()]);
        loading = false;
        // Initial dispatch after loading
        notifyChange();
    });

    // For MetaPaymentCategories component compatibility - this object is bound
    let metaData = {
        expense: false,
        meta_payment_categories: [],
    };

    // Keep metaData.expense in sync with local expense state
    $: metaData.expense = expense;
</script>

<div class="step-content">
    {#if loading}
        <div class="text-center py-10 d-flex justify-content-center">
            <div class="spinner spinner-primary spinner-lg" />
        </div>
    {:else if accounts.length === 0}
        <div class="d-flex flex-column justify-content-center align-items-center text-dark-50 my-5 font-weight-bolder">
            <p class="text-center">Devi configurare almeno un conto per poter creare pagamenti.</p>
        </div>
    {:else}
        <div class="px-2">
            <div class="d-flex flex-column flex-md-row justify-content-between">
                <TextInput
                    customClasses={'mx-2 px-0 w-100'}
                    editable={false}
                    active={false}
                    on:change={e => {
                        description = e.detail;
                        notifyChange();
                    }}
                    props={{
                        id: 'description',
                        name: 'description',
                        label: 'Descrizione',
                        placeholder: 'Inserisci una descrizione',
                        required: false,
                        value: description,
                    }} />
                <Currency
                    customClasses={'mx-2 px-0 min-w-10'}
                    editable={false}
                    on:change={e => {
                        amount = e.detail;
                        notifyChange();
                    }}
                    props={{
                        label: 'Importo',
                        name: 'amount',
                        id: 'amount',
                        placeholder: '0,00',
                        required: true,
                        value: amount,
                    }} />
            </div>
            <div class="d-flex flex-column flex-md-row justify-content-between flex-wrap">
                <SmartSelect
                    hideEmptyState={true}
                    customClasses={'mx-2 px-0 col-12 col-md-3'}
                    editable={false}
                    active={false}
                    on:change={e => {
                        custom_accounts = e.detail.value;
                        const account = accounts.filter(x => x.custom_account_id == e.detail.value)[0];
                        if (parseInt(account?.account_type) == 1) {
                            type = 'cash';
                        } else if (parseInt(account?.account_type) == 2) {
                            type = 'transfer';
                        }
                        notifyChange();
                    }}
                    props={{
                        id: 'custom_accounts',
                        name: 'custom_accounts',
                        label: 'Conto',
                        placeholder: 'Seleziona il conto',
                        required: true,
                        options: accounts?.map(x => ({
                            label: x.name,
                            value: x.custom_account_id,
                        })),
                        clearable: false,
                        searchable: false,
                        showChevron: true,
                        value: custom_accounts || accounts[0]?.custom_account_id,
                    }} />
                <SmartSelect
                    hideEmptyState={true}
                    customClasses={'mx-2 px-0 col-12 col-md-3'}
                    editable={false}
                    active={false}
                    on:change={e => {
                        type = e.detail.value;
                        notifyChange();
                    }}
                    props={{
                        id: 'type',
                        name: 'type',
                        label: 'Metodo di pagamento',
                        placeholder: 'Seleziona il metodo di pagamento',
                        required: false,
                        options: [
                            {value: 'default', label: 'non specificato'},
                            {value: 'cash', label: 'contanti'},
                            {value: 'transfer', label: 'Bonifico Bancario'},
                            {value: 'online', label: 'Altro mezzo Online'},
                            {value: 'sepa-transfer', label: 'Bonifico SEPA'},
                            {value: 'stripe', label: 'Stripe'},
                            {value: 'pos', label: 'PoS'},
                        ],
                        clearable: false,
                        searchable: false,
                        showChevron: true,
                        value: type,
                    }} />
                <SmartSelect
                    hideEmptyState={true}
                    customClasses={'mx-2 px-0 col-12 col-md-2'}
                    editable={false}
                    active={false}
                    on:change={e => {
                        expense = e.detail.value;
                        notifyChange();
                    }}
                    props={{
                        id: 'expense',
                        name: 'expense',
                        label: 'Tipologia',
                        placeholder: 'Seleziona la tipologia',
                        required: true,
                        options: [
                            {label: 'Entrata', value: false},
                            {label: 'Uscita', value: true},
                        ],
                        clearable: false,
                        searchable: false,
                        showChevron: true,
                        value: expense,
                    }} />
                <SmartSelect
                    customClasses={'mx-2 px-0 col-12 col-md-3'}
                    editable={false}
                    active={false}
                    on:change={e => {
                        subject = e.detail.value;
                        notifyChange();
                    }}
                    props={{
                        id: 'subject',
                        name: 'subject',
                        label: 'Tipo Quota',
                        placeholder: 'Seleziona tipologia quota',
                        required: true,
                        clearable: false,
                        searchable: false,
                        showChevron: true,
                        options: [
                            {value: 0, label: 'Altro'},
                            {value: 1, label: 'Iscrizione'},
                            {value: 2, label: 'Corso'},
                            {value: 3, label: 'Giroconto'},
                        ],
                        value: subject,
                    }} />
            </div>
            <div class="d-flex flex-column flex-md-row justify-content-between">
                <Datepicker
                    customClasses={'px-0 max-w-8 col-12 col-md-3'}
                    editable={false}
                    active={false}
                    on:change={e => {
                        creation_date = moment(e.detail, 'DD/MM/YYYY').format('YYYY-MM-DD');
                        notifyChange();
                    }}
                    props={{
                        id: 'creation_date',
                        name: 'creation_date',
                        label: expense ? 'Data Prevista Uscita' : 'Data Prevista Incasso',
                        required: true,
                        format: 'DD/MM/YYYY',
                        value: creation_date
                            ? moment(creation_date).format('DD/MM/YYYY')
                            : moment().format('DD/MM/YYYY'),
                    }} />
                <Datepicker
                    customClasses={'px-0 max-w-9 col-12 col-md-3'}
                    editable={false}
                    active={false}
                    on:change={e => {
                        if (e.detail) {
                            payment_date = moment(e.detail, 'DD/MM/YYYY').format('YYYY-MM-DD');
                        } else {
                            payment_date = null;
                        }
                        notifyChange();
                    }}
                    props={{
                        id: 'payment_date',
                        name: 'payment_date',
                        label: expense ? 'Data Pagamento' : 'Data Incasso',
                        required: false,
                        format: 'DD/MM/YYYY',
                        value: payment_date ? moment(payment_date).format('DD/MM/YYYY') : null,
                    }} />
                <SmartSelect
                    customClasses={'mx-2 px-0 col-12 col-md-6'}
                    editable={false}
                    active={false}
                    on:change={e => {
                        payment_category = e.detail.value;
                        notifyChange();
                    }}
                    props={{
                        id: 'payment_category',
                        name: 'payment_category',
                        label: 'Causale',
                        placeholder: 'Seleziona la causale',
                        required: true,
                        clearable: false,
                        searchable: false,
                        showChevron: true,
                        options: expense
                            ? categories
                                .filter(x => x.expense)
                                ?.map(x => ({label: x.name, value: x.payment_category_id}))
                            : categories
                                .filter(x => !x.expense)
                                ?.map(x => ({label: x.name, value: x.payment_category_id})),
                        value:
                            payment_category ||
                            (expense
                                ? categories?.filter(x => x.name == 'Compensi e Rimborsi Spese')[0]?.payment_category_id
                                : categories?.filter(x => x.name == 'entrate e proventi da attività tipiche')[0]
                                    ?.payment_category_id),
                    }} />
            </div>
            {#if subject == 2}
                <div class="d-flex flex-column flex-md-row justify-content-between pr-0 pr-md-4">
                    <SmartSelect
                        customClasses={'mx-2 px-0 col-12'}
                        editable={false}
                        active={false}
                        on:change={e => {
                            course = e.detail;
                            notifyChange();
                        }}
                        props={{
                            id: 'course',
                            name: 'course',
                            label: 'Corso associato',
                            placeholder: 'Seleziona il corso (opzionale)',
                            required: false,
                            clearable: true,
                            searchable: true,
                            showChevron: true,
                            options: courses?.length > 0 ? courses : [],
                            value: course?.value || null,
                        }} />
                </div>
            {/if}
            <MetaPaymentCategories
                bind:data={metaData}
                bind:categories
            />
            <TextArea
                customClasses={'mx-2 px-0'}
                editable={false}
                active={false}
                on:change={e => {
                    notes = e.detail;
                    notifyChange();
                }}
                props={{
                    id: 'notes',
                    name: 'notes',
                    label: 'Note',
                    placeholder: 'Inserisci delle note',
                    required: false,
                    rows: 3,
                    value: notes,
                }} />
        </div>
    {/if}
</div>
