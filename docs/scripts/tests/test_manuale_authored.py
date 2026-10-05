"""Publishing consumes reviewed MDX and preserves unverified placeholders."""
import json
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
NODE = r'''
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
const {authoredSection, replaceAuthoredSection, materializeCapturedSection, publicationBinding, editorialWorkflowPublication, authoredPublicationOwners, mayPublishAuthoredSection} = await import(process.argv[1]);
const {validateAuthoredWorkflow} = await import(process.argv[2]);
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'manual-authored-'));
try {
 const rewritten={editorial_generation:{requires_implementation_review:true}};
 const procedureKey='docs/guide.mdx#salva';
 const completeSection={path:'docs/guide.mdx',id:'salva'};
 const owners=authoredPublicationOwners({complete:{sections:[completeSection]}});
 assert.equal(mayPublishAuthoredSection(owners,'legacy',procedureKey,{}),false);
 assert.equal(mayPublishAuthoredSection(owners,'complete',procedureKey,{}),false);
 assert.equal(mayPublishAuthoredSection(owners,'complete',procedureKey,completeSection),true);
 assert.equal(mayPublishAuthoredSection(owners,'legacy','docs/other.mdx#intro',{}),true);
 assert.throws(()=>authoredPublicationOwners({one:{sections:[completeSection]},two:{sections:[completeSection]}}),/Multiple authored workflow owners/);

 assert.equal(editorialWorkflowPublication(procedureKey,'verified','workflow',rewritten).status,'pending');
 assert.equal(editorialWorkflowPublication(procedureKey,'verified','workflow',{},
  {editedKeys:new Set([procedureKey])}).status,'pending');
 assert.equal(editorialWorkflowPublication(procedureKey,'verified','workflow',rewritten,
  {sections:[{path:'docs/guide.mdx',id:'altro'}]}).status,'pending');
 assert.equal(editorialWorkflowPublication(procedureKey,'verified','workflow',rewritten,
  {sections:[{path:'docs/guide.mdx',id:'salva'}]}).status,'verified');
 assert.equal(editorialWorkflowPublication(procedureKey,'verified','workflow',{}).status,'verified');
 assert.equal(editorialWorkflowPublication(procedureKey,'unsupported','workflow',rewritten).status,'unsupported');
 const proofSpec={version:1,prefix:'images/unit/',sources:['UI/example.svelte'],checkpoints:[{id:'saved'},{id:'reopened'}],
  outcome:{field:'proof',expected:{persisted:true,rows:2}}};
 const proof={id:'unit-test-only',fixture_version:8,fixture_profile:'baseline',source_hashes:{'UI/example.svelte':'test-source'},
  screenshots:[{checkpoint:'saved',path:'images/unit/1.png'},{checkpoint:'reopened',path:'images/unit/2.png'}],proof:{persisted:true,rows:2}};
 validateAuthoredWorkflow(proof,proofSpec);
 const removalSpec={...proofSpec,fixture_profile:'collaborator-removal'};
 validateAuthoredWorkflow({...proof,fixture_profile:'collaborator-removal'},removalSpec);
 assert.throws(()=>validateAuthoredWorkflow(proof,removalSpec),/explicitly selected profile/);
 assert.throws(()=>validateAuthoredWorkflow({...proof,fixture_profile:'collaborator-removal'},proofSpec),/explicitly selected profile/);
 assert.throws(()=>validateAuthoredWorkflow({...proof,fixture_version:7},proofSpec),/current baseline fixture/);
 assert.throws(()=>validateAuthoredWorkflow({...proof,proof:{persisted:true,rows:1}},proofSpec),/Incomplete authored workflow outcome/);
 validateAuthoredWorkflow({...proof,screenshots:[...proof.screenshots].reverse()},proofSpec);
 assert.throws(()=>validateAuthoredWorkflow({...proof,screenshots:[proof.screenshots[0],proof.screenshots[0]]},proofSpec),/Missing, duplicate, or misbound/);
 assert.throws(()=>validateAuthoredWorkflow({...proof,screenshots:proof.screenshots.map(image=>({...image,path:'images/unit/9.png'}))},proofSpec),/Missing, duplicate, or misbound/);
 assert.throws(()=>validateAuthoredWorkflow({...proof,source_hashes:{}},proofSpec),/Missing authored workflow source/);
 const manual=path.join(temporary,'manual'),code=path.join(temporary,'code');
 fs.mkdirSync(path.join(manual,'docs'),{recursive:true});fs.mkdirSync(path.join(code,'docs'),{recursive:true});
 const recipe=path.join(code,'docs/recipe.mjs');fs.writeFileSync(recipe,'export const capture = true;');
 const body='## Salva\n\nPremi **Salva** e controlla.\n![Modulo](/images/form/1.png)';
 const draft=body.replace('1.png','1.placeholder.svg');
 const page=path.join(manual,'docs/form.mdx');fs.writeFileSync(page,'---\ntitle: Modulo\n---\n\n'+draft+'\n\n## Altro\nContenuto distinto.');
 const sidecar={format:1,purpose:'reviewed-content',verified:false,sections:{'docs/form.mdx#salva':{
  title:'Salva',recipe_module:'docs/recipe.mjs',recipe_sha256:sha(fs.readFileSync(recipe)),content_sha256:sha(body)}}};
 const sidecarPath=path.join(manual,'.manuale-evidence.json');fs.writeFileSync(sidecarPath,JSON.stringify(sidecar));
 assert.equal(authoredSection(manual,code,'docs/form.mdx','salva','Salva'),draft);
 assert.equal(authoredSection(manual,code,'docs/form.mdx','salva','Salva',{captured:true}),body);
 const notice='Bozza in attesa di prova: questa sezione deve essere confermata con una procedura reale prima della pubblicazione.';
 const workflow='## Salva\n\n<Note>'+notice+'</Note>\n\n<Note>La consegna email richiede una verifica separata.</Note>\n'+
  '<Steps><Step title="Salva">Premi **Salva**.\n<Frame>![Modulo](/images/form/pending.svg)</Frame></Step>'+
  '<Step title="Riapri">Controlla i dati dopo il ricaricamento.</Step></Steps>';
 const screenshots=[{checkpoint:'saved',path:'images/form/1.png',caption:'Salvataggio'},
  {checkpoint:'reopened',path:'images/form/2.png',caption:'Dati ricaricati'}];
 const spec={images:[{from:'/images/form/pending.svg',checkpoint:'saved'}],
  insert_images:[{step:'Riapri',checkpoint:'reopened'}]};
 const published=materializeCapturedSection(workflow,spec,screenshots);
 assert.ok(!published.includes(notice));
 assert.ok(published.includes('La consegna email richiede una verifica separata.'));
 assert.ok(published.includes('/images/form/1.png') && published.includes('/images/form/2.png'));
 assert.equal(materializeCapturedSection(published,spec,screenshots),published);
 const sourceNotice='Procedura basata sulle sorgenti dell’applicazione; l’esecuzione completa e le schermate reali devono ancora essere verificate nell’istanza dimostrativa.';
 const externalNotice='Le schermate reali non verificano la consegna email: richiede una prova esterna.';
 const sourceDraft=workflow.replace(notice,sourceNotice)+'\n\n<Note>'+externalNotice+'</Note>';
 fs.writeFileSync(page,sourceDraft);
 sidecar.sections['docs/form.mdx#salva'].content_sha256=sha(sourceDraft);
 fs.writeFileSync(sidecarPath,JSON.stringify(sidecar));
 // Reading a reviewed draft never clears its pending-capture notice.
 assert.ok(authoredSection(manual,code,'docs/form.mdx','salva','Salva').includes(sourceNotice));
 // A missing reopen capture cannot produce materialized content or alter its input.
 assert.throws(()=>materializeCapturedSection(sourceDraft,spec,screenshots.slice(0,1)),/Missing or ambiguous captured checkpoint/);
 assert.equal(fs.readFileSync(page,'utf8'),sourceDraft);
 const sourcePublished=materializeCapturedSection(sourceDraft,spec,screenshots);
 assert.ok(!sourcePublished.includes(sourceNotice));
 assert.ok(sourcePublished.includes(externalNotice));
 assert.ok(sourcePublished.includes('La consegna email richiede una verifica separata.'));
 assert.equal(materializeCapturedSection(sourcePublished,spec,screenshots),sourcePublished);
 const inlineStep='<Steps>\n<Step title="Riapri">Controlla i dati.</Step>\n</Steps>';
 const expandedInline=materializeCapturedSection(inlineStep,{insert_images:[{step:'Riapri',checkpoint:'reopened'}]},screenshots);
 assert.ok(expandedInline.includes('<Step title="Riapri">\nControlla i dati.\n\n<Frame>'));
 assert.equal(materializeCapturedSection(expandedInline,{insert_images:[{step:'Riapri',checkpoint:'reopened'}]},screenshots),expandedInline);
 const placed='<Steps><Step title="Riapri">Controlla i dati.<Frame>![Segnaposto dei dati](/images/form/2.placeholder.svg)</Frame></Step></Steps>';
 const deduplicated=materializeCapturedSection(placed,{insert_images:[{step:'Riapri',checkpoint:'reopened'}]},screenshots);
 assert.equal((deduplicated.match(/\/images\/form\/2.png/g)||[]).length,1);
 assert.ok(deduplicated.includes('![Dati ricaricati]'));
 const pendingText='Il download attende una prova completa.';
 const editorial=materializeCapturedSection(workflow+'\n\n'+pendingText,
  {...spec,remove_draft_text:[pendingText]},screenshots);
 assert.ok(!editorial.includes(pendingText));
 assert.ok(editorial.includes('La consegna email richiede una verifica separata.'));
 assert.throws(()=>materializeCapturedSection(workflow,spec,screenshots.slice(0,1)),/Missing or ambiguous captured checkpoint/);
 assert.throws(()=>materializeCapturedSection(workflow,{insert_images:[{step:'Elimina',checkpoint:'saved'}]},screenshots),/Missing or ambiguous authored step/);
 assert.throws(()=>materializeCapturedSection(workflow.replace('pending.svg','unknown.png'),spec,screenshots),/Missing reviewed image slot/);
 sidecar.sections['docs/form.mdx#salva'].content_sha256=sha(workflow);
 const editorialHash=sidecar.sections['docs/form.mdx#salva'].content_sha256;
 sidecar.sections['docs/form.mdx#salva']=publicationBinding(sidecar.sections['docs/form.mdx#salva'],published,
  {captureId:'test-fixture',scenarioId:'test-only',reportSha256:sha('unit fixture, no real capture')});
 assert.equal(sidecar.sections['docs/form.mdx#salva'].editorial_content_sha256,editorialHash);
 fs.writeFileSync(sidecarPath,JSON.stringify(sidecar));fs.writeFileSync(page,published);
 assert.equal(authoredSection(manual,code,'docs/form.mdx','salva','Salva'),published);
 fs.writeFileSync(page,published.replace('Premi **Salva**','Premi **Elimina**'));
 assert.throws(()=>authoredSection(manual,code,'docs/form.mdx','salva','Salva'),/Review changed manual prose/);
 sidecar.sections['docs/form.mdx#salva'].content_sha256=sha(body);
 fs.writeFileSync(sidecarPath,JSON.stringify(sidecar));
 const intro='Le comunicazioni hanno destinatari distinti.';
 const prefaced='---\ntitle: Comunicazioni\n---\n\n'+intro+'\n\n## Salva\nContenuto successivo.';
 fs.writeFileSync(page,prefaced);
 sidecar.sections['docs/form.mdx#introduzione']={title:'Introduzione',recipe_module:'docs/recipe.mjs',
  recipe_sha256:sha(fs.readFileSync(recipe)),content_sha256:sha(intro)};
 fs.writeFileSync(sidecarPath,JSON.stringify(sidecar));
 assert.equal(authoredSection(manual,code,'docs/form.mdx','introduzione','Introduzione'),intro);
 assert.equal(replaceAuthoredSection(prefaced,'Introduzione','Nuovo testo.','introduzione'),
  '---\ntitle: Comunicazioni\n---\n\nNuovo testo.\n\n## Salva\nContenuto successivo.');
 assert.throws(()=>replaceAuthoredSection(prefaced,'Introduzione','Testo.','altro'),/Missing or ambiguous/);
 fs.writeFileSync(page,prefaced.replace(intro,'Affermazione cambiata.'));
 assert.throws(()=>authoredSection(manual,code,'docs/form.mdx','introduzione','Introduzione'),/Review changed manual prose/);
 fs.writeFileSync(page,'---\ntitle: Modulo\n---\n\n'+draft+'\n\n## Altro\nContenuto distinto.');
 fs.writeFileSync(page,fs.readFileSync(page,'utf8').replace('Premi **Salva**','Premi **Elimina**'));
 assert.throws(()=>authoredSection(manual,code,'docs/form.mdx','salva','Salva'),/Review changed manual prose/);
 fs.writeFileSync(page,draft);fs.writeFileSync(recipe,'export const capture = false;');
 assert.throws(()=>authoredSection(manual,code,'docs/form.mdx','salva','Salva'),/Review bindings after recipe changes/);
 fs.writeFileSync(recipe,'export const capture = true;');fs.unlinkSync(page);
 const external=path.join(temporary,'external.mdx');fs.writeFileSync(external,draft);fs.symlinkSync(external,page);
 assert.throws(()=>authoredSection(manual,code,'docs/form.mdx','salva','Salva'),/escapes checkout/);
 fs.unlinkSync(page);fs.writeFileSync(page,draft);fs.unlinkSync(sidecarPath);
 assert.throws(()=>authoredSection(manual,code,'docs/form.mdx','salva','Salva'),/Prepare reviewed MDX/);
 console.log(JSON.stringify({content_mutation_rejected:true,recipe_mutation_rejected:true,escape_rejected:true,
  placeholders_retained_for_drafts:true,missing_review_rejected:true}));
} finally {fs.rmSync(temporary,{recursive:true,force:true});}
'''


class AuthoredManualTests(unittest.TestCase):
    def test_coverage_includes_text_references_without_promoting_prepared_procedures(self):
        module_spec = importlib.util.spec_from_file_location('manuale_coverage', ROOT / 'docs/scripts/manuale-coverage.py')
        coverage = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(coverage)
        sections = {
            'docs/introduzione.mdx': ['cosa-desideri-approfondire', 'manuale', 'per-iniziare'],
            'faq/quali-sono-piani-abbonamento.mdx': ['piani-di-abbonamento', 'confronto-tra-i-piani',
                'piano-pro', 'come-cambiare-piano', 'piano-base'],
            'faq/i-miei-dati-sono-al-sicuro.mdx': ['la-sicurezza-dei-tuoi-dati-e-la-nostra-priorita',
                'misure-di-sicurezza-avanzate', 'robusta-politica-di-backup'],
        }
        with tempfile.TemporaryDirectory() as temporary:
            manual = Path(temporary)
            (manual / '.manuale-authoring.json').write_text(json.dumps({'pages': [
                {'path': path, 'sections': [{'id': id, 'title': id} for id in ids]}
                for path, ids in sections.items()]}))
            (manual / '.manuale-evidence.json').write_text(json.dumps({'sections': {
                path + '#' + id: {'recipe_module': 'docs/manuale/reference-recipes.mjs'}
                for path, ids in sections.items() for id in ids}}))
            result = coverage.inventory(manual)
        self.assertEqual(result['status_counts'], {'code-reference-prepared': 8,
            'execution-prepared': 1, 'needs_external_verification': 1, 'unsupported': 1})
        self.assertEqual(result['verified_sections'], 0)
        self.assertFalse(result['browser_executed'])
        self.assertTrue(all(entry['verified'] is False for entry in result['entries']))

    def test_complete_legacy_bindings_use_actual_reviewed_sources(self):
        script = r'''
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
const root=process.argv[1];
const {pathToFileURL}=await import('node:url');
const {authoredReferenceSections}=await import(pathToFileURL(path.join(root,'docs/manuale/authored-reference-recipes.mjs')));
const source=(relative,symbol,length)=>{
 const bytes=fs.readFileSync(path.join(root,relative)),lines=bytes.toString().split(/\r?\n/);
 const first=lines.findIndex(line=>line.includes(symbol));assert.ok(first>=0);
 const sha=value=>crypto.createHash('sha256').update(value).digest('hex');
 return {path:relative,symbol,start:first+1,end:Math.min(lines.length,first+length),
  sha256:sha(bytes),canonical_source_sha256:sha(lines.join('\n'))};
};
const sections=authoredReferenceSections(source);
for(const [relative,id] of [['docs/pagamenti.mdx','creare-un-pagamento'],
 ['faq/come-si-crea-un-socio.mdx','creare-un-socio-socio-tesserato-o-tesserato'],
 ['tutorials/come-assegnare-i-tag-agli-atleti.mdx','creare-un-nuovo-tag']]){
 const section=sections.find(item=>item.path===relative&&item.id===id);
 assert.equal(section?.authored,true);assert.equal(section.status,'pending');assert.ok(section.evidence.length);
}
assert.equal(new Set(sections.map(section=>section.path+'#'+section.id)).size,sections.length);
assert.throws(()=>authoredReferenceSections((...args)=>({...source(...args),canonical_source_sha256:'changed-source'})),/changed|review/i);
console.log('Complete legacy candidates remain pending and reject changed source hashes.');
'''
        subprocess.run(['node', '--input-type=module', '-e', script, str(ROOT)],
                       capture_output=True, text=True, check=True)

    def test_publication_reads_reviewed_mdx_and_rejects_stale_or_escaped_content(self):
        result = subprocess.run(['node', '--input-type=module', '-e', NODE,
            (ROOT / 'docs/manuale/manual-content.mjs').as_uri(),
            (ROOT / 'docs/manuale/authored-workflows.mjs').as_uri()], capture_output=True, text=True, check=True)
        self.assertTrue(all(json.loads(result.stdout).values()))


if __name__ == '__main__':
    unittest.main()
