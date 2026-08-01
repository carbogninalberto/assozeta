<script>
	import { X } from 'lucide-svelte';
    import {onMount} from 'svelte';
    import {sessionToken} from 'store/stores.js';
    import {initSelectpicker} from 'shim/select.js';
	import { UiApp } from 'shim/ui.js';

    sessionToken.useLocalStorage();

    export let isModal = false;
    export let exclude = []; // subscription ids to exclude
    // export let one_fee_payment = false;
    let athletes = [];

    $: athletes = athletes
        .filter(athlete => {
            return !exclude.includes(athlete.subscription_id);
        })
        .filter(athlete => {
            return athlete.current_year;
        });

    onMount(async () => {
        const url = __bakney.env.API.SUBSCRIPTION.LIST + '?m=1';

        const res = await window.fetch(url, {
            method: 'GET',
            headers: {
                Accept: 'application/json',
                'Content-Type': 'application/json',
                Authorization: 'Bearer ' + $sessionToken,
            },
        });

        const response = await res.json();
        // spinner stop
        UiApp.unblockPage();

        let result = JSON.stringify(response);
        if (res.status == 200) {
            let dataResult = response.data;
            athletes = [];
            for (let i = 0; i < Object.keys(dataResult).length; i++) {
                athletes.push(dataResult[i]);
            }
        }
    });

    var Select2Init = (function () {
        // Private functions
        var demos = function () {
            // limiting the number of selections
            initSelectpicker(document.getElementById('bkn_select2_athletes'), {
                liveSearch: true,
            });
        };
        // Public functions
        return {
            init: function () {
                demos();
            },
        };
    })();

    onMount(() => {
        Select2Init.init();
    });
</script>

<div class="pb-5" data-wizard-type="step-content">
    <div class="d-flex justify-content-between align-items-start">
        <h4 class="mb-10 font-weight-bold text-dark wizard-title-info">Assegna il carnet</h4>
        {#if isModal}
            <button type="button" class="close" data-dismiss="modal" aria-label="Close">
                <X size={16} aria-hidden="true" />
            </button>
        {/if}
    </div>

    <div class="form-group">
        <!-- svelte-ignore a11y-label-has-associated-control -->
        <label>Lista atleti</label>
        <select class="form-control select2" id="bkn_select2_athletes" name="athletes" multiple>
            {#if athletes.length > 0}
                {#each athletes as athlete}
                    <!-- svelte-ignore a11y-no-onchange -->
                    <option value={athlete.subscription_id}
                        >{athlete.associate.first_name.capitalize()} {athlete.associate.last_name.capitalize()}</option>
                {/each}
            {:else}
                <optgroup label="Nessuna iscrizione" />{/if}
        </select>
        <span class="form-text text-muted"
            >Inserisci il nome e/o cognome dell'atleta che stai cercando, oppure scorri la lista e premi sul nome per
            aggiungerlo. (CTRL+click per selezionarne più di uno)</span>
    </div>
</div>

<svelte:head>
    <style>
        .select2-container--default .select2-search--inline .select2-search__field {
            outline: none !important;
        }
    </style>
</svelte:head>
