// Initialize the Svelte app and inject it in the DOM
import App from './App.svelte';
import { createIcons, X, Check, Plus, ArrowLeft, ArrowRight, Upload, User, FileText, Info } from 'lucide';
import moment from 'moment';
import Swal from 'sweetalert2';
import {FormValidation} from 'shim/form-validation.js';
// Side-effect imports: install Bootstrap-style data-API delegation (data-toggle="dropdown"/"modal"/"collapse", data-dismiss="modal"/"alert")
import 'shim/dropdown.js';
import 'shim/modal.js';
import 'shim/collapse.js';
import 'shim/alert.js';

// import * as Sentry from '@sentry/svelte';
import fetchIntercept from 'fetch-intercept';

import {downloadFile, downloadCSV, downloadPdf, downloadZip, tryDownloadFile, tryDownloadCSV} from './utils/download.js';
import * as Base64 from './utils/base64.js';

window.moment = moment;
window.Swal = Swal;
window.swal = Swal;
window.FormValidation = FormValidation;
window.downloadFile = downloadFile;
window.downloadCSV = downloadCSV;
window.downloadPdf = downloadPdf;
window.downloadZip = downloadZip;
window.tryDownloadFile = tryDownloadFile;
window.tryDownloadCSV = tryDownloadCSV;
window.Base64 = Base64;

fetchIntercept.register({
    request: function (url, config) {
        // Modify the url or config here
        return [url, config];
    },

    requestError: function (error) {
        // Called when an error occured during another 'request' interceptor call
        return Promise.reject(error);
    },

    response: function (response) {
        // Modify the reponse object
        // if (response.status == 401) {
        //     alert('Unauthorized');
        // }
        // if (response.status == 401) localStorage.removeItem('expires');
        return response;
    },

    responseError: function (error) {
        // Handle an fetch error
        return Promise.reject(error);
    },
});

const replaceContainer = function (Component, options) {
    const frag = document.createDocumentFragment();
    const component = new Component(Object.assign({}, options, {target: frag}));

    options.target.replaceWith(frag);

    return component;
};

// // Initialize the Sentry SDK here
// // if (__bakney.env.DEPLOY_ENV != 'development') {
// Sentry.init({
//     dsn: '',
//     integrations: [
//         Sentry.browserTracingIntegration(),
//         Sentry.replayIntegration({
//             // Additional SDK configuration goes in here, for example:
//             maskAllText: false,
//             blockAllMedia: false,
//         }),
//         // Sentry.replayCanvasIntegration(),
//     ],
//     environment: __bakney.env.DEPLOY_ENV,
//     // debug: true,

//     // Set tracesSampleRate to 1.0 to capture 100%
//     // of transactions for performance monitoring.
//     // We recommend adjusting this value in production
//     // once we reach a stable state.
//     // tracesSampleRate: __bakney.env.DEPLOY_ENV == 'production' ? 0.5 : 1.0,
//     tracesSampleRate: 1.0,

//     // This sets the sample rate to be 10%. You may want this to be 100% while
//     // in development and sample at a lower rate in production
//     // we keep 0.5 until with face stability
//     replaysSessionSampleRate: 0.5,
//     // If the entire session is not sampled, use the below sample rate to sample
//     // sessions when an error occurs.
//     replaysOnErrorSampleRate: 1.0,

//     // tracePropagationTargets: ["localhost"],
// });
// // }

Object.defineProperty(String.prototype, 'capitalize', {
    value: function () {
        return this.charAt(0).toUpperCase() + this.slice(1).toLowerCase();
    },
    enumerable: false,
});

/*
const app = new App({
    target: document.querySelector('#view')
})
*/

// const renderSplashScreenBefore = async () => {
//     await SplashScreen.show({
//         showDuration: 1500,
//         autoHide: true,
//     });
//     return replaceContainer(App, {
//         target: document.querySelector('#view')
//     });
// }
const app = replaceContainer(App, {
    target: document.querySelector('#view'),
});

// Week/time-grid view: show a 1-hour ghost event following the mouse
function initCalendarWeekGhostHover() {
    if (typeof document === 'undefined') return;

    const GHOST_CLASS = 'ec-event ec-ghost ec-hover-ghost';
    let activeGhost = null;
    let activeDay = null;

    function removeGhost() {
        if (activeGhost) {
            activeGhost.remove();
            activeGhost = null;
        }
        activeDay = null;
    }

    document.addEventListener('mousemove', (e) => {
        const dayCell = e.target.closest('.ec-time-grid .ec-day');
        if (!dayCell) {
            removeGhost();
            return;
        }

        if (activeDay !== dayCell) {
            removeGhost();
            activeDay = dayCell;
            dayCell.style.position = 'relative';

            const ghost = document.createElement('div');
            ghost.className = GHOST_CLASS;
            ghost.style.position = 'absolute';
            ghost.style.left = '4px';
            ghost.style.right = '4px';
            ghost.style.pointerEvents = 'none';
            ghost.style.zIndex = '2';
            dayCell.appendChild(ghost);
            activeGhost = ghost;
        }

        const computed = getComputedStyle(dayCell);
        const slotHeight = parseFloat(computed.getPropertyValue('--ec-slot-height')) || 20;
        const periodicity = parseFloat(computed.getPropertyValue('--ec-slot-label-periodicity')) || 2;
        const hourHeight = slotHeight * periodicity;

        const rect = dayCell.getBoundingClientRect();
        const y = e.clientY - rect.top;
        const maxY = Math.max(0, rect.height - hourHeight);
        const snappedY = Math.min(maxY, Math.max(0, Math.floor(y / hourHeight) * hourHeight));

        activeGhost.style.top = snappedY + 'px';
        activeGhost.style.height = hourHeight + 'px';
    });

    document.addEventListener('mouseout', (e) => {
        const dayCell = e.target.closest('.ec-time-grid .ec-day');
        if (dayCell && !dayCell.contains(e.relatedTarget)) {
            removeGhost();
        }
    }, true);
}

initCalendarWeekGhostHover();

createIcons({ icons: { X, Check, Plus, ArrowLeft, ArrowRight, Upload, User, FileText, Info } });

export default app;
