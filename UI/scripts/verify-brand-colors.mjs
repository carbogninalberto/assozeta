import {readFileSync, readdirSync} from 'node:fs';
import {resolve, relative} from 'node:path';
import {fileURLToPath} from 'node:url';
import postcss from 'postcss';
const root=fileURLToPath(new URL('../',import.meta.url));
const issues=[];
// Check application-owned primary rules. Semantic palettes and vendor/artwork files
// deliberately retain their own colors; bootstrap.min.css is upstream, not our theme.
for(const file of ['public/static/css/app-bundle.css','public/global.css','public/dark-mode.css']){
    postcss.parse(readFileSync(resolve(root,file),'utf8')).walkRules(rule=>{
        if(rule.selector.includes(':root') || /^\[data-theme="dark"\]$/.test(rule.selector))return;
        if(!/primary|:focus|swal2-progress|ribbon-target/.test(rule.selector))return;
        if(/info|success|danger|warning/.test(rule.selector))return;
        rule.walkDecls(d=>{
            const value=d.value.replace(/var\([^)]*\)/g,'');
            if(/#(?:351dc2|69b3ff|6c57e6|2778c4|5c47e5|1086ff|037fff|6b6be0|5b6bd0|e1f0ff|deedff|a5b4fc)|rgba?\((?:54,\s*153,\s*255|53,\s*29,\s*194|92,\s*107,\s*192)/i.test(value))issues.push(`${file}:${d.source.start.line}: ${rule.selector}`);
        });
    });
}
function walk(dir){for(const e of readdirSync(dir,{withFileTypes:true})){const path=resolve(dir,e.name);if(e.isDirectory())walk(path);else if(/\.(svelte|js)$/.test(e.name)&&!e.name.endsWith('.test.js')){
    if(e.name === 'BrandTheme.js')continue; // the single configured default
    readFileSync(path,'utf8').split('\n').forEach((line,i)=>{
        if(/primaryColor:|Assozeta purple|<!--/.test(line))return; // setup presets and comments
        const withoutFallbacks = line.replace(/var\([^)]*\)/g, '');
        if(/#351dc2|rgba\(53,\s*29,\s*194/i.test(withoutFallbacks))issues.push(`${relative(root,path)}:${i+1}: hardcoded brand`);
    });
}}}
walk(resolve(root,'src'));
if(issues.length){console.error(issues.join('\n'));process.exitCode=1;}else console.log('Brand color checks passed.');
