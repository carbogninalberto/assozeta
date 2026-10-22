// Pure MDX compilation and local references. Never evaluates authored JavaScript.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {compile} from '@mdx-js/mdx';

const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const slug = value => value.normalize('NFKD').replace(/[^\x00-\x7F]/g, '')
    .toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'introduzione';
const body = value => value.replace(/^---\s*\n[\s\S]*?\n---\s*\n/, '');

function owned(root, relative) {
    const file = path.resolve(root, relative);
    if (path.isAbsolute(relative) || !file.startsWith(root + path.sep)
        || !fs.realpathSync(file).startsWith(fs.realpathSync(root) + path.sep))
        throw new Error('Manual reference escapes its source: ' + relative);
    return file;
}

function navigation(items) {
    return items.flatMap(item => typeof item === 'string' ? [item] : navigation(item.pages || []));
}

export async function compileManual(manual, output) {
    manual = path.resolve(manual);
    output = path.resolve(output);
    const pages = navigation(JSON.parse(fs.readFileSync(owned(manual, 'mint.json'))).navigation);
    if (!pages.length || new Set(pages).size !== pages.length) throw new Error('Empty or duplicate manual navigation');
    const sources = new Map(pages.map(page => [page, fs.readFileSync(owned(manual, page + '.mdx'), 'utf8')]));
    const anchors = new Map([...sources].map(([page, text]) => {
        const content = body(text);
        const headings = [...content.matchAll(/^#{1,6}\s+(.+?)\s*$/gm)];
        const ids = headings.map(match => slug(match[1]));
        if (!headings.length || content.slice(0, headings[0].index).trim()) ids.unshift('introduzione');
        if (new Set(ids).size !== ids.length) throw new Error('Duplicate manual heading: ' + page);
        return [page, new Set(ids)];
    }));
    const compiled = [];
    for (const [page, text] of sources) {
        const references = [];
        const definitions = new Map();
        const referenceUses = [];
        function visit(node) {
            if (node.type === 'definition') definitions.set(node.identifier, node.url);
            if (['linkReference', 'imageReference'].includes(node.type)) referenceUses.push(node.identifier);
            if (['link', 'image'].includes(node.type)) references.push(node.url);
            for (const attribute of node.attributes || []) {
                if (['href', 'src'].includes(attribute.name)) {
                    if (typeof attribute.value !== 'string') throw new Error('Manual references must be literal: ' + page);
                    references.push(attribute.value);
                }
            }
            for (const child of node.children || []) visit(child);
        }
        const result = await compile({path: page + '.mdx', value: body(text)}, {
            remarkPlugins: [() => tree => { visit(tree); }],
        });
        for (const id of referenceUses) {
            if (!definitions.has(id)) throw new Error('Missing link definition: ' + page + ':' + id);
            references.push(definitions.get(id));
        }
        for (const reference of references) {
            if (typeof reference !== 'string' || /[\u0000-\u0020\\]/.test(reference))
                throw new Error('Invalid manual reference: ' + page + ':' + reference);
            const url = new URL(reference, 'https://manual.invalid/' + page);
            if (!['http:', 'https:', 'mailto:', 'tel:'].includes(url.protocol))
                throw new Error('Unsafe manual reference: ' + page + ':' + reference);
            if (/^[a-z][a-z0-9+.-]*:/i.test(reference) || reference.startsWith('//')) continue;
            const relative = decodeURIComponent(url.pathname.slice(1));
            const target = relative.replace(/\.mdx$/, '');
            if (sources.has(target)) {
                if (url.hash && !anchors.get(target).has(decodeURIComponent(url.hash.slice(1))))
                    throw new Error('Missing manual anchor: ' + page + ':' + reference);
            } else {
                if (!fs.statSync(owned(manual, relative)).isFile())
                    throw new Error('Missing manual file: ' + page + ':' + reference);
            }
        }
        compiled.push({page, source_sha256: sha(text), code: String(result), references: references.length});
    }
    // No previous build is replaced until all pages and references pass.
    for (const item of compiled) {
        const destination = path.join(output, 'compiled', item.page + '.js');
        fs.mkdirSync(path.dirname(destination), {recursive: true});
        fs.writeFileSync(destination, item.code);
    }
    const report = {format: 1, status: 'passed', compiler: '@mdx-js/mdx@3.1.1',
        scope: 'static-mdx-and-local-references',
        pages: compiled.map(({code, ...item}) => ({...item, compiled_sha256: sha(code)}))};
    fs.writeFileSync(path.join(output, 'compile.json'), JSON.stringify(report, null, 2) + '\n');
    return report;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
    const run = path.resolve(process.argv[2]);
    const state = JSON.parse(fs.readFileSync(path.join(run, 'run.json')));
    try {
        const result = await compileManual(state.manual, run);
        console.log('Compiled and checked ' + result.pages.length + ' manual pages without runtime services.');
    } catch (error) {
        fs.writeFileSync(path.join(run, 'compile.json'), JSON.stringify({format: 1, status: 'failed', error: error.message}) + '\n');
        throw error;
    }
}
