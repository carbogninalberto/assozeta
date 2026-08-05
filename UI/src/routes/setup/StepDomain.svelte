<script>
	import { ArrowRight, Info as LucideInfo } from 'lucide-svelte';
    import {createEventDispatcher} from 'svelte';
    import {Info, Globe, Link} from 'phosphor-svelte';
    import {validateSetupToken} from 'store/instanceStore.js';

    export let config = {
        domain: ''
    };
    export let setupToken = '';

    const dispatch = createEventDispatcher();

    let error = null;
    let tokenError = null;
    let validatingToken = false;

    async function validateAndNext() {
        if (validatingToken) return;

        error = null;
        tokenError = null;

        if (!config.domain || config.domain.trim() === '') {
            error = 'Il dominio è obbligatorio';
            return;
        }

        // Basic domain validation
        const domainRegex = /^[a-zA-Z0-9][a-zA-Z0-9-]{0,61}[a-zA-Z0-9](?:\.[a-zA-Z]{2,})+$|^localhost$/;
        if (!domainRegex.test(config.domain) && config.domain !== 'localhost') {
            // Allow IP addresses too
            const ipRegex = /^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$/;
            if (!ipRegex.test(config.domain)) {
                error = 'Inserisci un dominio valido (es. app.miaassociazione.it)';
                return;
            }
        }

        const normalizedSetupToken = setupToken.trim();
        if (!normalizedSetupToken) {
            tokenError = 'Il token di setup è obbligatorio';
            return;
        }

        validatingToken = true;
        try {
            const result = await validateSetupToken(normalizedSetupToken);
            if (!result.valid) {
                tokenError = result.error || 'Il token di setup non è valido';
                return;
            }

            setupToken = normalizedSetupToken;
            dispatch('next');
        } catch (e) {
            tokenError = 'Impossibile verificare il token di setup. Riprova.';
        } finally {
            validatingToken = false;
        }
    }
</script>

<div class="step-domain">
    <div class="text-center mb-5">
        <div class="step-icon mx-auto mb-4">
            <Globe size={32} weight="duotone" />
        </div>
        <h2 class="font-weight-bolder mb-2">Configurazione Dominio</h2>
        <p class="text-muted font-size-sm">
            Configura il dominio su cui sarà accessibile la tua istanza
        </p>
    </div>

    <div class="form-group">
        <label class="col-form-label font-weight-bolder text-left">Dominio</label>
        <div class="input-group">
            <div class="input-group-prepend">
                <span class="input-group-text bg-secondary border-0">
                    <Link size={18} weight="duotone" class="text-dark" />
                </span>
            </div>
            <input
                type="text"
                class="form-control form-control-solid"
                class:is-invalid={error}
                bind:value={config.domain}
                placeholder="app.miaassociazione.it"
                on:keydown={e => e.key === 'Enter' && validateAndNext()}
            />
        </div>
        {#if error}
            <div class="invalid-feedback d-block">{error}</div>
        {/if}
        <small class="text-muted font-size-sm mt-2 d-block">
            Il dominio viene rilevato automaticamente. Modificalo solo se necessario.
        </small>
    </div>

    <div class="form-group">
        <label class="col-form-label font-weight-bolder text-left">Token di Setup<b class="text-danger">*</b></label>
        <input
            type="password"
            class="form-control form-control-solid"
            class:is-invalid={tokenError}
            bind:value={setupToken}
            placeholder="Inserisci il token di bootstrap"
            autocomplete="off"
            spellcheck="false"
            on:keydown={e => e.key === 'Enter' && validateAndNext()}
        />
        {#if tokenError}
            <div class="invalid-feedback d-block">{tokenError}</div>
        {/if}
        <small class="text-muted font-size-sm mt-2 d-block">
            Il token protegge la configurazione iniziale e non viene salvato nel browser.
        </small>
    </div>

    <div class="info-box d-flex align-items-center text-info font-weight-bold rounded-lg mb-4">
        <div class="alert-icon mr-2">
            <LucideInfo size={18} weight="duotone" />
        </div>
        <div class="alert-text font-size-sm">
            Il dominio configurato verrà utilizzato per identificare questa istanza.
            Assicurati che il <b class="font-weight-boldest">DNS</b> punti correttamente a questo server.
        </div>
    </div>

    <div class="d-flex justify-content-end">
        <button
            type="button"
            class="btn btn-primary font-weight-bolder"
            disabled={validatingToken}
            on:click={validateAndNext}
        >
            {validatingToken ? 'Verifica...' : 'Continua'}
            <ArrowRight size={16} class="ml-2" />
        </button>
    </div>
</div>

<style>
    .step-icon {
        width: 70px;
        height: 70px;
        border-radius: 50%;
        background: linear-gradient(135deg, rgba(53, 29, 194, 0.1) 0%, rgba(53, 29, 194, 0.05) 100%);
        display: flex;
        align-items: center;
        justify-content: center;
        color: var(--main-color, #351DC2);
    }

    .step-domain :global(.input-group .form-control.form-control-solid) {
        border-top-left-radius: 0 !important;
        border-bottom-left-radius: 0 !important;
    }

    .info-box {
        background-color: #f0f7ff;
        border: 1px solid #bfdbfe;
        padding: 1rem;
    }
</style>
