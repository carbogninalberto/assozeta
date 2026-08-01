<script>
    import {onMount} from 'svelte';
    import {scale} from 'svelte/transition';
    import * as easing from 'svelte/easing';
    import {userData} from 'store/stores.js';

    userData.useLocalStorage();

    let couponCode = '';

    onMount(() => {
        couponCode = $userData.sport_association.affiliate_code || 'n.d.😢';
    });
</script>

<div  class="d-flex flex-column-fluid">
    <!--begin::Container-->
    <div class="container">
        <img src="/static/affiliate_2.jpg" style="top:0; width:100%;" alt="programma di affiliazione" />
        <div class="p-8 bg-white">
            <div class="text-center">
                <h1 class="mb-4 font-weight-boldest" style="font-size:2.5rem;">Cos'è?</h1>
                <p class="font-size-lg" style="font-size: 1.25rem;">
                    Il programma di affiliazione ti permette di gudagnare denaro col <b class="font-weight-boldest"
                        >passaparola</b
                    >. <br />
                    Parlando di <strong class="font-weight-boldest">{__bakney.OEM_CONFIG?.name}</strong> ad altre
                    associazioni ottieni dei benefici.
                    <br />
                </p>
            </div>
            <div class="font-size-lg text-center mb-8 mt-8" style="font-size: 1.25rem;">
                <h2 class="my-4 font-weight-boldest" style="font-size: 2.5rem">Benefici</h2>
                <p>
                    Per <b class="font-weight-boldest">ogni associazione</b> che
                    <b class="font-weight-boldest">attiva un piano a pagamento</b>
                    inserendo il tuo codice
                    <kbd>{couponCode}</kbd> ottieni un credito* di:
                </p>
                <div
                    style="text-align: left; margin-left: 2rem; width: 25rem;margin:3rem auto; border-radius: 0.55rem; padding: 1rem 2rem; background: var(--bg-surface-secondary); line-height: 3.25rem;">
                    <div class="m-auto d-flex align-items-center font-weight-boldest text-primary">
                        <!-- <CheckCircle size={22} class="text-primary" weight="duotone" /> -->

                        <strong class="font-weight-boldest text-primary mr-4" style="font-weight:800;font-size:3rem"
                            >50€
                        </strong> per ogni associazione
                    </div>
                </div>
                <p class="pt-0">
                    L'associazione che utlizza il tuo codice al momento dell' <b class="font-weight-boldest"
                        >acquisto</b>
                    ottiene uno
                    <b class="font-weight-boldest">sconto del 10%</b> sul primo ordine.
                </p>
            </div>

            <div class="font-size-lg text-center mb-8 mt-12" style="font-size: 1.25rem;">
                <hr style="max-width:40rem;" />
                <div class="m-auto p-8">
                    <h2 class="mb-2 font-weight-boldest text-dark" style="font-size: 2.5rem;">Condividi il coupon</h2>
                    <kbd class="bg-dark" style="font-size: 4rem;">{couponCode}</kbd>
                </div>
                <hr style="max-width:40rem;" />
                <div style="font-size: .8rem;max-width: 40rem;" class="m-auto pt-8">
                    * L'associazione dovrà inserire il codice del coupon al momento del checkout, e in 1-3 giorni
                    lavorativi dal pagamento riceverai a tua volta un coupon del valore indicato da riscuotere entro 6
                    mesi.

                    <h4 class="mb-4 mt-12 font-weight-boldest">Domande frequenti</h4>
                    <h6 class="text-dark mt-6">Se porto 2 associazioni ottengo 100€ di credito?</h6>
                    <p>
                        Sì, per ogni associazione che attiva un piano a pagamento inserendo il tuo codice ottieni un
                        credito di 50€.
                    </p>
                    <h6 class="text-dark mt-6">Posso utilizzare il credito per acquistare un piano?</h6>
                    <p>Sì, il credito è utilizzabile solamente per acquistare un piano, e verrà scalato dal totale.</p>
                    <h6 class="text-dark mt-6">Cosa significa che ho 6 mesi per riscuotere il credito?</h6>
                    <p>
                        Significa che il credito è utilizzabile entro 6 mesi dalla data di emissione, dopo i quali
                        scadrà.
                    </p>
                </div>
            </div>
        </div>
    </div>
</div>
