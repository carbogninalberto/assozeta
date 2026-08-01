<script>
    import {createEventDispatcher} from 'svelte';

    const dispatch = createEventDispatcher();

    let pinCode = '';
    let maxLength = 4;
    export let value = '';
    export let confirmText = 'Check in';
    export let resetText = 'Reset';

    function addDigit(digit) {
        if (pinCode.length < maxLength) {
            pinCode += digit;
            value = pinCode;
        }
    }

    function resetPin() {
        pinCode = '';
        value = '';
    }

    function confirmPin() {
        if (pinCode.length === maxLength) {
            dispatch('confirm', pinCode);
            resetPin();
        }
    }
</script>

<div class="pin-pad">
    <div class="pin-display">{pinCode.padEnd(maxLength, '•')}</div>
    <div class="pin-buttons">
        {#each Array(9) as _, i}
            <button class="rounded-lg" on:click={() => addDigit(i + 1)}>{i + 1}</button>
        {/each}
        <button class="rounded-lg reset" on:click={resetPin}>{resetText}</button>
        <button class="rounded-lg" on:click={() => addDigit(0)}>0</button>
        <button class="rounded-lg confirm" on:click={confirmPin}>{confirmText}</button>
    </div>
</div>

<style>
    .pin-pad {
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 1.5rem;
        border: 1px solid var(--border-color);
        width: fit-content;
        padding: 3.5rem;
        border-radius: 1rem;
        background-color: var(--bg-surface);
    }

    .pin-display {
        margin-bottom: 2rem;
        font-size: 2rem;
        letter-spacing: 0.5rem;
        font-weight: 900;
        background-color: var(--bg-input);
        border: 2px solid var(--border-color);
        border-radius: 8px;
        padding: 0.5rem 1rem;
        color: var(--text-primary);
        font-family: 'Courier New', monospace;
        text-align: center;
        min-width: 200px;
    }

    .pin-buttons {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 1.5rem;
    }

    button {
        padding: 1rem;
        font-size: 1.2rem;
        border-top: 1px solid var(--bg-surface);
        outline: 1px solid var(--border-color) !important;
        background-color: var(--bg-surface-secondary);
        background-image: linear-gradient(to right, var(--bg-surface), var(--bg-surface-secondary), var(--bg-surface));
        cursor: pointer;
        font-weight: 900;
        color: var(--text-primary);
    }
    button:hover {
        background-color: var(--bg-hover);
        transition: background-color 0.3s ease;
    }

    button:active {
        background-color: #9896c8;
        transform: scale(0.95);
        transition: background-color 0.1s ease, transform 0.1s ease;
    }
    button.reset {
        background-color: var(--danger);
        border: 0px solid var(--danger);
        border-top: 1px solid rgba(255, 255, 255, 0.5);
        color: var(--white);
        background-image: none;
        outline: 1px solid var(--danger) !important;
    }

    button.confirm {
        background-color: var(--primary);
        border: 0px solid var(--primary);
        border-top: 1px solid rgba(255, 255, 255, 0.5);
        color: var(--white);
        background-image: none;
        outline: 1px solid var(--primary) !important;
    }

    @keyframes clickAnimation {
        0% {
            transform: scale(1);
        }
        50% {
            transform: scale(0.95);
        }
        100% {
            transform: scale(1);
        }
    }

    button:active {
        animation: clickAnimation 0.2s ease;
    }
</style>
