#!/usr/bin/env node
/**
 * CSS safety verifier for the open-source release cleanup.
 *
 * Collects class names referenced by the app:
 *   - class="..." attributes and Svelte class: directives in src/**\/*.svelte,
 *     index.html and public/*.html
 *   - string literals in src/**\/*.js and src/**\/*.svelte (covers
 *     classList.add(...), className = '...', HTML-in-template-literals)
 *   - a hardcoded list of runtime classes created by src/shim/** with
 *     dynamic suffixes (bs-tooltip-*, bkn-spinner--*, ...)
 *
 * Then checks each referenced class against the shipped CSS stack:
 *   bootstrap.min.css, app-bundle.css, event-calendar.min.css,
 *   global.css, dark-mode.css
 *
 * If the legacy bundles are still present in git history/worktree, pass
 * `--legacy <path>...` to also classify missing classes as regressions
 * (present in legacy CSS but not in the new stack) vs never-styled classes.
 *
 * Exit code 1 if regressions are found, 0 otherwise.
 */
import { readFileSync, existsSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { globSync } from 'glob';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');

const CONTENT_GLOBS = [
    'index.html',
    'public/*.html',
    'src/**/*.svelte',
    'src/**/*.js',
];

const NEW_CSS = [
    'public/static/css/bootstrap.min.css',
    'public/static/css/app-bundle.css',
    'public/static/assets/plugins/event-calendar/event-calendar.min.css',
    'public/global.css',
    'public/dark-mode.css',
];

// Classes created at runtime with dynamic parts (expanded manually).
const RUNTIME_CLASSES = [
    // shim/tooltip.js + shim/popover.js
    ...['top', 'bottom', 'left', 'right', 'auto'].flatMap((p) => [
        `bs-tooltip-${p}`,
        `bs-popover-${p}`,
    ]),
    'tooltip', 'popover', 'arrow', 'tooltip-inner', 'popover-body', 'popover-header',
    // shim/modal.js
    'modal-backdrop', 'modal-open', 'show', 'fade',
    // shim/ui.js
    'bkn-blockui-overlay', 'bkn-popover-shim', 'bkn-spinner', 'bkn-spinner--v2',
    ...['primary', 'secondary', 'success', 'info', 'warning', 'danger', 'light', 'dark'].map(
        (c) => `bkn-spinner--${c}`
    ),
    'spinner', 'spinner-sm',
    // shim/select.js
    'selectpicker-shim', 'select-shim-wrapper', 'select-shim-filter',
    // shim/dropzone.js
    'dropzone-shim', 'dropzone-shim-dragover',
    // shim/form-validation.js
    'fv-plugins-message-container', 'fv-plugins-message-container--enabled',
    'fv-help-block', 'is-valid', 'is-invalid', 'invalid-feedback',
    // shim/collapse.js, shim/alert.js, shim/dropdown.js
    'collapsed', 'collapsing',
];

// Known false positives, verified manually during the CSS extraction:
// - fc-event-* / fc-description: legacy FullCalendar names; the calendar
//   components map them to ec-event-* classes styled in public/global.css.
// - the bare single words below come from generic JS string literals
//   (e.g. 'primary' passed as a color parameter, 'token' in auth code) and
//   are never used as standalone classes in markup.
const IGNORED_CLASSES = new Set([
    ...[
        'primary', 'secondary', 'success', 'info', 'warning', 'danger',
        'light', 'dark', 'white',
        'light-primary', 'light-success', 'light-info', 'light-warning',
        'light-danger', 'light-dark',
    ],
    ...[
        'available', 'category', 'child', 'date', 'fullscreen', 'group',
        'in-range', 'ins', 'left', 'locked', 'ltr', 'off', 'operator',
        'rtl', 'single', 'span', 'svg', 'token', 'value', 'week',
    ],
    'fc-description',
    ...['primary', 'success', 'danger', 'warning', 'dark', 'light', 'white'].flatMap(
        (c) => [`fc-event-${c}`, `fc-event-solid-${c}`]
    ),
    // Tokens that only matched excluded vendor CSS after the corresponding
    // plugin JS was removed (bootstrap-timepicker/datetimepicker,
    // daterangepicker, summernote, bootstrap-select, Font Awesome, and
    // FormValidation framework adapters).
    // `.dragover` is styled by component-scoped CSS in Archive.svelte.
    ...[
        'open', 'checked', 'error', 'input', 'textarea', 'field', 'fields',
        'cell', 'column', 'second', 'top', 'bottom', 'right', 'hover',
        'dragover', 'selectpicker', 'fa', 'fab', 'far',
        'la', 'las', 'lab', 'lar',
    ],
]);

function collectUsedClasses() {
    const used = new Set(RUNTIME_CLASSES);
    const files = CONTENT_GLOBS.flatMap((g) => globSync(g, { cwd: root, absolute: true }));
    const classToken = /^-?[A-Za-z][A-Za-z0-9_-]*$/;

    for (const file of files) {
        const text = readFileSync(file, 'utf8');

        // class="..." / class='...' attributes (static portions only)
        for (const m of text.matchAll(/class\s*=\s*(["'])([\s\S]*?)\1/g)) {
            for (const tok of m[2].split(/[\s{}$]+/)) {
                if (classToken.test(tok)) used.add(tok);
            }
        }
        // Svelte class:name directives
        for (const m of text.matchAll(/class:([A-Za-z0-9_-]+)/g)) {
            used.add(m[1]);
        }
        // Quoted string literals (classList.add('x'), ternaries in class={...},
        // className = '...', HTML in template literals)
        for (const m of text.matchAll(/(["'`])((?:(?!\1)[^\\\n]|\\.){1,200})\1/g)) {
            const s = m[2];
            // Skip strings that clearly aren't class lists
            if (/[:;()<>\/\\@]|\$\{/.test(s)) continue;
            for (const tok of s.split(/\s+/)) {
                if (classToken.test(tok)) used.add(tok);
            }
        }
    }
    return used;
}

function cssHasClass(cssText, cls) {
    const esc = cls.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    return new RegExp(`\\.${esc}(?![\\w-])`).test(cssText);
}

function main() {
    const legacyArgIdx = process.argv.indexOf('--legacy');
    const legacyPaths = legacyArgIdx > -1 ? process.argv.slice(legacyArgIdx + 1) : [];

    // Strip comments so that URLs/text inside them (e.g. apache.org,
    // animate.style) are not mistaken for class selectors.
    const readCss = (p) =>
        readFileSync(resolve(root, p), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
    const newCss = NEW_CSS.filter((p) => existsSync(resolve(root, p)))
        .map(readCss)
        .join('\n');
    const legacyCss = legacyPaths
        .filter((p) => existsSync(resolve(root, p)))
        .map(readCss)
        .join('\n');

    const used = collectUsedClasses();
    console.log(`Collected ${used.size} candidate class names from sources.`);

    const missing = [];
    for (const cls of [...used].sort()) {
        if (IGNORED_CLASSES.has(cls)) continue;
        if (!cssHasClass(newCss, cls)) missing.push(cls);
    }

    if (legacyCss) {
        const regressions = missing.filter((c) => cssHasClass(legacyCss, c));
        const neverStyled = missing.length - regressions.length;
        console.log(`${neverStyled} referenced classes have no styles (also unstyled in legacy CSS).`);
        if (regressions.length) {
            console.error(`\nREGRESSIONS (${regressions.length}) - styled in legacy CSS, missing from new stack:`);
            for (const c of regressions) console.error('  .' + c);
            process.exit(1);
        }
        console.log('No regressions: every class styled by the legacy CSS is covered by the new stack.');
    } else {
        console.log(`${missing.length} referenced classes not found in the shipped CSS stack`);
        console.log('(no --legacy baseline given: cannot classify regressions vs never-styled).');
        const interesting = missing.filter((c) =>
            /^(menu|aside|header|topbar|subheader|brand|wizard|symbol|label|btn|badge|card|form|input|nav|tab|modal|dropdown|tooltip|popover|alert|fv-|bkn-|swal2|ec-|ps|dropzone|select)/.test(c)
        );
        if (interesting.length) {
            console.log('\nPotentially interesting (framework-looking) classes without styles:');
            for (const c of interesting) console.log('  .' + c);
        }
    }
}

main();
