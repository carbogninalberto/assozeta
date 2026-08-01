<script>
    import {apiFetch} from 'utils/ApiMiddleware';
    import {fade} from 'svelte/transition';
    import * as easing from 'svelte/easing';
    import Portal from 'svelte-portal';
    import {userData} from 'store/stores.js';
    import StarRating from '@ernane/svelte-star-rating';
    import {toast} from 'svelte-sonner';

    userData.useLocalStorage();

    export let id;
    export let showTestimonial = false;

    let testimonialText = '';
    let config = {
        readOnly: false,
        countStars: 5,
        range: {
            min: 0,
            max: 5,
            step: 0.5,
        },
        score: 5,
        showScore: false,
        scoreFormat: function () {
            return `(${this.score.toFixed(0)}/${this.countStars})`;
        },
        name: '',
        starConfig: {
            size: 25,
            fillColor: '#211DE2',
            strokeColor: '#FFF',
            unfilledColor: '#CCC',
            strokeUnfilledColor: '#FFF',
        },
    };

    function setSnoozeTime(snoozeType) {
        const MINUTE = 1000 * 60;
        if (snoozeType === 'long-snooze') {
            $userData.sport_association.reviewed = true;
        }
        return snoozeType === 'long-snooze' ? MINUTE * 60 * 24 : MINUTE * 20;
    }

    function updateShowTestimonial(snoozeType) {
        showTestimonial = false;
        localStorage.setItem('snoozeTestimonial', true);
        const snoozeTime = setSnoozeTime(snoozeType);
    }

    async function sendTestimonial() {
        let res = await apiFetch(__bakney.env.API.TESTIMONIALS.ADD, {
            method: 'POST',
            body: JSON.stringify({
                score: config.score,
                text: testimonialText,
            }),
        });

        showTestimonial = false;

        if (!res.error) {
            $userData.sport_association.reviewed = true;
            localStorage.setItem('snoozeTestimonial', true);
            const snoozeTime = setSnoozeTime('long-snooze');
            toast.success('Abbiamo salvato la tua preziosa recensione, Grazie.');
        } else {
            toast.error('Si è verificato un errore, riprova più tardi.');
        }
    }
</script>

<!-- svelte-ignore missing-declaration -->
<Portal>
    <!-- Modal-->
    <div
        in:fade={{duration: 500, easing: easing.cubicInOut}}
        class="modal fade show"
        {id}
        tabindex="-1"
        role="dialog"
        aria-labelledby="staticBackdrop"
        aria-hidden="true"
        style="display:block;">
        <div class="modal-dialog modal-dialog-centered" role="document">
            <div class="modal-content">
                <img src="/static/banner_testimonial.png" alt="banner testimonial" style="border-radius: 1.25rem;" />
                <div class="modal-header d-flex justify-content-center">
                    <h1>Lasciaci una recensione</h1>
                </div>
                <div class="modal-body py-2" style="text-align: center;padding-left: 4rem; padding-right: 4rem;">
                    <p class="text-align-center">
                        Aiutaci a far crescere {__bakney.OEM_CONFIG?.name}, lascia una breve recensione, ti basteranno
                        2 minuti. Grazie per il tuo supporto! 🙏
                    </p>
                    <div class="my-6">
                        <StarRating bind:config /> ({config.score}/5)
                    </div>
                    <div>
                        <textarea
                            rows="5"
                            bind:value={testimonialText}
                            class="form-control form-control-solid form-control-lg"
                            placeholder="Scrivi qui una breve recensione... Cosa ne pensi di {__bakney.OEM_CONFIG
                                ?.name}? 😊" />
                    </div>
                </div>
                <div class="modal-footer d-flex justify-content-center">
                    <button class="btn btn-ghost" style="text-decoration: underline;" on:click={updateShowTestimonial}
                        >Forse dopo</button>
                    <button
                        disabled={!testimonialText || !config.score}
                        class="btn btn-primary font-weight-boldest"
                        on:click={() => sendTestimonial()}
                        >Lascia una recensione
                    </button>
                </div>
            </div>
        </div>
    </div>
</Portal>
