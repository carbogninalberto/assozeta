#!/usr/bin/env node
/* eslint-disable no-console */
import fs from 'node:fs';
import path from 'node:path';

const ROOT = process.cwd();

const readFile = (relativePath) => fs.readFileSync(path.join(ROOT, relativePath), 'utf8');

const normalizeEndpoint = (value) => {
    return value
        .replace(/^r'|^r"|^'|^"|"$|'$/g, '')
        .replace(/<[^>]+>/g, '*')
        .replace(/<[^>]+>/g, '*')
        .replace(/:([A-Za-z0-9_?]+)\b/g, '*')
        .replace(/\//g, '/')
        .replace(/\/+/g, '/')
        .replace(/^\//, '')
        .replace(/\/$/, '');
};

const normalizeBackendPattern = (value) => {
    return normalizeEndpoint(value)
        .replace(/<[^>]+>/g, '*');
};

const normalizeUIRoute = (value) => normalizeEndpoint(value);

const collectDjangoPaths = (filePath, { includeRouter = false } = {}) => {
    const txt = readFile(filePath);
    const lines = txt.split('\n');
    const paths = [];

    const pathRe = /path\(\s*r?(['"])(.*?)\1/g;
    let m;
    while ((m = pathRe.exec(txt)) !== null) {
        const p = m[2];
        if (p.includes('include(')) {
            continue;
        }
        paths.push(p);
    }

    if (includeRouter) {
        const routerRe = /router\.register\(r?(['"])(.*?)\1/g;
        while ((m = routerRe.exec(txt)) !== null) {
            paths.push(m[2]);
        }
    }

    return paths;
};

const collectRouterRegistrations = (filePath) => {
    const txt = readFile(filePath);
    const lines = txt.split('\n');
    const out = [];
    const re = /router\.register\(r?(['"])([^'"]+)\1\s*,\s*([A-Za-z0-9_]+)\s*(?:,\s*basename\s*=\s*(['"])([^'"]+)\4)?/g;
    let m;
    while ((m = re.exec(txt)) !== null) {
        out.push({
            prefix: m[2],
            viewset: m[3],
            basename: m[5] || '(inferred)',
        });
    }
    return out;
};

const collectPermissionsRegistry = (filePath) => {
    const txt = readFile(filePath);
    const lines = txt.split('\n');
    const explicit = [];
    const excluded = [];

    let collectingPerms = false;
    for (const rawLine of lines) {
        const line = rawLine.trim();
        if (line.includes('PERMISSIONS_REGISTRY')) {
            collectingPerms = true;
            continue;
        }
        if (line.includes('EXCLUDED_ENDPOINTS')) {
            collectingPerms = false;
            continue;
        }
        if (line.startsWith('}')) {
            continue;
        }
        if (line.includes(')')) {
            continue;
        }

        if (!collectingPerms) {
            if (line.startsWith("'") || line.startsWith('("') || line.startsWith("('") ) {
                const excl = line.match(/^['"]([^'"]+)['"]\s*:/);
                if (excl && excl[1].startsWith('export ')) {
                    excluded.push(normalizeEndpoint(excl[1]));
                }
            }
            continue;
        }

        const tupleMatch = line.match(/^\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]\s*\)\s*:\s*['"]([^'"]+)['"]\s*,?/);
        if (tupleMatch) {
            explicit.push({
                method: tupleMatch[1].toUpperCase(),
                path: normalizeBackendPattern(tupleMatch[2]),
                perm: tupleMatch[3],
                raw: tupleMatch[2],
            });
            continue;
        }

        const simpleMatch = line.match(/^['"]([^'"]+)['"]\s*:\s*['"]([^'"]+)['"]\s*,?/);
        if (simpleMatch) {
            explicit.push({
                method: 'ANY',
                path: normalizeBackendPattern(simpleMatch[1]),
                perm: simpleMatch[2],
                raw: simpleMatch[1],
            });
        }
    }

    // Explicitly parse exclusions block
    const exclusionBlock = txt.match(/EXCLUDED_ENDPOINTS\s*=\s*\[(.*?)\]/s);
    if (exclusionBlock && exclusionBlock[1]) {
        const excludedRaw = exclusionBlock[1];
        const exclLine = /['"]([^'"]+)['"]/g;
        let mm;
        while ((mm = exclLine.exec(excludedRaw)) !== null) {
            excluded.push(normalizeEndpoint(mm[1]));
        }
    }

    return {
        explicit,
        excluded: [...new Set(excluded)],
    };
};

const collectFrontendRoutes = (filePath) => {
    const txt = readFile(filePath);
    const lines = txt.split('\n');
    const routes = [];
    let current = null;
    const blocks = {};

    const routeLineRe = /^\s*['"]([^'"]+)['"]:\s*wrap\(\{/;
    const permRe = /canPerformAction\(\s*['"]([^'"]+)['"]|checkAssociationAccess\(\s*['"]([^'"]+)['"]/g;
    const compRe = /asyncComponent:/;

    const startBlock = (route) => {
        current = route;
        blocks[route] = {
            permissions: new Set(),
            hasComponent: false,
            raw: route,
        };
    };

    for (const l of lines) {
        const routeMatch = l.match(routeLineRe);
        if (routeMatch) {
            startBlock(routeMatch[1]);
            continue;
        }

        if (!current) {
            continue;
        }

        if (compRe.test(l)) {
            blocks[current].hasComponent = true;
        }

        const permMatches = [...l.matchAll(permRe)];
        for (const pm of permMatches) {
            const val = pm[1] || pm[2];
            if (val) {
                blocks[current].permissions.add(val);
            }
        }

        if (l.trim() === '}),') {
            current = null;
        }
    }

    return Object.values(blocks).map((r) => ({
        route: normalizeUIRoute(r.raw),
        routeRaw: r.raw,
        permissions: [...r.permissions].sort(),
        hasExplicitPermissionCheck: r.permissions.size > 0,
        hasComponent: r.hasComponent,
    }));
};

const main = () => {
    const backendFiles = [
        ['BE/application/urls.py', 'application'],
        ['BE/communications/urls.py', 'communications'],
        ['BE/docmanager/urls.py', 'docmanager'],
        ['BE/application/chat/urls.py', 'chat'],
        ['BE/instance/urls.py', 'instance'],
        ['BE/core/urls.py', 'core'],
    ];

    const backendByApp = {};
    for (const [file, app] of backendFiles) {
        const includeRouter = app === 'application';
        const patterns = collectDjangoPaths(file, { includeRouter });
        backendByApp[app] = [...new Set(patterns)].sort();
    }

    const routerRegs = collectRouterRegistrations('BE/application/urls.py');
    const perms = collectPermissionsRegistry('BE/application/permissions_registry.py');
    const frontendRoutes = collectFrontendRoutes('UI/src/routes.js');

    const apiPatterns = new Set();
    Object.values(backendByApp).forEach((arr) => arr.forEach((p) => apiPatterns.add(normalizeBackendPattern(p))));

    const routerPrefixes = routerRegs.map((x) => normalizeEndpoint(x.prefix));
    const permissionByMethod = perms.explicit;
    const permissionPaths = new Set(permissionByMethod.map((x) => x.path));

    const unmapped = [...apiPatterns]
        .filter((p) => !permissionPaths.has(p))
        .filter((p) => !perms.excluded.includes(p))
        .sort();

    const extraPermissions = [...permissionPaths]
        .filter((p) => !apiPatterns.has(p))
        .sort();

    const missingRoutesForRegistry = extraPermissions.filter((p) => !apiPatterns.has(p) && p.includes('automation'));

    const lines = [];
    lines.push('# Backend Route Inventory (literal)');
    lines.push('');
    for (const [app, routes] of Object.entries(backendByApp)) {
        lines.push(`## ${app}.py`);
        for (const p of routes) {
            lines.push(`- ${p}`);
        }
        lines.push('');
    }

    lines.push('## DRF Router registrations (application)');
    lines.push('');
    for (const reg of routerRegs) {
        lines.push(`- \`${reg.prefix}\` → ${reg.viewset} (basename: ${reg.basename})`);
    }
    lines.push('');

    lines.push('## WebSocket routes');
    lines.push('- `/ws/notifications/`');
    lines.push('- `/ws/health/`');
    lines.push('- `/ws/updates/`');
    lines.push('- `/ws/agent/`');
    lines.push('');

    lines.push('# Frontend Route Inventory');
    lines.push('');
    lines.push(`- Total routes: ${frontendRoutes.length}`);
    lines.push('- Route entries with explicit permission checks:');
    for (const r of frontendRoutes) {
        const permsText = r.permissions.length ? `\`${r.permissions.join('`, `')}\`` : '-';
        lines.push(`- ${r.routeRaw} → ${permsText}`);
    }
    lines.push('');

    lines.push('# Permissions Registry Coverage');
    lines.push(`- Total backend endpoint patterns: ${apiPatterns.size}`);
    lines.push(`- Total registry patterns: ${permissionPaths.size}`);
    lines.push(`- Excluded by default: ${perms.excluded.length}`);
    lines.push(`- Registry unmatched by endpoint list: ${extraPermissions.length}`);
    lines.push(`- Unmapped backend endpoints: ${unmapped.length}`);
    lines.push('');

    lines.push('## Unmapped backend endpoints (check required registry entries or exclusion)');
    for (const p of unmapped) {
        lines.push(`- ${p}`);
    }
    lines.push('');
    lines.push('## Permissions without backend endpoint match');
    for (const p of extraPermissions) {
        lines.push(`- ${p}`);
    }
    lines.push('');

    const payload = {
        generatedAt: new Date().toISOString(),
        backendByApp,
        routerRegs,
        apiPatterns: [...apiPatterns].sort(),
        permissionRegistry: perms.explicit,
        permissionExclusions: perms.excluded,
        frontendRoutes,
        unmappedBackend: unmapped,
        permissionsWithoutBackend: extraPermissions,
    };

    fs.mkdirSync(path.join(ROOT, 'docs/matrix'), { recursive: true });
    fs.writeFileSync(
        path.join(ROOT, 'docs/matrix/architecture-inventory.md'),
        lines.join('\n'),
        'utf8',
    );
    fs.writeFileSync(
        path.join(ROOT, 'docs/matrix/architecture-inventory.json'),
        `${JSON.stringify(payload, null, 2)}\n`,
        'utf8',
    );

    console.log(`Generated docs/matrix/architecture-inventory.md`);
    console.log(`Generated docs/matrix/architecture-inventory.json`);
    console.log(`Backend patterns: ${apiPatterns.size}`);
    console.log(`Permissions: ${permissionPaths.size}`);
    console.log(`Unmapped backend endpoints: ${unmapped.length}`);
};

main();
