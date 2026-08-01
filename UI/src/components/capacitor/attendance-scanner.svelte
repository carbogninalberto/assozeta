<script>
    import {replaceUID} from 'utils/ApiMiddleware.js';
    import {onMount, onDestroy} from 'svelte';
    import {Html5Qrcode} from 'html5-qrcode';
    import {apiFetch} from 'utils/ApiMiddleware';
    import {toast} from 'svelte-sonner';

    let scanResult = '';
    let errorMessage = '';
    let html5QrCode;
    let isScanning = false;
    let cameras = [];
    let selectedCameraId = null;
    export let fullScreen = false;

    async function markAttendance(subscriptionId) {
        const res = await apiFetch(`${replaceUID(__bakney.env.API.ATTENDANCE.MARK, subscriptionId)}`, {
            method: 'POST',
        });
        if (res.status === 200) {
            // toast.success(res.response.msg || 'Presenza registrata');
            swal.fire({
                text: res.response.msg || 'Presenza registrata',
                icon: 'success',
                buttonsStyling: false,
                confirmButtonText: 'Continua',
                customClass: {
                    confirmButton: 'btn font-weight-bold btn-primary',
                },
            }).then(() => {
                startContinuousScan();
            });
        } else {
            if (res.status === 207) {
                const courses = res.response.data;
                if (courses && courses.length > 0) {
                    const courseOptions = courses.map(course => ({
                        text: `${course.course.title}`,
                        value: course.attendance_day_id,
                    }));

                    swal.fire({
                        title: 'Seleziona il corso',
                        input: 'select',
                        inputOptions: Object.fromEntries(courseOptions.map(option => [option.value, option.text])),
                        inputValue: courseOptions[0].value, // Select the first option by default
                        showCancelButton: true,
                        cancelButtonText: 'Annulla',
                        confirmButtonText: 'Conferma',
                        showLoaderOnConfirm: true,
                        buttonsStyling: false,
                        customClass: {
                            confirmButton: 'btn font-weight-bold btn-primary',
                            cancelButton: 'btn font-weight-bold btn-light text-dark',
                        },
                        reverseButtons: true,
                        allowOutsideClick: () => !swal.isLoading(),
                    }).then(result => {
                        if (result.isConfirmed) {
                            const selectedAttendanceDayId = result.value;
                            apiFetch(`${replaceUID(__bakney.env.API.ATTENDANCE.MARK, subscriptionId)}`, {
                                method: 'POST',
                                body: JSON.stringify({attendance_day_id: selectedAttendanceDayId}),
                            }).then(res => {
                                if (res.status === 200) {
                                    swal.fire({
                                        text: res.response.msg || 'Presenza registrata',
                                        icon: 'success',
                                        buttonsStyling: false,
                                        confirmButtonText: 'Continua',
                                        customClass: {
                                            confirmButton: 'btn font-weight-bold btn-light-primary',
                                        },
                                    });
                                } else {
                                    swal.fire({
                                        text: res.response.msg || 'Errore durante il salvataggio della presenza',
                                        icon: 'error',
                                        buttonsStyling: false,
                                        confirmButtonText: 'Continua',
                                        customClass: {
                                            confirmButton: 'btn font-weight-bold btn-primary',
                                        },
                                    });
                                }
                                startContinuousScan();
                            });
                        } else {
                            startContinuousScan();
                        }
                    });
                } else {
                    startContinuousScan();
                }
            } else {
                swal.fire({
                    text: res.response.msg || 'Errore durante il salvataggio della presenza',
                    icon: 'error',
                    buttonsStyling: false,
                    confirmButtonText: 'Continua',
                    customClass: {
                        confirmButton: 'btn font-weight-bold btn-primary',
                    },
                }).then(() => {
                    startContinuousScan();
                });
            }
        }
    }

    async function fetchCameras() {
        try {
            cameras = await Html5Qrcode.getCameras();
            if (cameras && cameras.length) {
                selectedCameraId = cameras[0].id; // Default to the first camera
            }
        } catch (error) {
            errorMessage = `Error fetching cameras: ${error.message}`;
        }
    }

    async function startContinuousScan() {
        if (isScanning) {
            await stopContinuousScan();
        }
        if (selectedCameraId) {
            try {
                html5QrCode = new Html5Qrcode('reader');
                isScanning = true;
                await html5QrCode.start(
                    {deviceId: {exact: selectedCameraId}},
                    {
                        fps: 10,
                        qrbox: {width: 250, height: 250},
                        aspectRatio: 1.0,
                    },
                    (decodedText, decodedResult) => {
                        stopContinuousScan();
                        scanResult = decodedText;
                        errorMessage = '';
                        markAttendance(scanResult);
                        scanResult = '';
                    },
                    error => {
                        // console.log(`QR Code scan error: ${error}`);
                    }
                );
            } catch (error) {
                errorMessage = `Error starting QR code scanner: ${error.message}`;
            }
        }
    }

    async function stopContinuousScan() {
        if (isScanning && html5QrCode) {
            try {
                await html5QrCode.stop();
                isScanning = false;
            } catch (err) {
                // console.error('Failed to stop scanning.', err);
            }
        }
    }

    onMount(async () => {
        await fetchCameras();
        if (selectedCameraId) {
            startContinuousScan();
        }
    });

    onDestroy(() => {
        stopContinuousScan();
    });
</script>

{#if !fullScreen}
    <div>
        <select
            class="form-control form-control-sm form-control-solid rounded-lg mb-4 border border-light-dark"
            bind:value={selectedCameraId}
            on:change={startContinuousScan}>
            {#each cameras as camera}
                <option value={camera.id}>{camera.label}</option>
            {/each}
        </select>
    </div>
{/if}

<div id="reader" class="rounded-xl overflow-hidden shadow-sm" />

<style>
    #reader {
        width: 100%;
        height: 100%;
        max-width: 500px;
        max-height: 500px;
        margin: auto;
        position: relative;
    }
</style>
