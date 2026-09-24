// VERSION is supplied by the release workflow; commit hashes are unstable builds.
export function buildVersion(value, deployEnv) {
    const tag = typeof value === 'string' ? value.trim() : '';
    const release = /^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/.exec(tag);
    return deployEnv === 'production' && release ? tag : 'unstable';
}

export function assetVersion(label) {
    // The existing HTML query parameter accepts digits only. Vite's bundled
    // assets retain their content hashes; unstable public assets revalidate.
    const match = /^v?(\d+)\.(\d+)\.(\d+)/.exec(label);
    return match ? match.slice(1).map((part, index) => part.padStart(index + 2, '0')).join('') : '0';
}
