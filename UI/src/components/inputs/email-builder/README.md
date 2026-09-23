# Embedded editor theme bridge

`emailbuilder.js` is the checked-in editor implementation; this repository does not contain a separate editor build project. Its exported `mountEmailBuilder(element)` function connects the Material UI provider to `brandPalette` and returns cleanup. `EmailBuilder.svelte` owns mounting and cleanup.

When replacing/regenerating the bundle, retain this API and the theme subscription at the end of the file. Update the provider without remounting the document tree. The bridge changes interface colors only: authored email JSON, templates, and content colors must remain intact.

The shared CSS palette is maintained in `utils/BrandTheme.js`. Stripe and canvas charts need resolved colors and subscribe to the same store; CSS variables alone cannot update these consumers.
