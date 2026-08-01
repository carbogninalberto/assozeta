<script>
    import {link} from 'svelte-spa-router';
    import {
        Archive,
        ArrowLeft,
        ArrowRight,
        Calendar,
        ChartBar,
        ChartPie,
        ChartPieSlice,
        Coins,
        RocketLaunch,
        Star,
        Ticket,
        TreeStructure,
        UserList,
        WarningCircle,
    } from 'phosphor-svelte';
    import {scale} from 'svelte/transition';
    import * as easing from 'svelte/easing';
</script>

<div
    
    class="d-flex flex-column-fluid m-auto justify-content-center align-items-center">
    <div class="text-center font-weight-bolder">
        <h1
            class="font-weight-boldest text-danger mb-4 d-flex flex-column align-items-center justify-content-center"
            style="font-size: 2.3rem;">
            <WarningCircle size={90} weight="duotone" class="mb-4" /><span class="ml-4 mr-4">Piano scaduto!</span>
        </h1>

        <div class="pt-4 pb-8">
            <a href="/#/subscription" class="btn btn-danger btn-lg font-weight-bolder"
                >Continua <ArrowRight weight="bold" /></a>
        </div>
        <div class="font-weight-bolder font-size-lg p-0" style="max-width: 70rem;">
            Con il <b class="text-secondary">piano Pro</b> hai accesso a tutte le funzioni!
            <div class="row p-2 mt-4 font-size-sm font-weight-boldest text-primary">
                <div class="col-12 col-md-4">
                    <div
                        class="m-4 bg-white shadow p-6 rounded-lg d-flex flex-column justify-content-center align-items-center">
                        <Calendar size={50} class="mb-2" weight="duotone" />
                        Calendario Completo
                    </div>
                </div>
                <div class="col-12 col-md-4">
                    <div
                        class="m-4 bg-white shadow p-6 rounded-lg d-flex flex-column justify-content-center align-items-center">
                        <Coins size={50} class="mb-2" weight="duotone" />
                        Pagamenti in Contanti
                    </div>
                </div>
                <div class="col-12 col-md-4">
                    <div
                        class="m-4 bg-white shadow p-6 rounded-lg d-flex flex-column justify-content-center align-items-center">
                        <Archive size={50} class="mb-2" weight="duotone" />
                        Archivio
                    </div>
                </div>
                <div class="col-12 col-md-4">
                    <div
                        class="m-4 bg-white shadow p-6 rounded-lg d-flex flex-column justify-content-center align-items-center">
                        <ChartPieSlice size={50} class="mb-2" weight="duotone" />
                        Report Statistiche
                    </div>
                </div>
                <div class="col-12 col-md-4">
                    <div
                        class="m-4 bg-white shadow p-6 rounded-lg d-flex flex-column justify-content-center align-items-center">
                        <ChartBar size={50} class="mb-2" weight="duotone" />
                        Bilancio Automatico
                    </div>
                </div>
                <div class="col-12 col-md-4">
                    <div
                        class="m-4 bg-white shadow p-6 rounded-lg d-flex flex-column justify-content-center align-items-center">
                        <UserList size={50} class="mb-2" weight="duotone" />
                        Gestione Presenze
                    </div>
                </div>
                <div class="col-12 col-md-4">
                    <div
                        class="m-4 bg-white shadow p-6 rounded-lg d-flex flex-column justify-content-center align-items-center">
                        <Ticket size={50} class="mb-2" weight="duotone" />
                        Gestione Carnet
                    </div>
                </div>
                <div class="col-12 col-md-4">
                    <div
                        class="m-4 bg-white shadow p-6 rounded-lg d-flex flex-column justify-content-center align-items-center">
                        <TreeStructure size={50} class="mb-2" weight="duotone" />
                        Automazione Email
                    </div>
                </div>
                <div class="col-12 col-md-4">
                    <div
                        class="m-4 bg-white shadow p-6 rounded-lg d-flex flex-column justify-content-center align-items-center">
                        <RocketLaunch size={50} class="mb-2" weight="duotone" />
                        E Molto Altro!
                    </div>
                </div>
            </div>
        </div>
    </div>
</div>
