// Reviewed-section recipe bindings; BE/application/manuale/recipe_binding.py
// implements the same digest. The whole recipe module is hashed except the
// reviewed hashes of sources the section's own contracts do not use. A section
// without registered contracts, or a module whose reviewed hashes are not all
// in recognized one-entry-per-line maps, keeps the whole-file hash.
import crypto from 'node:crypto';

const FORMAT = 'manuale-section-recipe-v1\0';
const MAP = /^(?:export )?const \w+ = (?:Object\.freeze\()?\{\n((?:[ \t]*"[^"\n]+": "[a-f0-9]{64}",?\n)+)\}\)?;/gm;
const ENTRY = /^([ \t]*)"([^"\n]+)": "([a-f0-9]{64})"(,?)$/gm;
const HASH = /[a-f0-9]{64}/g;
const count = text => (text.match(HASH) || []).length;
const sha256 = (...parts) => parts.reduce((hash, part) => hash.update(part), crypto.createHash('sha256')).digest('hex');

/** The binding digest of module bytes for a section's source paths (null: unknown section). */
export function sectionRecipeSha256(module, sources) {
    if (!sources) return sha256(module);
    const text = module.toString('utf8');
    const blocks = [...text.matchAll(MAP)];
    if (!blocks.length || blocks.reduce((total, block) => total + count(block[1]), 0) !== count(text)) return sha256(module);
    const keep = new Set(sources);
    let result = '', last = 0;
    for (const block of blocks) {
        result += text.slice(last, block.index) + block[0].replace(ENTRY, (entry, indent, path, hash, comma) =>
            keep.has(path) ? entry : `${indent}"${path}": "elided"${comma}`);
        last = block.index + block[0].length;
    }
    result += text.slice(last);
    return sha256(FORMAT, Buffer.from(result, 'utf8'));
}
