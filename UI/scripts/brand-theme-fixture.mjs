import {createServer} from 'vite';
import {svelte} from '@sveltejs/vite-plugin-svelte';
import {fileURLToPath} from 'node:url';
const root = fileURLToPath(new URL('../', import.meta.url));
let color = '#087e54';
const config = () => ({oem: {name: 'Brand test', abbreviation: 'BT', primaryColor: color, supportEmail: 'test@example.test'}, features: {selfHosted: true}});
const server = await createServer({root, configFile: false, logLevel: 'error',
    resolve: {alias: Object.fromEntries(['store', 'utils', 'components', 'routes', 'shim'].map(name => [name, `${root}/src/${name}`]))},
    plugins: [{name: 'branding-fixture', enforce: 'pre',
        resolveId(id) {
            if (/utils\/ApiMiddleware(?:\.js)?$/.test(id)) return '\0brand-api';
            if (id === '/brand-entry.js') return '\0brand-entry';
        },
        load(id) {
            if (id === '\0brand-api') return `export const originalFetch = window.fetch.bind(window); export const replaceUID = (url, id) => url.replace('{id}', id); export async function apiFetch(url, options={}) { const res = await fetch(url, {...options, headers:{'Content-Type':'application/json'}}); return {error:!res.ok, status:res.status, response:await res.json()}; }`;
            if (id === '\0brand-entry') return `
                import SelfInstance from '/src/routes/profile/sections/SelfInstance.svelte';
                import SinglePayment from '/src/routes/stripe/Checkout.svelte';
                import CartPayment from '/src/routes/stripe/CartCheckout.svelte';
                import Card from '/src/routes/association/Members/detail/sections/subcomponents/Tessera.svelte';
                import SetupWizard from '/src/routes/setup/SetupWizard.svelte';
                import {params} from 'svelte-spa-router';
                params.set({});
                import Associates from '/src/components/widgets/Associates.svelte';
                import BestCourses from '/src/components/widgets/BestCourses.svelte';
                import Subscriptions from '/src/components/widgets/Subscriptions.svelte';
                import * as theme from '/src/utils/BrandTheme.js';
                import * as store from '/src/store/instanceStore.js';
                import * as echarts from 'echarts/core';
                import Chart from 'chart.js/auto';
                window.brandTest = {theme, store, echarts, Chart};
                window.brandTest.mountSetup = () => new SetupWizard({target:document.getElementById('extra')});
                window.brandTest.mountEditor = async () => {
                    const {mountEmailBuilder} = await import('/src/components/inputs/email-builder/emailbuilder.js');
                    window.brandTest.disposeEditor = mountEmailBuilder(document.getElementById('extra'));
                    window.addEventListener('save', event => window.savedEmail = event.detail);
                };
                window.brandTest.mountPayment = async (cart=false) => {
                    window.__bakney.STRIPE_KEY = 'pk_test_fixture';
                    window.__bakney.env.API.PAYMENT = {INFO:'/api/payment/{id}'};
                    window.__bakney.env.API.STRIPE = {PAY:'/api/intent/{id}',MULTIPLE_PAY:'/api/intent/cart'};
                    window.paymentUpdates=[];
                    window.Stripe = () => ({elements(options) {
                        window.paymentOptions=options;
                        return {update(value){window.paymentUpdates.push(value);},create(){return {mount(selector){window.paymentMounted=!!document.querySelector(selector);},on(){}};}};
                    }});
                    const Component = cart ? CartPayment : SinglePayment;
                    const component = new Component({target:document.getElementById('extra'),props:{id:'fixture',inModal:true}});
                    window.brandTest.destroyExtra=()=>component.$destroy();
                };
                window.brandTest.mountCard = async () => {
                    const Component = Card;
                    const component = new Component({target:document.getElementById('extra'),props:{preview:true,showQRCode:false,member:{associate:{first_name:'Test',last_name:'Member'},sport_association:{denomination:'Club'},start_date:'2026-01-01',end_date:'2026-12-31'}}});
                    window.brandTest.setCardColor=color=>component.$set({color});
                };
                await store.loadInstanceConfig();
                new SelfInstance({target: document.getElementById('settings')});
                new Associates({target: document.getElementById('associates')});
                new BestCourses({target: document.getElementById('courses')});
                new Subscriptions({target: document.getElementById('subscriptions')});
                window.brandTest.ready = true;
            `;
        },
        configureServer(server) {
            server.middlewares.use(async (req,res,next) => {
                if(req.url === '/') {
                    res.setHeader('Content-Type','text/html');
                    return res.end(await server.transformIndexHtml('/', `<!doctype html><html><head><meta name="theme-color" content="#333333"><link rel="stylesheet" href="/static/css/bootstrap.min.css"><link rel="stylesheet" href="/static/css/app-bundle.css"><link rel="stylesheet" href="/global.css"><link rel="stylesheet" href="/dark-mode.css"><link rel="stylesheet" href="/brand.css"><style>body{padding:24px}#samples{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:30px}.chart{width:350px;height:300px;display:inline-block}#settings{max-width:1100px}</style></head><body>
                    <script>window.__bakney={build:{VERSION:'test'},OEM_CONFIG:{selfHosted:true},env:{HOST:'/api',API_HOST:'/api',API:{STATISTIC:{DASHBOARD:'/api/stats'}}}};</script>
                    <div id="samples"><button id="primary" class="btn btn-primary">Primary <i>Icon</i></button><button id="disabled" class="btn btn-primary" disabled>Disabled</button><button id="outline" class="btn btn-outline-primary">Outline</button><button id="light" class="btn btn-light-primary">Light</button><span id="background" class="bg-primary">Background</span><a id="link" href="#" class="text-primary">Link</a><span id="badge" class="badge badge-primary">Badge</span><span id="alert" class="alert alert-primary">Alert</span><label class="checkbox checkbox-primary"><input checked type="checkbox"><span id="check"></span></label><input id="input" class="form-control"><ul class="nav nav-tabs"><li><a id="tab" class="nav-link active">Tab</a></li></ul><button id="dialog" class="swal2-styled swal2-confirm">Confirm</button><span id="filter" class="active-filter">Filter</span></div>
                    <div id="associates" class="chart"></div><div id="courses" class="chart"></div><div id="subscriptions" class="chart"></div><div id="settings"></div><div id="extra"></div><script type="module" src="/brand-entry.js"></script></body></html>`));
                }
                if(!req.url.startsWith('/api/')) return next();
                let raw='';for await(const chunk of req)raw+=chunk;
                const body=raw?JSON.parse(raw):{};
                res.setHeader('Content-Type','application/json');
                const send=value=>res.end(JSON.stringify(value));
                if(req.url==='/api/instance/setup-token/validate')return send({valid:true});
                if(req.url==='/api/instance/status')return send({configured:true});
                if(req.url==='/api/instance/config')return send(config());
                if(req.url==='/api/test-color'){color=body.color;return send(config());}
                if(req.url==='/api/instance/admin'){
                    if(req.method==='PUT')color=body.oem.primaryColor;
                    return send({config:config(),mode:'production',running_version:'v1.2.3',configured_version:'v1.2.3'});
                }
                if(req.url.startsWith('/api/intent/'))return send({data:{client_secret:'fixture_secret',status:'pending',info:{amount:'10.00'}}});
                if(req.url.startsWith('/api/payment/'))return send({data:{subject:0,amount:'10.00',meta:{},info:{}}});
                if(req.url.startsWith('/api/stats'))return send({data:{current_month_associates:[1,2,3],total_associates:6,current_week_course_associates:[2,1,3],best_courses:[],total_course_associates:6,pie_subscriptions:[1,2,3,4],total_subscriptions:10}});
                if(req.url==='/api/instance/admin/updates')return send({available:true,active:null,history:[]});
                if(req.url.startsWith('/api/instance/admin/releases'))return send({relation:'current',history:[],pending:[],latest:null});
                if(req.url==='/api/instance/admin/diagnostics')return send({overall:'passed',checks:[],integrations:[]});
                if(req.url==='/api/instance/admin/email')return send({host:'',source:'environment',revision:0});
                if(req.url.startsWith('/api/instance/admin/integrations/'))return send({enabled:false,source:'environment',revision:0});
                res.statusCode=404;return send({error:req.url});
            });
        },
    },svelte({configFile:false})],server:{host:'127.0.0.1',port:5194,strictPort:true},
});
await server.listen();
