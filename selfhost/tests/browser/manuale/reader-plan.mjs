// Select assertions from the actual corpus, including incremental-only runs.
const searches = [
    ['docs/bacheca#aggiungere-un-widget', 'Come posso aggiungere un widget alla bacheca?'],
    ['docs/carnet#creare-e-gestire-un-carnet', 'Come posso creare un carnet?'],
    ['tutorials/come-gestire-registro-presenze-carnet#correggere-una-presenza-errata', 'Come posso correggere una presenza errata?'],
    ['docs/impostazioni#anno-fiscale', 'Come configurare l’anno fiscale?'],
    ['docs/collaboratori#modificare-i-permessi-di-un-collaboratore', 'Come modificare i permessi di un collaboratore?'],
    ['docs/contabilita-avanzata#creare-un-nuovo-conto', 'Come creare un nuovo conto finanziario?'],
    ['docs/contabilita-avanzata#eliminare-un-giroconto', 'Come eliminare un giroconto?'],
    ['faq/come-generare-bilancio#pubblicare-il-bilancio', 'Manuale: come pubblicare il bilancio e annullare la pubblicazione?'],
    ['docs/camp-e-ritiri#creare-un-camp-o-ritiro', 'Come creare un camp o ritiro?'],
    ['docs/camp-e-ritiri#aggiungere-un-periodo', 'Come aggiungere un periodo al camp?'],
    ['docs/calendario#esportare-il-calendario', 'Come esportare il calendario in formato ics?'],
    ['tutorials/come-creare-moduli-iscrizione-personalizzati#campi-aggiuntivi', 'Come posso aggiungere campi aggiuntivi per la taglia maglietta al modulo iscrizione?'],
    ['tutorials/come-creare-moduli-iscrizione-personalizzati#sezioni-del-modulo-d-iscrizione', 'Come posso aggiungere sezioni del modulo iscrizione visibili a soci e tesserati?'],
    ['faq/come-condividere-il-link-iscrizioni#1-copia-il-link', 'Come posso copiare il link iscrizioni negli appunti?'],
    ['docs/istruttori#aggiungere-un-istruttore', 'Come posso aggiungere un istruttore?'],
    ['tutorials/come-impostare-gestire-istruttori#aggiungere-un-nuovo-istruttore', 'Come posso aggiungere un istruttore?'],
    ['docs/libro-soci#anagrafica-smart', 'Come posso modificare numero tessera e tipo iscrizione?'],
    ['tutorials/come-gestire-certificati-medici#dal-profilo-dell-atleta', "Come posso caricare un certificato medico dal profilo dell'atleta?"],
    ['docs/libro-soci#ciclo-di-vita-dell-iscrizione', 'Come posso approvare una iscrizione?'],
    ['faq/come-si-crea-un-socio#creare-un-socio-socio-tesserato-o-tesserato', 'Come posso creare un socio e tesserato?'],
    ['tutorials/come-assegnare-i-tag-agli-atleti#assegna-tag', 'Come posso assegnare un tag?'],
    ['faq/come-usare-ricerca-filtri-atleti#la-barra-di-ricerca', 'Come posso trovare Giulia nelle iscrizioni?'],
    ['docs/corsi#creare-un-corso', 'Come posso creare un corso Standard?'],
    ['faq/come-archiviare-dati#archiviazione-manuale', 'Come posso archiviare iscrizioni con Archivia selezionati?'],
    ['docs/pagamenti#creare-un-pagamento', 'Come posso creare un pagamento in contanti?'],
    ['docs/ricevute#come-posso-scaricare-una-ricevuta', 'Come posso scaricare il PDF di una ricevuta?'],
    ['docs/ricevute#modifica-del-progressivo-della-ricevuta', 'Come posso modificare il progressivo di una ricevuta?'],
    ['faq/quali-sono-piani-abbonamento#piano-pro', 'Manuale Piano Pro installazione self-hosted'],
    ['faq/quali-sono-piani-abbonamento#piani-di-abbonamento', 'Manuale piani di abbonamento installazione self-hosted'],
    ['faq/quali-sono-piani-abbonamento#come-cambiare-piano', 'Manuale acquisto cambio piano abbonamento'],
    ['faq/i-miei-dati-sono-al-sicuro#misure-di-sicurezza-avanzate', 'Manuale misure di sicurezza avanzate autenticazione a due fattori'],
];

export function readerPlan(chunks, features = ['self_hosted']) {
    const sections = chunks.filter(chunk => chunk.status === 'verified' && chunk.audience === 'public'
        && (chunk.features || []).every(feature => features.includes(feature)));
    if (!sections.length) throw new Error('Reader verification requires applicable public sections');
    const selected = searches.find(([id]) => sections.some(section => section.id === id));
    const section = selected ? sections.find(section => section.id === selected[0]) : sections[0];
    return {sections, section, query: selected ? selected[1] : 'Manuale ' + section.title,
        unsupportedQuery: 'Manuale teletrasporto satellitare'};
}
