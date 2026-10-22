// Shared metadata projection. Neither function supplies or generates user prose.
export function editorialDescriptors(module, sections, contracts, reviewed) {
    return sections.map(({contracts: keys, ...section}) => ({
        ...section, recipe_module: module, verified: false,
        source_contracts: keys.map(key => {
            const [path, symbol, length] = contracts[key];
            return {path, symbol, length, reviewed_sha256: reviewed[path]};
        }),
    }));
}

export function referenceDescriptors(sections, contracts, reviewed, source) {
    const evidence = new Map();
    return sections.map(({contracts: keys, ...section}) => ({
        ...section, evidence: keys.map(key => {
            if (!evidence.has(key)) {
                const [path, symbol, length] = contracts[key];
                const value = source(path, symbol, length);
                if (value.canonical_source_sha256 !== reviewed[path])
                    throw new Error('Reference evidence changed; review required: ' + path);
                evidence.set(key, value);
            }
            return evidence.get(key);
        }),
    }));
}
