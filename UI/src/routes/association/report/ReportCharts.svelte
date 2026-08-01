<script>
	import { AlertTriangle } from 'lucide-svelte';
    import ContentLoader from 'svelte-content-loader';
    import {onMount} from 'svelte';
    import {apiFetch} from 'utils/ApiMiddleware.js';
    import {userData} from 'store/stores.js';
    import Subscriptions from './charts/Subscriptions.svelte';
    import Payments from './charts/Payments.svelte';
    import Courses from './charts/Courses.svelte';
    import SubscriptionsPie from './charts/SubscriptionsPie.svelte';
    import Kpi from './charts/PaymentsKPI.svelte';
    import PaymentsPie from './charts/PaymentsPie.svelte';
    import SubscriptionsKpi from './charts/SubscriptionsKPI.svelte';
    import { initTooltips, destroyTooltips } from 'shim/tooltip.js';
    import { initPopovers, destroyPopovers } from 'shim/popover.js';
	import { UiApp } from 'shim/ui.js';

    userData.useLocalStorage();

    let loading = false;
    let data = {};
    let availableYears = [];
    let selectedYear;

    onMount(async () => {
        await fetchData();
        document.querySelectorAll('.popover').forEach(popover => popover.remove());
        initTooltips(document.body);
        initPopovers(document.body);
    });

    async function fetchData() {
        loading = true;
        if (selectedYear) {
UiApp.block('#content-graphs', {
                overlayColor: '#000000',
                state: 'primary',
                message: 'Caricamento in corso...',
            });
        }
        let url = __bakney.env.API.STATISTIC.REPORT;
        selectedYear = parseInt(document.getElementById('report-year')?.value || selectedYear);
        if (selectedYear) {
            let selectedDate;
            for (let year of availableYears) {
                if (year.year === selectedYear) {
                    selectedDate = year.start_date;
                    break;
                }
            }
            if (!selectedDate) {
                console.error(`selectedDate not found for year ${selectedYear} in ${availableYears}`);
                return;
            }
            let parsedDate = moment(selectedDate).format('YYYY-MM-DD');
            url += '?currentDate=' + parsedDate;
        }
        let res = await apiFetch(url);
        if (!res.error) {
            data = res.response.data;
            // set as last available year the first time we load the page
            if (!selectedYear && data.available_years.length > 0) {
                selectedYear = data.available_years[data.available_years.length - 1].year;
            }
            availableYears = data.available_years;
            loading = false;
        }
        setTimeout(() => {
            UiApp.unblock('#content-graphs');
        }, 500);
    }

    function printPDF() {
let subscriptionChart = document.getElementById('subscriptions-chart');
        let paymentsChart = document.getElementById('payments-chart');
        let coursesChart = document.getElementById('courses-chart');
        if (subscriptionChart) subscriptionChart.style = 'width:25.5cm!important';
        if (paymentsChart) paymentsChart.style = 'width:25.5cm!important';
        if (coursesChart) coursesChart.style = 'width:25.5cm!important';
        setTimeout(() => {
            window.print();
            if (subscriptionChart) subscriptionChart.style.width = '100%';
            if (paymentsChart) paymentsChart.style.width = '100%';
            if (coursesChart) coursesChart.style.width = '100%';
            UiApp.unblock('#content-graphs');
        }, 500);
    }
</script>

<!--begin::Content-->
<div id="content-graphs" class="flex-row-fluid">
    <!--begin::Card-->
    <div class="card card-custom card-stretch">
        <!--begin::Header-->
        <div class="card-header p-0 border-0">
            <div class="card-title">
                <h3 id="title-report" class="card-label font-size-h2">
                    <span id="title-description-report">Report</span>
                    <span id="title-description-report-print">Report {$userData.sport_association.denomination}</span>
                    <span id="title-description-balance-sheet" class="d-block text-muted pt-2 font-size-sm"
                        >Visualizza le statistiche per avere un'idea chiara di come sta andando.</span>
                </h3>
            </div>
            <div class="card-toolbar">
                <button class="btn btn-primary font-weight-bolder mr-2" on:click={printPDF}> Stampa PDF</button>
            </div>
        </div>
        <!--end::Header-->
        <div>
            <div class="card-body balance-sheet-card p-0" style="min-height: 100vh;">
                {#if window.screen.width < 768}
                    <div class="alert alert-custom alert-light-warning fade show my-5" role="alert">
                        <div class="alert-icon"><AlertTriangle size={16} /></div>
                        <span class="d-flex align-items-center text-bolder text-size-md text-warning"
                            >Si consiglia di visualizzare e stampare questa pagina da un computer.</span>
                    </div>
                {/if}
                {#if !loading}
                    <div class="row mb-2 mt-4 not-printable">
                        <div class="col-xl-6">
                            <!-- svelte-ignore a11y-label-has-associated-control -->
                            {#if selectedYear}
                                <label class="font-weight-bolder h5 mb-2"> Anno report </label>
                                <div style="max-width: 8rem">
                                    <select
                                        on:change={fetchData}
                                        bind:value={selectedYear}
                                        id="report-year"
                                        class="form-control form-control-solid">
                                        {#each availableYears || [] as year}
                                            <option value={year.year}>{year.year}</option>
                                        {/each}
                                    </select>
                                </div>
                            {/if}
                        </div>
                    </div>
                    <div class="row report-chart-row mb-0">
                        <div class="w-100">
                            <Subscriptions bind:subscriptions={data.subscriptions} />
                        </div>
                    </div>
                    <div class="section-description-print report-chart-description text-dark-50 font-weight-bolder font-size-sm">
                        Nel grafico è raffigurato l'andamento del numero di iscrizioni ragruppato per mese. Il periodo
                        di riferimento è l'anno sociale corrente.
                    </div>
                    <div class="row report-chart-row report-chart-row-spaced mb-0">
                        <div class="col-6 subscription-pie-chart">
                            <SubscriptionsPie bind:data={data.pie_subscriptions} />
                        </div>
                        <div class="col pr-0">
                            <SubscriptionsKpi bind:data={data.kpi_subscriptions} />
                        </div>
                    </div>
                    <div class="section-description-print report-chart-description text-dark-50 font-weight-bolder font-size-sm">
                        Gli indicatori chiave di prestazione (KPI) riportati in questa sezione, permettono di avere una
                        panoramica degli atleti iscritti all'associazione.
                    </div>
                    <div class="row report-chart-row mb-0">
                        <div class="w-100">
                            <Payments bind:payments={data.payments} />
                        </div>
                    </div>
                    <div class="section-description-print report-chart-description text-dark-50 font-weight-bolder font-size-sm">
                        Nel grafico sono raffigurate le entrate ragruppate per mese dervianti dai pagamenti. Il periodo
                        di riferimento è l'anno sociale corrente.
                    </div>
                    <div class="row report-chart-row report-chart-row-spaced mb-0">
                        <div class="col-6 payment-pie-chart">
                            <PaymentsPie bind:data={data.pie_payments} />
                        </div>
                        <div class="col pr-0">
                            <Kpi bind:data={data.kpi_payments} />
                        </div>
                    </div>
                    <div class="section-description-print report-chart-description text-dark-50 font-weight-bolder font-size-sm">
                        Gli indicatori chiave di prestazione (KPI) riportati in questa sezione, permettono di avere una
                        panoramica generale dell'andamento finanziario ed operativo dell'associazione.
                    </div>
                    <div class="row report-chart-row report-chart-row-spaced mb-0">
                        <div class="w-100">
                            <Courses bind:courses={data.courses} />
                        </div>
                    </div>
                    <div class="section-description-print report-chart-description text-dark-50 font-weight-bolder font-size-sm">
                        Nel grafico sono raffigurati il numero di atleti per corso. Inoltre, sono anche riportate le
                        informazioni relative alle entrate di ogni corso.
                    </div>
                {/if}

                {#if loading}
                    <div class="py-8 px-2">
                        <ContentLoader height="2rem" width="10rem" primaryColor="#6b6b6b1f" secondaryColor="#eaf1f7" />
                    </div>
                    <div class="p-2">
                        <ContentLoader height="30rem" width="100%" primaryColor="#6b6b6b1f" secondaryColor="#eaf1f7" />
                    </div>
                    <div class="py-8 px-2">
                        <ContentLoader height="2rem" width="10rem" primaryColor="#6b6b6b1f" secondaryColor="#eaf1f7" />
                    </div>
                    <div class="p-2">
                        <ContentLoader height="30rem" width="100%" primaryColor="#6b6b6b1f" secondaryColor="#eaf1f7" />
                    </div>
                    <div class="py-8 px-2">
                        <ContentLoader height="2rem" width="10rem" primaryColor="#6b6b6b1f" secondaryColor="#eaf1f7" />
                    </div>
                    <div class="p-2">
                        <ContentLoader height="30rem" width="100%" primaryColor="#6b6b6b1f" secondaryColor="#eaf1f7" />
                    </div>
                {/if}
            </div>
        </div>
    </div>
</div>

<svelte:head>
    <style>
        .balance-sheet-card {
            padding-top: 0;
            overflow: visible;
        }

        .balance-sheet-table {
            width: 100%;
            background: var(--bg-surface);
            border-radius: 0.55rem !important;
            border-collapse: collapse;
            border-style: hidden;
            box-shadow: 0 0 0 1px #e4e6ef;
            min-width: 45rem;
            overflow: scroll;
        }

        .balance-sheet-table td:first-child {
            text-align: left;
        }

        .balance-sheet-table th,
        .balance-sheet-table td {
            border-bottom: 1px solid #e4e9f0;
            padding: 1rem;
            text-align: right;
        }

        .balance-sheet-table tr {
            border-bottom: 1px solid #e4e9f0;
            padding: 1rem;
            text-align: right;
        }

        .subtotal td {
            font-size: 1.1rem;
            font-weight: bolder;
        }

        #logo-print {
            display: none;
        }

        #title-description-report-print {
            display: none;
        }

        :global(.report-chart-body) {
            position: relative;
        }

        .report-chart-row {
            padding: 1rem 1rem 0;
        }

        .report-chart-row-spaced {
            margin-top: 1.75rem;
        }

        :global(.report-chart-card) {
            margin-bottom: 0.65rem;
        }

        .report-chart-description {
            margin: 0 1rem 1.5rem;
            padding: 0.75rem 1rem;
            background: #f8f9fc;
            border: 1px solid #edf0f6;
            border-radius: 0.55rem;
            line-height: 1.45;
        }

        :global(.report-chart-download) {
            position: absolute;
            top: 1rem;
            right: 1rem;
            z-index: 20;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 2rem;
            height: 2rem;
            padding: 0;
            color: #5e73b7;
            background: transparent;
            border: 0;
            border-radius: 0.45rem;
            cursor: pointer;
            pointer-events: auto;
            transition: color 0.15s ease, background-color 0.15s ease;
        }

        :global(.report-chart-body canvas) {
            position: relative;
            z-index: 1;
        }

        :global(.report-chart-download:hover) {
            color: #255aee;
            background: rgba(37, 90, 238, 0.08);
        }

        @page {
            /* margin: 0; */
            /* size: A4 portrait; */
            size: 21cm 29.7cm;
            margin: 15mm 10mm 15mm 10mm;
        }

        @media print {
            #content-graphs {
                background: var(--border-color-dark);
            }

            .not-printable {
                display: none !important;
            }

            .card-header {
                border: 0 !important;
            }

            #title-description-report-print {
                display: block;
            }

            #title-description-report {
                display: none;
            }

            html,
            body {
                height: 100vh;
                margin: 0 !important;
                padding: 0 !important;
                page-break-after: auto;
            }

            #title-report {
                font-size: 2rem !important;
            }

            /* .print:last-child {
                page-break-after: auto;
            } */
            .content,
            #bkn_wrapper,
            .container {
                padding: 0 !important;
            }

            .card-toolbar {
                display: none !important;
            }

            #engage-msg {
                display: none !important;
            }

            #title-description-balance-sheet {
                display: none !important;
            }

            .theader-balance-sheet .btn-clean {
                display: none !important;
            }

            #bkn_header_mobile,
            #bkn_header,
            #bkn_footer {
                display: none !important;
            }

            .aside {
                display: none !important;
            }

            #logo-print {
                display: block !important;
            }

            .selected-year {
                display: none !important;
            }

            #bkn_quick_user {
                display: none !important;
            }
        }
    </style>
</svelte:head>
