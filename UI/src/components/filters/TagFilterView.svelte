<script>
    import {onMount} from 'svelte';
    import MobileFilterSheet from './MobileFilterSheet.svelte';

    function resetTags() { tags = tags.map(tag => ({...tag, active: true})); }

    export let tags = [];
    export let activeTags = [];

    $: tags, (activeTags = [...tags.filter(t => t.active).map(t => t.tag_name)]);

    // when user clic on a tag is active
    const activateTag = tag_name => {
        tags = [
            ...tags?.map(t => {
                if (t.tag_name === tag_name) {
                    t.active = !t.active;
                }
                return t;
            }),
        ];
    };

    onMount(() => {
        // set all tags to active
        tags = [
            ...tags?.map(tag => {
                return {
                    ...tag,
                    active: true,
                };
            }),
        ];
    });
</script>

<div class="datatable-filters d-flex flex-wrap align-items-center justify-content-start w-100 mb-4 px-4">
    <MobileFilterSheet onReset={resetTags}>
    <span class="font-weight-boldest font-size-lg my-auto mr-2"> Filtra corso per tag </span>
    <div class="d-flex flex-wrap align-items-center" style="gap: 0.5rem;">
        {#each tags as tag}
            <button type="button" aria-pressed={tag.active} style="min-height:44px"
                class="m-0 font-weight-bolder btn btn-sm py-1 px-3 {tag.active ? 'btn-dark' : 'btn-light'} mx-1"
                on:click={() => activateTag(tag.tag_name)}>
                {tag.tag_name}
            </button>
        {/each}
    </div>
    </MobileFilterSheet>
</div>
