#!/usr/bin/env node
/**
Render Mermaid diagram sources in docs/diagrams to SVG artifacts.

Usage examples:
  node docs/scripts/render_mermaid_diagrams.mjs
  node docs/scripts/render_mermaid_diagrams.mjs --source=docs/diagrams --output=docs/diagrams/rendered --format=svg
*/

import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';

const ROOT = process.cwd();

const defaults = {
  source: path.join(ROOT, 'docs/diagrams'),
  output: path.join(ROOT, 'docs/diagrams/rendered'),
  format: 'svg',
  theme: 'default',
  width: '1800',
  height: '1200',
};

const options = { ...defaults };

for (const arg of process.argv.slice(2)) {
  if (!arg.startsWith('--')) continue;
  const [raw, value] = arg.replace(/^--/, '').split('=', 2);
  if (!raw) continue;

  if (raw === 'help') {
    console.log(`
Usage:
  node docs/scripts/render_mermaid_diagrams.mjs [options]

Options:
  --source=PATH     Directory with .mmd files (default: docs/diagrams)
  --output=PATH     Output directory for rendered files (default: docs/diagrams/rendered)
  --format=svg|png  Output format (default: svg)
  --theme=default|forest|dark|neutral
  --width=NUM
  --height=NUM
  --help
`);
    process.exit(0);
  }

  if (raw === 'source' && value) options.source = path.resolve(value);
  if (raw === 'output' && value) options.output = path.resolve(value);
  if (raw === 'format' && value) options.format = value;
  if (raw === 'theme' && value) options.theme = value;
  if (raw === 'width' && value) options.width = value;
  if (raw === 'height' && value) options.height = value;
}

const files = fs
  .readdirSync(options.source, { withFileTypes: true })
  .filter((entry) => entry.isFile() && entry.name.endsWith('.mmd'))
  .map((entry) => entry.name);

if (files.length === 0) {
  console.log(`No .mmd files found in ${options.source}`);
  process.exit(0);
}

await fs.promises.mkdir(options.output, { recursive: true });

const rendered = [];
for (const filename of files) {
  const inputPath = path.join(options.source, filename);
  const base = filename.replace(/\.mmd$/, '');
  const outputPath = path.join(options.output, `${base}.${options.format}`);

  const command = [
    'npx',
    '-y',
    '@mermaid-js/mermaid-cli',
    '-i',
    inputPath,
    '-o',
    outputPath,
    '-t',
    options.theme,
    '-w',
    options.width,
    '-H',
    options.height,
    '-q',
    '--outputFormat',
    options.format,
  ];

  const proc = spawnSync(command[0], command.slice(1), {
    stdio: 'pipe',
    encoding: 'utf8',
  });

  if (proc.status !== 0) {
    console.error(`[error] ${filename}`);
    if (proc.stdout) console.log(proc.stdout.trim());
    if (proc.stderr) console.error(proc.stderr.trim());
    continue;
  }

  rendered.push({
    source: filename,
    output: path.relative(ROOT, outputPath),
  });
  console.log(`[ok] ${filename} -> ${outputPath}`);
}

if (rendered.length > 0) {
  const manifest = [
    '# Rendered Mermaid diagrams',
    '',
    '| Source | Output |',
    '| --- | --- |',
    ...rendered.map((item) => `| ${item.source} | ${item.output} |`),
    '',
  ];
  await fs.promises.writeFile(
    path.join(options.output, 'README.md'),
    `${manifest.join('\n')}\n`,
    'utf8',
  );
}

console.log(`Rendered ${rendered.length}/${files.length} diagrams to ${options.output}`);
