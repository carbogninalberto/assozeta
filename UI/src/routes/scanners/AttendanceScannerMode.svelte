<script>
    import AttendanceScanner from 'components/capacitor/attendance-scanner.svelte';
    import PinPad from 'components/capacitor/partials/pin-pad.svelte';
    import QrCode from 'svelte-qrcode';
    import {FrameCorners, Gear} from 'phosphor-svelte';
    import {writable} from 'svelte/store';
    import SettingsModal from './modals/SettingsModal.svelte';
    import {Camera} from '@capacitor/camera';
    import {onMount} from 'svelte';
    import {oemConfig} from 'store/instanceStore.js';
    import {getWebSocketUrl} from 'utils/websocketUrl.js';

    let settings = writable({
        showScanner: true,
        showQrCode: false,
        showPinPad: false,
    });

    let isFullScreen = false;
    let size = 4;
    let show = true;

    settings.subscribe(value => {
        // count how many are true, if 3 active, size = 4, if 2 active, size = 6, if 1 active, size = 8, if 0 active, size = 12
        size =
            Object.values(value).filter(x => x).length === 3
                ? 4
                : Object.values(value).filter(x => x).length === 2
                ? 6
                : Object.values(value).filter(x => x).length === 1
                ? 8
                : 12;

        if ($settings.showQrCode) {
            initWebSocket();
        }
    });

    function handlePinConfirm(e) {
        alert(e.detail);
    }

    function toggleFullScreen() {
        if (!isFullScreen) {
            if (document.documentElement.requestFullscreen) {
                document.documentElement.requestFullscreen();
            } else if (document.documentElement.mozRequestFullScreen) {
                document.documentElement.mozRequestFullScreen();
            } else if (document.documentElement.webkitRequestFullscreen) {
                document.documentElement.webkitRequestFullscreen();
            } else if (document.documentElement.msRequestFullscreen) {
                document.documentElement.msRequestFullscreen();
            }
            isFullScreen = true;
        } else {
            if (document.exitFullscreen) {
                document.exitFullscreen();
            } else if (document.mozCancelFullScreen) {
                document.mozCancelFullScreen();
            } else if (document.webkitExitFullscreen) {
                document.webkitExitFullscreen();
            } else if (document.msExitFullscreen) {
                document.msExitFullscreen();
            }
            isFullScreen = false;
        }
    }

    function initWebSocket() {
        const socket = new WebSocket(getWebSocketUrl(__bakney.env.WS.UPDATES));

        socket.onopen = function (e) {
            console.log('WebSocket connection established');
            socket.send(JSON.stringify({message: 'Hello from client'}));
        };

        socket.onmessage = function (e) {
            const data = JSON.parse(e.data);
            console.log('Message from server:', data.message);
            // Handle the update here (e.g., update UI)
        };

        socket.onclose = function (e) {
            console.log('WebSocket connection closed');
        };

        return () => {
            socket.close();
        };
    }

    async function checkAndRequestCameraPermissions() {
        try {
            const permission = await Camera.checkPermissions();

            if (permission.camera === 'granted') {
                // Camera permission is already granted, proceed with using the camera
                console.log('Camera permission is granted');
            } else {
                // Request permission
                const request = await Camera.requestPermissions({
                    permissions: ['camera'],
                });

                if (request.camera === 'granted') {
                    console.log('Camera permission was granted');
                    // Proceed with using the camera
                } else {
                    console.log('Camera permission was denied');
                    // Handle the case where permission is denied
                }
            }
        } catch (error) {
            console.error('Error checking or requesting camera permissions:', error);
        }
    }

    onMount(() => {
        checkAndRequestCameraPermissions();
    });
</script>

<div class="m-1 md-4 border rounded-xl bg-white p-1 p-md-12" style="height: fit-content;">
    <div class="d-flex justify-content-between align-items-center mb-8" style="gap: 16px;">
        <div class="d-flex align-items-center">
            <img id="logo" class="h-30px" src={$oemConfig?.logo || ''} alt="logo" />
        </div>
        <div class="d-flex align-items-center justify-content-between" style="gap: 20px;">
            {#if !isFullScreen}
                <!-- <button
                    class="btn btn-icon btn-sm font-weight-bolder mb-0"
                    on:click={() => {
                        // create new modal
                        let modal = new SettingsModal({
                            target: document.querySelector(`body`),
                            intro: true,
                            props: {
                                show: true,
                                settings: $settings,
                            },
                        });

                        modal.$on('close', e => {
                            show = false;
                            settings.set(e.detail);
                            setTimeout(() => {
                                show = true;
                            }, 100);
                        });
                    }}>
                    <Gear weight="bold" size={24} />
                </button> -->
            {/if}
            <button class="btn btn-icon btn-clean btn-sm font-weight-bolder mb-0" on:click={toggleFullScreen}>
                {#if isFullScreen}
                    Esci
                {:else}
                    <FrameCorners weight="bold" size={24} class="text-success" />
                {/if}
            </button>
            {#if !isFullScreen}
                <a href="/" class="btn btn-light border border-light-dark btn-sm font-weight-bolder"> Esci </a>
            {/if}
        </div>
    </div>

    {#if typeof window !== 'undefined' && window.innerWidth >= 920}
        <h1 class="text-center font-weight-boldest font-size-h1 mb-12" style="font-size: 4rem!important;">Check-in</h1>
    {/if}
    <div class="row d-flex justify-content-center align-items-center m-auto">
        {#if show}
            {#if $settings.showScanner}
                <div
                    class="d-flex flex-column justify-content-between align-items-center col-12 col-md-{size}"
                    style="height: 500px;">
                    <h1 class="font-weight-boldest font-size-h1 text-center mb-4">
                        Mostra il tuo QR code della tua tessera
                    </h1>
                    <AttendanceScanner bind:fullScreen={isFullScreen} />
                </div>
            {/if}
            {#if $settings.showQrCode}
                <div
                    id="qr-code-container"
                    class="d-flex flex-column justify-content-between align-items-center col-12 col-md-{size} mt-12 mt-md-0"
                    style="height: 500px;">
                    <h1 class="font-weight-boldest font-size-h1 text-center mb-4">Scansiona il QR code</h1>
                    <div class="d-flex justify-content-center align-items-center" style="height: 100%;">
                        <QrCode value={window.location.origin} size={250} />
                    </div>
                </div>
            {/if}
            {#if $settings.showPinPad}
                <div
                    class="d-flex flex-column justify-content-between align-items-center col-12 col-md-{size} mt-12 mt-md-0"
                    style="height: 500px;">
                    <h1 class="font-weight-boldest font-size-h1 text-center mb-4">Digita il tuo PIN</h1>
                    <PinPad on:confirm={handlePinConfirm} />
                </div>
            {/if}
            {#if size === 12}
                <div class="col-12 col-md-12 mt-12 mt-md-0" style="height: 500px;">
                    <h1 class="font-weight-boldest font-size-h1 text-center mb-4">Scansiona il qr code</h1>
                    <AttendanceScanner />
                </div>
            {/if}
        {/if}
    </div>
    <div class="mt-24 text-center">
        <h2 class="font-weight-boldest">Effettua il check-in, in uno dei modi sopra indicati.</h2>
    </div>
</div>

<svelte:head>
    <style>
        body {
            background: #584d9114 !important;
        }
    </style>
</svelte:head>
