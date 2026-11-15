<script lang="ts">
	import '../app.css';

	let { children } = $props();
</script>

<svelte:head>
	<link rel="icon" href="/favicon.svg" type="image/svg+xml" />
	<link rel="apple-touch-icon" href="/favicon.svg" />
	<meta name="theme-color" content="#ffffff" />
	<meta name="color-scheme" content="light dark" />
	<link rel="manifest" href="/manifest.json" />
</svelte:head>

{@render children()}

<!--
	No cookie banner: the site stores only the theme choice (`assozeta-theme`),
	a technical preference, and no analytics or marketing cookies.
-->
