import {scenario,expect} from './scenario.mjs';
import {automaticAttendanceAuthoredWorkflows} from '../../../../docs/manuale/automatic-attendance-authored-workflows.mjs';
const id='dashboard-display';const spec=automaticAttendanceAuthoredWorkflows[id];
await scenario({id,prefix:spec.prefix.replace(/\/$/,''),sources:spec.sources,
 actions:async({page,api,input,capture,report})=>{
    expect(input.fixture_version).toBe(8);expect(input.fixture_profile).toBe('baseline');
    const payloads={};const read=async widget=>{const r=await api('statistic/dashboard?widget='+widget);expect(r.status()).toBe(200);return (await r.json()).data;};
    await expect(page.locator('.dashboard-widget')).toHaveCount(8);
    await expect(page.locator('.dashboard-widget').getByText('...',{exact:true})).toHaveCount(0);
    await expect(page.locator('#today-lessons-widget')).toContainText('Nessuna lezione oggi');
    await capture(page,12,'dashboard-display-initial-eight-widgets');
    await page.getByRole('button',{name:'Modifica',exact:true}).click();
    for(const title of ['Carnet in esaurimento','Entrate e spese','Pagamenti scaduti o in scadenza oggi','Certificati medici scaduti']) {
        await page.getByRole('button',{name:'Aggiungi widget',exact:true}).click();const modal=page.locator('#addWidget');
        await expect(modal).toBeVisible();
        if(title==='Carnet in esaurimento'){await expect(modal.locator('.cursor-pointer')).toHaveCount(4);await capture(page,14,'dashboard-display-four-available-widgets',modal.locator('.modal-content'));}
        await modal.locator('.cursor-pointer').filter({hasText:title}).click();
        await modal.getByRole('button',{name:'Aggiungi Widget',exact:true,includeHidden:true}).click();await expect(modal).not.toBeVisible();
    }
    const saved=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/statistic/dashboard/layout'&&r.request().method()==='POST');
    await page.getByRole('button',{name:'Salva',exact:true}).click();expect((await saved).status()).toBe(200);await page.reload();
    await expect(page.locator('.dashboard-widget')).toHaveCount(12);
    await capture(page,13,'dashboard-display-twelve-widgets-after-reload');
    const card=title=>page.locator('.dashboard-widget').filter({has:page.locator('.card-label').filter({hasText:new RegExp('^'+title+'$')})});
    const currency=value=>new Intl.NumberFormat('it-IT',{style:'currency',currency:'EUR'}).format(Number(value||0));
    const take=async(widget,title,checkpoint)=>{
        payloads[widget]=await read(widget);const target=card(title);await expect(target).toHaveCount(1);
        await expect(target.getByText('...',{exact:true})).toHaveCount(0);
        const index=spec.checkpoints.findIndex(c=>c.id===checkpoint);expect(index).toBeGreaterThanOrEqual(0);
        await expect(page.locator('[data-sonner-toast]:visible')).toHaveCount(0,{timeout:15000});
        if(widget==='incomeAndExpenses') {
            // The fixed edit button can cover the last expense amount at the viewport bottom.
            await target.evaluate(node=>node.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'}));
            await expect(target).toBeInViewport({ratio:1});
            const edit=page.getByRole('button',{name:'Modifica',exact:true});
            await expect.poll(async()=>{
                const [widgetBox,editBox]=await Promise.all([target.boundingBox(),edit.boundingBox()]);
                return Boolean(widgetBox&&editBox&&widgetBox.y+widgetBox.height+16<=editBox.y);
            },{message:'Payment summary must be fully above the fixed edit control'}).toBe(true);
        }
        await capture(page,index+1,checkpoint,target);return {data:payloads[widget],target};
    };
    const associates=await take('associates','Iscrizioni','dashboard-new-subscriptions-visible');
    expect(associates.data.total_associates).toBe(3);await expect(associates.target.getByText('+3',{exact:true})).toBeVisible();
    await expect(associates.target.locator('#bkn_dashboard_widget_associates canvas')).toBeVisible();
    const payments=await take('payments','Pagamenti incassati','dashboard-paid-amount-visible');
    expect(Number(payments.data.total_payments)).toBe(50);await expect(payments.target.getByText(currency(50),{exact:true})).toBeVisible();
    const subs=await take('subscriptions','Tesserati anno corrente','dashboard-current-membership-states-visible');
    expect(subs.data.total_subscriptions).toBe(3);expect(subs.data.pie_subscriptions).toEqual([0,0,0,3]);
    await expect(subs.target.getByText('Accettate',{exact:false})).toContainText('(3)');
    const courses=await take('bestcourses','I 3 corsi con più iscritti','dashboard-active-course-rank-visible');
    expect(courses.data.best_courses).toHaveLength(1);expect(courses.data.best_courses[0].subscriptions).toBe(0);
    await expect(courses.target.getByRole('link',{name:'Ginnastica per tutti',exact:true})).toBeVisible();
    await expect(courses.target.locator('b').filter({hasText:/^0$/})).toBeVisible();
    const income=await take('incomeAndExpenses','Riepilogo Pagamenti','dashboard-income-expense-periods-visible');
    for(const key of ['today_income','last_month_income','total_income'])expect(Number(income.data[key])).toBe(50);
    for(const key of ['today_expenses','last_month_expenses','total_expenses'])expect(Number(income.data[key]||0)).toBe(0);
    await expect(income.target.getByText(currency(50),{exact:true})).toHaveCount(3);
    await expect(income.target.getByText(currency(0),{exact:true})).toHaveCount(3);
    const empties=[['todaylessons','Registro presenze lezioni','Nessuna lezione oggi','lessons'],
        ['expiringcarnets','Carnet in esaurimento','Nessun carnet in scadenza','expiring_carnets'],
        ['subscriptionstoapprove','Iscrizioni da approvare','Nessun socio da approvare','subscriptions_to_approve'],
        ['expiringmedicalcertificates','Certificati medici in scadenza','Nessun certificato medico in scadenza','expiring_medical_certificates'],
        ['expiredPayments','Pagamenti scaduti e in scadenza','Nessun pagamento scaduto','expired_payments'],
        ['expiredmedicalcertificates','Certificati medici scaduti','Nessun certificato medico in scadenza','expired_medical_certificates']];
    for(const [widget,title,message,key]of empties) {
        const {data,target}=await take(widget,title,'dashboard-empty-'+widget);
        expect(data[key]).toEqual([]);await expect(target.getByText(message,{exact:true})).toBeVisible();
    }
    report.dashboard_display={visible_widgets:12,new_subscriptions:3,paid_amount:50,accepted_current_subscriptions:3,
        active_courses_ranked:1,course_enrollments:0,income_each_period:50,expenses_each_period:0,
        empty_lists_match_real_api:6,persisted_layout_after_reload:true};
 }});
