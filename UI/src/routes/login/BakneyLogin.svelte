<script>
    import {onMount} from 'svelte';
    import {originalFetch} from 'utils/ApiMiddleware.js';
    import {initializeLoginSession, reloadAppAt} from 'utils/loginSession.js';

    const endpoint = '/bakney/v1/session';
    const messages = {
        account_unavailable: 'Il tuo account non è presente o non è attivo in questa istanza. Contatta l’associazione.',
        account_not_eligible: 'Questo account non può usare il trasferimento automatico. Puoi accedere direttamente.',
        membership_required: 'Il tuo account non risulta collegato a questa associazione. Contatta l’associazione.',
        bakney_unavailable: 'Bakney non è raggiungibile. Riprova più tardi o accedi direttamente.',
        forwarding_disabled: 'Il trasferimento automatico è disabilitato. Puoi accedere direttamente.',
        configuration_required: 'Il collegamento con Bakney deve essere configurato dall’amministratore.',
        revalidation_required: 'Il collegamento con Bakney deve essere verificato di nuovo dall’amministratore.',
        invalid_pairing: 'Il collegamento con Bakney non è attivo. Contatta l’associazione.',
        pairing_rejected: 'Il collegamento con Bakney non è più valido. Contatta l’associazione.',
        account_switch_required: 'La sessione è cambiata in un’altra scheda. Ricarica questa pagina per confermare l’account.',
    };
    let pending;
    let busy = true;
    let error = '';
    let switchRequired = false;
    function stored(key) {
        try { return JSON.parse(localStorage.getItem(key)); } catch { return null; }
    }
    const hadSession = !!stored('sessionToken');

    async function request(method = 'GET', body) {
        const headers = {'Accept': 'application/json', 'Content-Type': 'application/json'};
        const token = stored('sessionToken');
        if (token) headers.Authorization = `Bearer ${token}`;
        if (pending) headers['X-SSO-CSRF'] = pending.csrf_token;
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 20000);
        try {
            const response = await originalFetch(endpoint, {
                method, headers, credentials: 'same-origin', cache: 'no-store',
                body: body ? JSON.stringify(body) : undefined, signal: controller.signal,
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'invalid_handoff');
            return data;
        } finally { clearTimeout(timeout); }
    }

    function showError(code) {
        error = messages[code] || 'Il collegamento di accesso è scaduto o non è valido. Avvia un nuovo trasferimento da Bakney o accedi direttamente.';
        busy = false;
    }

    async function complete() {
        busy = true;
        try {
            const response = await request('POST', {confirm_for: pending.confirm_for});
            initializeLoginSession(response, true);
            reloadAppAt('/');
        } catch (e) { showError(e.message); }
    }

    onMount(async () => {
        const query = window.location.hash.split('?')[1] || '';
        const code = new URLSearchParams(query).get('error');
        history.replaceState(null, '', '/#/bakney-login');
        if (code) { showError(code); return; }
        try {
            pending = await request();
            const previous = stored('userData')?.user_id;
            switchRequired = pending.confirm_for.length > 0 || (previous && previous !== pending.user.id);
            if (!switchRequired) await complete();
            else busy = false;
        } catch (e) { showError(e.message); }
    });
</script>

<main class="bakney-login">
    <section class="card p-6" aria-labelledby="bakney-login-title">
        <h1 id="bakney-login-title">Accesso da Bakney</h1>
        {#if error}<p role="alert">{error}</p>
        {:else if busy}<p role="status">Accesso in corso…</p>
        {:else if switchRequired}
            <p>Hai già una sessione su questa istanza. Vuoi continuare come <strong>{pending.user.name}</strong>?</p>
            <p>Le informazioni della sessione precedente verranno rimosse da questo browser.</p>
            <button class="btn btn-primary mb-3" on:click={complete}>Conferma cambio account</button>
        {/if}
        {#if !busy}
            <a class="btn btn-light" href={hadSession ? '/' : '/#/login'} on:click|preventDefault={() => reloadAppAt(hadSession ? '/' : '/#/login')}>{hadSession ? 'Mantieni la sessione attuale' : 'Accedi direttamente'}</a>
        {/if}
    </section>
</main>

<style>
    .bakney-login {min-height: 100vh; display: grid; place-items: center; padding: 1.5rem; background: #f4f6f9;}
    section {max-width: 34rem; width: 100%;}
    h1 {font-size: 1.6rem;}
</style>
