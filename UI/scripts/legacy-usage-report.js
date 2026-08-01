import {existsSync, readdirSync, readFileSync, statSync} from 'node:fs';
import path from 'node:path';

const root = process.cwd();
const targets = ['src', 'index.html', 'public/offline.html', 'public/error.html'];
const allowedExtensions = new Set(['.svelte', '.js', '.ts', '.html', '.css', '.scss']);
const ignoredDirectories = new Set(['.git', 'dist', 'node_modules', 'android', 'ios', 'build']);

const checks = [
    {name: 'UiApp', pattern: /\bUiApp\b/g},
    {name: 'UiUtil', pattern: /\bUiUtil\b/g},
    {name: 'UiWizard', pattern: /\bUiWizard\b|data-wizard-/g},
    {name: 'jQuery', pattern: /\bjQuery\s*\(|\$\s*\(/g},
    {name: 'FormValidation', pattern: /\bFormValidation\b/g},
    {name: 'Bootstrap data-toggle', pattern: /data-toggle\s*=\s*["']/g},
    {name: 'Input plugins', pattern: /selectpicker|datetimepicker|daterangepicker|datepicker|inputmask|select2/g},
];

function walk(entry) {
    const absolutePath = path.join(root, entry);

    if (!existsSync(absolutePath)) {
        return [];
    }

    const stat = statSync(absolutePath);

    if (stat.isFile()) {
        return allowedExtensions.has(path.extname(absolutePath)) ? [absolutePath] : [];
    }

    if (!stat.isDirectory() || ignoredDirectories.has(path.basename(absolutePath))) {
        return [];
    }

    return readdirSync(absolutePath).flatMap(child => walk(path.join(entry, child)));
}

const files = targets.flatMap(walk);
const rows = checks.map(check => {
    let matches = 0;
    const matchedFiles = new Set();

    files.forEach(file => {
        const content = readFileSync(file, 'utf8');
        const fileMatches = content.match(check.pattern);

        if (fileMatches?.length) {
            matches += fileMatches.length;
            matchedFiles.add(path.relative(root, file));
        }
    });

    return {
        check: check.name,
        matches,
        files: matchedFiles.size,
    };
});

console.table(rows);
