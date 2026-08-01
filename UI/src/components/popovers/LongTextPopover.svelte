<script>
    import { convert } from 'html-to-text';
    import {onMount} from "svelte";
    import { initTooltips, destroyTooltips } from 'shim/tooltip.js';

    export let text = '';
    export let lenSubstring = 50;

    let convertedText;
    let truncatedText;
    let showEllipsis;

    $: convertedText = convert(text.replace(/&lt;/g, '<').replace(/&gt;/g, '>'));
    $: truncatedText = convertedText.substring(0, lenSubstring);
    $: showEllipsis = text.length > lenSubstring;

    onMount(() => {
        initTooltips(document.body);
    });
</script>


<span
    style="word-break: keep-all; { showEllipsis ? 'cursor: pointer;' : ''}"
    data-toggle="tooltip"
    data-container="body"
    data-placement="left"
    title={convertedText}
>
    {truncatedText}{showEllipsis ? '...' : ''}
</span>