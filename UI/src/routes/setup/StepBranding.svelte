<script>
	import { ArrowLeft, Building, Check, Info as LucideInfo, X } from 'lucide-svelte';
    import {createEventDispatcher} from 'svelte';
    import {Palette, Image, CaretDown, CaretUp, Info} from 'phosphor-svelte';

    export let config = {
        name: '',
        abbreviation: '',
        primaryColor: '#351DC2',
        supportEmail: '',
        logoFile: null,
        logoPreview: null
    };

    export let oauthConfig = {
        googleClientId: '',
        appleClientId: ''
    };

    export let loading = false;

    const dispatch = createEventDispatcher();

    let errors = {};
    let showAdvanced = false;

    function handleLogoSelect(e) {
        const file = e.target.files[0];
        if (file) {
            // Validate file type
            const validTypes = ['image/png', 'image/jpeg', 'image/svg+xml'];
            if (!validTypes.includes(file.type)) {
                errors.logo = 'Formato non supportato. Usa PNG, JPG o SVG';
                return;
            }

            // Validate file size (max 2MB)
            if (file.size > 2 * 1024 * 1024) {
                errors.logo = 'Il file è troppo grande. Massimo 2MB';
                return;
            }

            config.logoFile = file;
            errors.logo = null;

            // Create preview
            const reader = new FileReader();
            reader.onload = (e) => {
                config.logoPreview = e.target.result;
            };
            reader.readAsDataURL(file);
        }
    }

    function removeLogo() {
        config.logoFile = null;
        config.logoPreview = null;
    }

    function validate() {
        errors = {};

        if (!config.name || config.name.trim() === '') {
            errors.name = 'Il nome è obbligatorio';
        }

        if (config.supportEmail && !isValidEmail(config.supportEmail)) {
            errors.supportEmail = 'Inserisci un\'email valida';
        }

        return Object.keys(errors).length === 0;
    }

    function isValidEmail(email) {
        return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
    }

    function handleSubmit() {
        if (validate()) {
            dispatch('submit');
        }
    }

    // Preset colors
    const presetColors = [
        '#351DC2', // Assozeta purple
        '#3699FF', // Blue
        '#50CD89', // Green
        '#FFC107', // Yellow
        '#F1416C', // Red
        '#7239EA', // Purple
        '#181C32', // Dark
        '#009EF7', // Light blue
    ];
</script>

<div class="step-branding">
    <div class="text-center mb-5">
        <div class="step-icon mx-auto mb-4">
            <Palette size={32} weight="duotone" />
        </div>
        <h2 class="font-weight-bolder mb-2">Personalizza il Branding</h2>
        <p class="text-muted font-size-sm">
            Configura l'aspetto della tua istanza
        </p>
    </div>

    <!-- Logo upload -->
    <div class="form-group">
        <label class="col-form-label font-weight-bolder">Logo</label>

        {#if config.logoPreview}
            <div class="logo-preview d-flex align-items-center p-3 bg-light rounded-lg">
                <div class="preview-image mr-3" style="background-color: {config.primaryColor}20;">
                    <img src={config.logoPreview} alt="Logo preview" />
                </div>
                <div class="flex-grow-1">
                    <p class="mb-0 font-weight-bolder">{config.logoFile?.name}</p>
                    <small class="text-muted font-size-sm">{(config.logoFile?.size / 1024).toFixed(1)} KB</small>
                </div>
                <button type="button" class="btn btn-icon btn-light" on:click={removeLogo}>
                    <X size={16} />
                </button>
            </div>
        {:else}
            <label class="logo-upload d-flex flex-column align-items-center justify-content-center p-4 rounded-lg cursor-pointer">
                <Image size={32} weight="duotone" class="text-muted mb-2" />
                <span class="text-muted font-weight-bold">Clicca per caricare il logo</span>
                <small class="text-muted font-size-sm">PNG, JPG o SVG (max 2MB)</small>
                <input type="file" accept=".png,.jpg,.jpeg,.svg" class="d-none" on:change={handleLogoSelect} />
            </label>
        {/if}
        {#if errors.logo}
            <div class="invalid-feedback d-block">{errors.logo}</div>
        {/if}
    </div>

    <!-- Instance name -->
    <div class="form-group">
        <label class="col-form-label font-weight-bolder">Nome Istanza<b class="text-danger">*</b></label>
        <input
            type="text"
            class="form-control form-control-solid"
            class:is-invalid={errors.name}
            bind:value={config.name}
            placeholder="La Mia Associazione"
        />
        {#if errors.name}
            <div class="invalid-feedback">{errors.name}</div>
        {/if}
        <div class="text-primary align-items-center d-flex font-weight-bold mt-2 font-size-sm">
            <LucideInfo size={14} weight="duotone" class="mr-1" />
            Questo nome verrà mostrato nell'interfaccia e nei documenti
        </div>
    </div>

    <!-- Abbreviation -->
    <div class="form-group">
        <label class="col-form-label font-weight-bolder">Abbreviazione (opzionale)</label>
        <input
            type="text"
            class="form-control form-control-solid"
            bind:value={config.abbreviation}
            placeholder="MIA"
            maxlength="10"
        />
        <small class="text-muted font-size-sm mt-2 d-block">
            Versione corta del nome (max 10 caratteri)
        </small>
    </div>

    <!-- Primary color -->
    <div class="form-group">
        <label class="col-form-label font-weight-bolder">Colore Principale</label>
        <div class="color-picker d-flex align-items-center flex-wrap">
            <div class="color-presets d-flex mr-3 mb-2 mb-sm-0">
                {#each presetColors as color}
                    <button
                        type="button"
                        class="color-preset"
                        class:active={config.primaryColor === color}
                        style="background-color: {color};"
                        on:click={() => config.primaryColor = color}
                    ></button>
                {/each}
            </div>
            <div class="custom-color d-flex align-items-center">
                <input
                    type="color"
                    class="color-input"
                    bind:value={config.primaryColor}
                />
                <input
                    type="text"
                    class="form-control form-control-solid form-control-sm ml-2"
                    style="width: 90px;"
                    bind:value={config.primaryColor}
                    pattern="^#[0-9A-Fa-f]{6}$"
                />
            </div>
        </div>
    </div>

    <!-- Support email -->
    <div class="form-group">
        <label class="col-form-label font-weight-bolder">Email Supporto (opzionale)</label>
        <input
            type="email"
            class="form-control form-control-solid"
            class:is-invalid={errors.supportEmail}
            bind:value={config.supportEmail}
            placeholder="supporto@miaassociazione.it"
        />
        {#if errors.supportEmail}
            <div class="invalid-feedback">{errors.supportEmail}</div>
        {/if}
    </div>

    <!-- Advanced options toggle -->
    <button
        type="button"
        class="btn btn-link p-0 mb-3 font-weight-bolder d-flex align-items-center"
        on:click={() => showAdvanced = !showAdvanced}
    >
        {#if showAdvanced}
            <CaretUp size={16} weight="bold" class="mr-2" />
        {:else}
            <CaretDown size={16} weight="bold" class="mr-2" />
        {/if}
        Opzioni Avanzate
    </button>

    {#if showAdvanced}
        <div class="advanced-options bg-light rounded-lg p-4 mb-4">
            <h6 class="font-weight-bolder mb-3">Configurazione OAuth (opzionale)</h6>
            <p class="text-muted font-size-sm mb-3">
                Configura l'accesso con Google/Apple. Se non configurato, sarà disponibile solo l'accesso con email e password.
            </p>

            <div class="form-group">
                <label class="col-form-label font-weight-bold font-size-sm">Google Client ID</label>
                <input
                    type="text"
                    class="form-control form-control-solid form-control-sm"
                    bind:value={oauthConfig.googleClientId}
                    placeholder="xxxxx.apps.googleusercontent.com"
                />
            </div>

            <div class="form-group mb-0">
                <label class="col-form-label font-weight-bold font-size-sm">Apple Client ID</label>
                <input
                    type="text"
                    class="form-control form-control-solid form-control-sm"
                    bind:value={oauthConfig.appleClientId}
                    placeholder="com.example.app"
                />
            </div>
        </div>
    {/if}

    <!-- Preview -->
    <div class="preview-card rounded-lg p-4 mb-4" style="border-left: 4px solid {config.primaryColor};">
        <h6 class="font-weight-bolder mb-3">Anteprima</h6>
        <div class="d-flex align-items-center">
            {#if config.logoPreview}
                <img src={config.logoPreview} alt="Logo" class="preview-logo mr-3" />
            {:else}
                <div class="preview-logo-placeholder mr-3" style="background-color: {config.primaryColor}20;">
                    <Building size={16} style="color: {config.primaryColor};" />
                </div>
            {/if}
            <div>
                <h5 class="mb-0 font-weight-bolder" style="color: {config.primaryColor};">{config.name || 'Nome Istanza'}</h5>
                {#if config.abbreviation}
                    <small class="text-muted font-size-sm">({config.abbreviation})</small>
                {/if}
            </div>
        </div>
    </div>

    <!-- Navigation -->
    <div class="d-flex justify-content-between mt-4">
        <button
            type="button"
            class="btn btn-light font-weight-bolder"
            on:click={() => dispatch('prev')}
            disabled={loading}
        >
            <ArrowLeft size={16} class="mr-2" />
            Indietro
        </button>

        <button
            type="button"
            class="btn btn-primary font-weight-bolder"
            disabled={loading}
            on:click={handleSubmit}
        >
            {#if loading}
                <span class="spinner-border spinner-border-sm mr-2"></span>
                Configurazione in corso...
            {:else}
                Completa Configurazione
                <Check size={16} class="ml-2" />
            {/if}
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

    .logo-upload {
        border: 2px dashed #e4e6ef;
        cursor: pointer;
        transition: all 0.2s;
    }

    .logo-upload:hover {
        border-color: var(--main-color, #351DC2);
        background-color: rgba(53, 29, 194, 0.02);
    }

    .logo-preview {
        border: 1px solid #e4e6ef;
    }

    .preview-image {
        width: 60px;
        height: 60px;
        border-radius: 0.5rem;
        display: flex;
        align-items: center;
        justify-content: center;
        overflow: hidden;
    }

    .preview-image img {
        max-width: 100%;
        max-height: 100%;
        object-fit: contain;
    }

    .color-presets {
        gap: 0.5rem;
    }

    .color-preset {
        width: 28px;
        height: 28px;
        border-radius: 50%;
        border: 2px solid transparent;
        cursor: pointer;
        transition: all 0.2s;
    }

    .color-preset:hover {
        transform: scale(1.1);
    }

    .color-preset.active {
        border-color: #181C32;
        box-shadow: 0 0 0 2px white, 0 0 0 4px currentColor;
    }

    .color-input {
        width: 36px;
        height: 36px;
        border: none;
        border-radius: 0.25rem;
        cursor: pointer;
        padding: 0;
    }

    .preview-card {
        background: var(--bg-surface-secondary);
    }

    .preview-logo {
        width: 50px;
        height: 50px;
        object-fit: contain;
    }

    .preview-logo-placeholder {
        width: 50px;
        height: 50px;
        border-radius: 0.5rem;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 1.5rem;
    }
</style>
