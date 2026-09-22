# Filter component browser regressions

These scripts compile the actual Svelte components and run them in Chromium. They do not need a running application, credentials, or seeded data. The table test supplies a deterministic HTTP adapter to exercise delayed responses.

Install the existing frontend and browser-project dependencies, then Chromium:

```sh
npm --prefix UI ci
npm --prefix selfhost/tests/browser ci
cd selfhost/tests/browser
npx playwright install chromium
```

Run from the repository root:

```sh
node selfhost/tests/browser/scripts/table-search.mjs
node selfhost/tests/browser/scripts/query-filters.mjs
node selfhost/tests/browser/scripts/filter-overlays.mjs
node selfhost/tests/browser/scripts/filter-selects.mjs
```

- `table-search`: stale response rejection, pending debounce cancellation, visible search clearing, and independent IDs/results for two tables.
- `query-filters`: isolated drafts, cancel/apply/removal, valid age zero, invalid ranges, keyboard dismissal and independent radio groups on mobile and desktop.
- `filter-selects`: visible selected labels and typed values (including numeric zero and boolean false), rebuilt options, reset, and independent dropdown instances at mobile, landscape, tablet, and desktop widths.
- `filter-overlays`: nested detail/filter/date drawers, Escape order, focus return, shared body scroll locks, long select lists, landscape bounds, responsive resizing and a simulated software-keyboard viewport.

These component checks complement real application checks. They do not validate backend permissions or replace physical-device keyboard testing.
