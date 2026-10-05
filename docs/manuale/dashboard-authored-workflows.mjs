// Complete local layout procedures; runtime reports must come from the real scripts.
import {dashboardWorkflowSources} from '../../selfhost/tests/browser/manuale/dashboard-sources.mjs';
import {dashboardCaptureSpecs,dashboardExpectedOutcome} from './dashboard-recipes.mjs';
const dependencies=['selfhost/tests/browser/manuale/dashboard-sources.mjs','selfhost/tests/browser/manuale/dashboard.mjs',
 'selfhost/tests/browser/manuale/scenario.mjs','selfhost/tests/browser/manuale/frame.mjs',
 'selfhost/tests/browser/manuale/redaction.mjs','selfhost/tests/browser/playwright.manual.config.mjs',
 'BE/application/management/commands/seed_manuale.py'];
export const dashboardAuthoredWorkflows=Object.freeze({
 'dashboard-personalize':{version:1,script:'selfhost/tests/browser/manuale/dashboard.mjs',
 prefix:dashboardCaptureSpecs['dashboard-personalize'][0],pages:['docs/bacheca.mdx'],sources:dashboardWorkflowSources,dependencies,
 checkpoints:dashboardCaptureSpecs['dashboard-personalize'][1].map((id,index)=>({id,caption:["Bacheca iniziale con otto riquadri.", "Controlli per larghezza, rimozione e salvataggio.", "Scelta del widget Certificati medici scaduti.", "Widget aggiunto alla larghezza massima.", "Widget e larghezza conservati dopo la ricarica.", "Cestino per rimuovere il riquadro dalla bacheca.", "Bacheca dopo la rimozione salvata.", "Disposizione iniziale ripristinata prima del salvataggio.", "Disposizione iniziale conservata dopo la ricarica.", "Bacheca del collaboratore con limite sui messaggi dello staff.", "Larghezza personale del collaboratore, distinta da quella del titolare."][index]})),
 sections:[
  {
    "path": "docs/bacheca.mdx",
    "id": "personalizzare-la-bacheca",
    "title": "Personalizzare la bacheca",
    "checkpoints": [
      "edit-mode-size-remove-and-save-controls",
      "add-widget-modal-expired-certificates-selected",
      "added-widget-resized-to-full-width",
      "added-widget-and-width-persist-after-reload",
      "reader-personal-width-persists-owner-layout-unchanged"
    ],
    "images": [
      {
        "checkpoint": "edit-mode-size-remove-and-save-controls",
        "from": "/images/bacheca/personalizzazione/2.placeholder.svg"
      },
      {
        "checkpoint": "add-widget-modal-expired-certificates-selected",
        "from": "/images/bacheca/personalizzazione/3.placeholder.svg"
      },
      {
        "checkpoint": "added-widget-resized-to-full-width",
        "from": "/images/bacheca/personalizzazione/4.placeholder.svg"
      },
      {
        "checkpoint": "added-widget-and-width-persist-after-reload",
        "from": "/images/bacheca/personalizzazione/5.placeholder.svg"
      },
      {
        "checkpoint": "reader-personal-width-persists-owner-layout-unchanged",
        "from": "/images/bacheca/personalizzazione/11.placeholder.svg"
      }
    ],
    "insert_images": []
  },
  {
    "path": "docs/bacheca.mdx",
    "id": "ridimensionare-i-widget",
    "title": "Ridimensionare i widget",
    "checkpoints": [
      "edit-mode-size-remove-and-save-controls",
      "added-widget-resized-to-full-width",
      "added-widget-and-width-persist-after-reload"
    ],
    "images": [
      {
        "checkpoint": "edit-mode-size-remove-and-save-controls",
        "from": "/images/bacheca/personalizzazione/2.placeholder.svg"
      },
      {
        "checkpoint": "added-widget-resized-to-full-width",
        "from": "/images/bacheca/personalizzazione/4.placeholder.svg"
      },
      {
        "checkpoint": "added-widget-and-width-persist-after-reload",
        "from": "/images/bacheca/personalizzazione/5.placeholder.svg"
      }
    ],
    "insert_images": []
  },
  {
    "path": "docs/bacheca.mdx",
    "id": "aggiungere-un-widget",
    "title": "Aggiungere un widget",
    "checkpoints": [
      "edit-mode-size-remove-and-save-controls",
      "add-widget-modal-expired-certificates-selected",
      "added-widget-and-width-persist-after-reload"
    ],
    "images": [
      {
        "checkpoint": "edit-mode-size-remove-and-save-controls",
        "from": "/images/bacheca/personalizzazione/2.placeholder.svg"
      },
      {
        "checkpoint": "add-widget-modal-expired-certificates-selected",
        "from": "/images/bacheca/personalizzazione/3.placeholder.svg"
      },
      {
        "checkpoint": "added-widget-and-width-persist-after-reload",
        "from": "/images/bacheca/personalizzazione/5.placeholder.svg"
      }
    ],
    "insert_images": []
  },
  {
    "path": "docs/bacheca.mdx",
    "id": "rimuovere-un-widget",
    "title": "Rimuovere un widget",
    "checkpoints": [
      "remove-added-widget-with-trash-control",
      "removed-widget-absent-after-reload"
    ],
    "images": [
      {
        "checkpoint": "remove-added-widget-with-trash-control",
        "from": "/images/bacheca/personalizzazione/6.placeholder.svg"
      },
      {
        "checkpoint": "removed-widget-absent-after-reload",
        "from": "/images/bacheca/personalizzazione/7.placeholder.svg"
      }
    ],
    "insert_images": []
  },
  {
    "path": "docs/bacheca.mdx",
    "id": "ripristinare-il-layout-predefinito",
    "title": "Ripristinare il layout predefinito",
    "checkpoints": [
      "default-restored-before-save",
      "default-layout-persists-after-reload"
    ],
    "images": [
      {
        "checkpoint": "default-restored-before-save",
        "from": "/images/bacheca/personalizzazione/8.placeholder.svg"
      },
      {
        "checkpoint": "default-layout-persists-after-reload",
        "from": "/images/bacheca/personalizzazione/9.placeholder.svg"
      }
    ],
    "insert_images": []
  }
],
 outcome:{field:'dashboard_workflow',expected:dashboardExpectedOutcome}},
 'dashboard-reorder':{version:1,script:'selfhost/tests/browser/manuale/dashboard-reorder.mjs',
 prefix:'images/bacheca/riordinamento/',pages:['docs/bacheca.mdx'],sources:dashboardWorkflowSources,
 dependencies:[...dependencies,'docs/manuale/dashboard-recipes.mjs'],
 checkpoints:[
  {
    "id": "dashboard-reorder-edit-before-drag",
    "caption": "Bacheca in modifica prima del trascinamento."
  },
  {
    "id": "dashboard-reorder-drag-before-save",
    "caption": "Ordine modificato prima del salvataggio."
  },
  {
    "id": "dashboard-reorder-order-persists-after-reload",
    "caption": "Nuovo ordine conservato dopo la ricarica."
  }
],
 sections:[
  {
    "path": "docs/bacheca.mdx",
    "id": "riordinare-i-widget",
    "title": "Riordinare i widget",
    "checkpoints": [
      "dashboard-reorder-edit-before-drag",
      "dashboard-reorder-drag-before-save",
      "dashboard-reorder-order-persists-after-reload"
    ],
    "images": [
      {
        "checkpoint": "dashboard-reorder-edit-before-drag",
        "from": "/images/bacheca/riordinamento/1.placeholder.svg"
      },
      {
        "checkpoint": "dashboard-reorder-drag-before-save",
        "from": "/images/bacheca/riordinamento/2.placeholder.svg"
      },
      {
        "checkpoint": "dashboard-reorder-order-persists-after-reload",
        "from": "/images/bacheca/riordinamento/3.placeholder.svg"
      }
    ],
    "insert_images": []
  }
],
 outcome:{field:'dashboard_reorder',expected:{widgets:8,first_widget:'payments',second_widget:'associates',
 real_mouse_drag:true,unsaved_database_unchanged:true,order_persisted_after_reload:true,sizes_preserved:true,reader_layout_preserved:true}}},
});
