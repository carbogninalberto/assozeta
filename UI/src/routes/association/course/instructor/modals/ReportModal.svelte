<script>
    import BasicModal from 'components/modals/BasicModal.svelte';
    import DateRangePicker from 'components/inputs/DateRangePicker.svelte';
    import {apiFetch} from 'utils/ApiMiddleware';
    import {toast} from 'svelte-sonner';

    export let show;

    let formData = {
        from_date: moment().startOf('month').format('DD/MM/YYYY'),
        to_date: moment().endOf('month').format('DD/MM/YYYY'),
    };

    let valid = true;

    function checkValidity() {
        valid = !!(formData.from_date && formData.to_date);
    }

    async function generateReport() {
        let res = await apiFetch(
            `${__bakney.env.API.INSTRUCTOR.REPORT}?start_date=${formData.from_date}&end_date=${formData.to_date}`
        );
        if (!res.error) {
            toast.success(res?.response?.msg ?? 'Report generato con successo');

            window.downloadFile(
                `report-istruttori-${formData.from_date}-${formData.to_date}.xlsx`,
                res.response.data.report_file,
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            );
        } else {
            toast.error(res?.response?.msg ?? 'Errore nella generazione del report');
        }
    }
</script>

<BasicModal
    id={`report-instructors-modal`}
    bind:show
    title="Genera Report Istruttori"
    showTitle={true}
    showFooter={false}
    modalSize={''}
    scrollable={false}
    on:confirm={() => generateReport()}
    dataHeight={300}>
    <div class="py-3">
        <DateRangePicker
            id="instructor_report_range"
            name="instructor_report_range"
            format="DD/MM/YYYY"
            required={true}
            sizeClass=""
            startPlaceholder="Dal"
            endPlaceholder="Al"
            bind:startValue={formData.from_date}
            bind:endValue={formData.to_date}
            on:change={checkValidity}
        />
    </div>
    <div class="modal-footer d-flex justify-content-center">
        <button disabled={!valid} type="button" class="btn btn-primary font-weight-boldest" on:click={generateReport}>
            Genera Report
        </button>
    </div>
</BasicModal>
