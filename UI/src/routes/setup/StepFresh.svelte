<script>
	import { ArrowLeft, ArrowRight, Check, Eye, EyeOff, X } from 'lucide-svelte';
    import {createEventDispatcher} from 'svelte';
    import {Plus, Info} from 'phosphor-svelte';

    export let config = {
        associationName: '',
        ownerEmail: '',
        ownerPassword: '',
        ownerPasswordConfirm: ''
    };

    const dispatch = createEventDispatcher();

    let errors = {};
    let showPassword = false;

    function validate() {
        errors = {};

        if (!config.associationName || config.associationName.trim() === '') {
            errors.associationName = 'Il nome dell\'associazione è obbligatorio';
        }

        if (!config.ownerEmail || config.ownerEmail.trim() === '') {
            errors.ownerEmail = 'L\'email è obbligatoria';
        } else if (!isValidEmail(config.ownerEmail)) {
            errors.ownerEmail = 'Inserisci un\'email valida';
        }

        if (!config.ownerPassword || config.ownerPassword.length < 10) {
            errors.ownerPassword = 'La password deve avere almeno 10 caratteri';
        } else if (!isValidPassword(config.ownerPassword)) {
            errors.ownerPassword = 'La password deve contenere almeno 1 maiuscola, 1 numero e 1 carattere speciale';
        }

        if (config.ownerPassword !== config.ownerPasswordConfirm) {
            errors.ownerPasswordConfirm = 'Le password non corrispondono';
        }

        return Object.keys(errors).length === 0;
    }

    function isValidEmail(email) {
        return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
    }

    function isValidPassword(password) {
        // At least 1 uppercase, 1 number, 1 special character, min 10 chars
        return /^(?=.*[A-Z])(?=.*[!@#$&.\-_*])(?=.*[0-9]).{10,}$/.test(password);
    }

    function handleNext() {
        if (validate()) {
            dispatch('next');
        }
    }
</script>

<div class="step-fresh">
    <div class="text-center mb-5">
        <div class="step-icon mx-auto mb-4">
            <Plus size={32} weight="duotone" />
        </div>
        <h2 class="font-weight-bolder mb-2">Crea Nuova Associazione</h2>
        <p class="text-muted font-size-sm">
            Inserisci i dati per creare la tua nuova associazione
        </p>
    </div>

    <!-- Association name -->
    <div class="form-group">
        <label class="col-form-label font-weight-bolder">Nome Associazione<b class="text-danger">*</b></label>
        <input
            type="text"
            class="form-control form-control-solid"
            class:is-invalid={errors.associationName}
            bind:value={config.associationName}
            placeholder="A.S.D. Nome Associazione"
        />
        {#if errors.associationName}
            <div class="invalid-feedback">{errors.associationName}</div>
        {/if}
    </div>

    <hr class="my-4" />

    <h5 class="font-weight-bolder mb-3">Account Amministratore</h5>

    <!-- Owner email -->
    <div class="form-group">
        <label class="col-form-label font-weight-bolder">Email<b class="text-danger">*</b></label>
        <input
            type="email"
            class="form-control form-control-solid"
            class:is-invalid={errors.ownerEmail}
            bind:value={config.ownerEmail}
            placeholder="admin@miaassociazione.it"
        />
        {#if errors.ownerEmail}
            <div class="invalid-feedback">{errors.ownerEmail}</div>
        {/if}
        <div class="text-primary align-items-center d-flex font-weight-bold mt-2 font-size-sm">
            <Info size={14} weight="duotone" class="mr-1" />
            Questa email verrà utilizzata per accedere come amministratore
        </div>
    </div>

    <!-- Password -->
    <div class="form-group">
        <label class="col-form-label font-weight-bolder">Password<b class="text-danger">*</b></label>
        <div class="input-group input-group-solid">
            {#if showPassword}
                <input
                    type="text"
                    class="form-control form-control-solid h-auto rounded-lg"
                    class:is-invalid={errors.ownerPassword}
                    bind:value={config.ownerPassword}
                    placeholder="Inserisci una password sicura"
                    autocomplete="off"
                />
            {:else}
                <input
                    type="password"
                    class="form-control form-control-solid h-auto rounded-lg"
                    class:is-invalid={errors.ownerPassword}
                    bind:value={config.ownerPassword}
                    placeholder="Inserisci una password sicura"
                    autocomplete="off"
                />
            {/if}
            <div
                class="input-group-append"
                style="cursor:pointer"
                on:click={() => showPassword = !showPassword}
            >
                <span class="input-group-text">
                    {#if showPassword}
                        <EyeOff size={16} />
                    {:else}
                        <Eye size={16} />
                    {/if}
                </span>
            </div>
        </div>
        {#if errors.ownerPassword}
            <div class="invalid-feedback d-block">{errors.ownerPassword}</div>
        {/if}
        <small class="text-muted font-size-sm mt-2 d-block">
            Minimo 10 caratteri, 1 maiuscola, 1 numero, 1 carattere speciale (!@#$&.-_*)
        </small>
    </div>

    <!-- Confirm password -->
    <div class="form-group">
        <label class="col-form-label font-weight-bolder">Conferma Password<b class="text-danger">*</b></label>
        <div class="input-group input-group-solid">
            {#if showPassword}
                <input
                    type="text"
                    class="form-control form-control-solid h-auto rounded-lg"
                    class:is-invalid={errors.ownerPasswordConfirm}
                    bind:value={config.ownerPasswordConfirm}
                    placeholder="Conferma la password"
                    autocomplete="off"
                />
            {:else}
                <input
                    type="password"
                    class="form-control form-control-solid h-auto rounded-lg"
                    class:is-invalid={errors.ownerPasswordConfirm}
                    bind:value={config.ownerPasswordConfirm}
                    placeholder="Conferma la password"
                    autocomplete="off"
                />
            {/if}
            <div
                class="input-group-append"
                style="cursor:pointer"
                on:click={() => showPassword = !showPassword}
            >
                <span class="input-group-text">
                    {#if showPassword}
                        <EyeOff size={16} />
                    {:else}
                        <Eye size={16} />
                    {/if}
                </span>
            </div>
        </div>
        {#if errors.ownerPasswordConfirm}
            <div class="invalid-feedback d-block">{errors.ownerPasswordConfirm}</div>
        {/if}
    </div>

    <!-- Password strength indicator -->
    {#if config.ownerPassword}
        <div class="password-strength mb-4">
            <div class="d-flex align-items-center mb-2">
                <small class="text-muted mr-2">Sicurezza password:</small>
                {#if config.ownerPassword.length < 10}
                    <span class="badge badge-danger">Debole</span>
                {:else if isValidPassword(config.ownerPassword)}
                    <span class="badge badge-success">Forte</span>
                {:else}
                    <span class="badge badge-warning">Media</span>
                {/if}
            </div>
            <div class="requirements small">
                <div class:text-success={config.ownerPassword.length >= 10} class:text-muted={config.ownerPassword.length < 10}>
                    {#if config.ownerPassword.length >= 10}
                        <Check size={16} class="mr-1" />
                    {:else}
                        <X size={16} class="mr-1" />
                    {/if}
                    Almeno 10 caratteri
                </div>
                <div class:text-success={/[A-Z]/.test(config.ownerPassword)} class:text-muted={!/[A-Z]/.test(config.ownerPassword)}>
                    {#if /[A-Z]/.test(config.ownerPassword)}
                        <Check size={16} class="mr-1" />
                    {:else}
                        <X size={16} class="mr-1" />
                    {/if}
                    Almeno 1 lettera maiuscola
                </div>
                <div class:text-success={/[0-9]/.test(config.ownerPassword)} class:text-muted={!/[0-9]/.test(config.ownerPassword)}>
                    {#if /[0-9]/.test(config.ownerPassword)}
                        <Check size={16} class="mr-1" />
                    {:else}
                        <X size={16} class="mr-1" />
                    {/if}
                    Almeno 1 numero
                </div>
                <div class:text-success={/[!@#$&.\-_*]/.test(config.ownerPassword)} class:text-muted={!/[!@#$&.\-_*]/.test(config.ownerPassword)}>
                    {#if /[!@#$&.\-_*]/.test(config.ownerPassword)}
                        <Check size={16} class="mr-1" />
                    {:else}
                        <X size={16} class="mr-1" />
                    {/if}
                    Almeno 1 carattere speciale
                </div>
            </div>
        </div>
    {/if}

    <!-- Navigation -->
    <div class="d-flex justify-content-between mt-4">
        <button
            type="button"
            class="btn btn-light font-weight-bolder"
            on:click={() => dispatch('prev')}
        >
            <ArrowLeft size={16} class="mr-2" />
            Indietro
        </button>

        <button
            type="button"
            class="btn btn-primary font-weight-bolder"
            on:click={handleNext}
        >
            Continua
            <ArrowRight size={16} class="ml-2" />
        </button>
    </div>
</div>

<style>
    .step-icon {
        width: 70px;
        height: 70px;
        border-radius: 50%;
        background: linear-gradient(135deg, rgba(80, 205, 137, 0.1) 0%, rgba(80, 205, 137, 0.05) 100%);
        display: flex;
        align-items: center;
        justify-content: center;
        color: #50cd89;
    }

    .requirements {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 0.25rem;
    }
</style>
