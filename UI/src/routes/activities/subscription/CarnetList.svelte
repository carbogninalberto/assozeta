<script>
    import {onDestroy} from 'svelte';
    import BackButton from 'components/buttons/BackButton.svelte';
    import BKNDatatable from 'components/tables/BKNDatatable.svelte';

    export let params = {};
    let datatable;

    const columns = [
        {
            field: 'title',
            title: 'Nome',
            width: 100,
            autoHide: false,
            template: function (row) {
                return '<span class="font-weight-bolder">' + row.title + '</span>';
            },
        },
        {
            field: 'course',
            title: 'Corso',
            template: function (row) {
                let contentText = '';
                for (let i = 0; i < row.course.length; i++) {
                    contentText += row.course[i].course_title + (row.course.length - 1 > i ? ', ' : '');
                }
                if (row.course.length == 0) {
                    contentText = 'Nessun corso associato';
                }
                return contentText;
            },
        },
        {
            field: 'amount',
            title: 'Importo',
            sortable: true,
            width: 80,
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            template: function (row) {
                let amount =
                    "<span style='font-weight:700;'>€ " +
                    String(row.payment?.amount).replace('.', ',') +
                    '</span>';
                return amount;
            },
        },
        {
            field: 'creation_date',
            title: 'Data',
            type: 'date',
            width: 80,
            responsive: {
                visible: 'lg',
                hidden: 'md',
            },
            sortCallback: function (data, sort, column) {
                let dataArray = Object.values(data);
                dataArray.sort(function (a, b) {
                    let timeA = new Date(a['creation_date']).getTime();
                    let timeB = new Date(b['creation_date']).getTime();
                    if (sort === 'asc') {
                        return parseFloat(timeA) > parseFloat(timeB)
                            ? 1
                            : parseFloat(timeA) < parseFloat(timeB)
                            ? -1
                            : 0;
                    } else {
                        return parseFloat(timeA) < parseFloat(timeB)
                            ? 1
                            : parseFloat(timeA) > parseFloat(timeB)
                            ? -1
                            : 0;
                    }
                });
                let newData = {};
                for (let i = 0; i < dataArray.length; i++) {
                    newData[i] = dataArray[i];
                }

                return newData;
            },
            template: function (row) {
                return moment(new Date(row.creation_date)).format('DD/MM/YYYY');
            },
        },
        {
            field: 'meta',
            title: 'Lezioni',
            autoHide: false,
            sortable: false,
            textAlign: 'right',
            width: 70,
            minWidth: '100%',
            template: function (row) {
                return (
                    '<span class="font-weight-bolder text-primary" style="word-break: keep-all;">' +
                    '<span class="text-success">' +
                    row.lessons_left +
                    '</span>' +
                    '/' +
                    row.lessons_counter +
                    '<span>'
                );
            },
        },
    ];

    onDestroy(() => {
        document.querySelectorAll('.popover').forEach(popover => popover.remove());
        document.querySelectorAll('.tooltip').forEach(popover => popover.remove());
    });
</script>

<!--begin::Entry-->
<div  class="d-flex flex-column-fluid">
    <!--begin::Container-->
    <div class="container container-overlay">
        <!--begin::Card-->
        <div class="card card-custom gutter-b">
            <div class="card-header pt-4 pb-0 header-mobile-btn-back" style="padding-bottom: 0 !important;">
                <div class="card-toolbar d-flex gap-4" style="gap: .5rem;">
                    <BackButton />
                </div>
                <div class="card-toolbar">
                    <h3 class="card-title font-size-h2">Carnet</h3>
                </div>
                <div class="card-toolbar" />
            </div>
            <div class="card-body pt-4">
                <BKNDatatable
                    bind:datatable
                    {columns}
                    url={__bakney.env.API.CARNET_SUBSCRIPTION.LIST}
                    params={{
                        subscription_id: params.subscriptionId,
                    }}
                    mapFunction={raw => {
                        if (typeof raw.data !== 'undefined') {
                            return raw.data;
                        }
                        return [];
                    }}
                    serverPaging={false}
                    serverFiltering={false}
                    serverSorting={false}
                    showDividerFilter={false} />
            </div>
        </div>
    </div>
    <!--end::Container-->
</div>
<!--end::Entry-->

<svelte:head>
    <style>
        .nav-link {
            cursor: pointer;
        }
        .nav-link.active {
            border-bottom: 4px solid #351dc2 !important;
        }
        .nav-link:hover {
            border-bottom: 4px solid #351dc2 !important;
        }
        .card-toolbar::-webkit-scrollbar {
            display: none;
        }
    </style>
</svelte:head>
