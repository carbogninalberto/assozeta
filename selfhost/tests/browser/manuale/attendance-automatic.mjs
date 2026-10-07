import {initializeActorSession} from './actor-session.mjs';
import {scenario, expect} from './scenario.mjs';
import {setCheckbox} from './controls.mjs';
import manualConfig from '../playwright.manual.config.mjs';
import {attendanceHarness, settleAttendanceCases} from './attendance-task-harness.mjs';
import {automaticAttendanceAuthoredWorkflows} from '../../../../docs/manuale/automatic-attendance-authored-workflows.mjs';
const id = 'attendance-automatic';
const spec = automaticAttendanceAuthoredWorkflows[id];
await scenario({id, prefix: spec.prefix.replace(/\/$/, ''), sources: spec.sources,
 actions: async ({page, api, actor, input, capture, report}) => {
    expect(input.fixture_version).toBe(8); expect(input.fixture_profile).toBe('baseline');
    let data; let athleteContext;
    const errors = []; const failed = [];
    const getSettings = async request => {const r = await request('profile/settings'); expect(r.status()).toBe(200); return (await r.json()).settings;};
    const take = async (currentPage, checkpoint, focus) => {
        const number = spec.checkpoints.findIndex(c => c.id === checkpoint) + 1; expect(number).toBeGreaterThan(0);
        await expect(currentPage.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout:15000});
        await capture(currentPage, number, checkpoint, focus);
    };
    try {
        data = attendanceHarness('prepare', input);
        await settleAttendanceCases(api, data, input, expect);
        const before = await getSettings(api); expect(before.auto_mark_attendance).toBe(false);
        await page.goto(input.origin + '/#/profile?page=settings');
        const setting = page.locator('.form-group').filter({has: page.locator('label').filter({hasText:/^Segna Presenze Automaticamente$/})});
        const toggle = setting.locator('input[type="checkbox"]');
        await expect(toggle).not.toBeChecked();
        await take(page,'automatic-setting-off',setting);
        await setCheckbox(toggle, true);
        expect((await getSettings(api)).auto_mark_attendance).toBe(false);
        await take(page,'automatic-setting-on-before-save',setting);
        const saved = page.waitForResponse(r=>new URL(r.url()).pathname==='/api/profile/settings'&&r.request().method()==='POST');
        await page.locator('#bkn_form_password_update_submit').click(); expect((await saved).status()).toBe(200);
        await page.reload(); await expect(toggle).toBeChecked();
        expect((await getSettings(api)).auto_mark_attendance).toBe(true);
        await take(page,'automatic-setting-on-persists',setting);
        const reader = await actor('reader'); await reader.page.goto(input.origin+'/#/profile?page=settings');
        const readerToggle = reader.page.locator('.form-group').filter({has:reader.page.locator('label').filter({hasText:/^Segna Presenze Automaticamente$/})}).locator('input[type="checkbox"]');
        await expect(readerToggle).toBeDisabled();
        const denied = await reader.api('profile/settings',{method:'POST',data:{...await getSettings(api),auto_mark_attendance:false}});
        expect(denied.status()).toBe(403); expect((await getSettings(api)).auto_mark_attendance).toBe(true);
        report.expected_denials.push({identity:'reader',path:'/api/profile/settings',status:403});
        await take(reader.page,'automatic-setting-reader-disabled');
        // Dedicated genuine athlete session: do not reuse the association-role actor helper.
        athleteContext = await page.context().browser().newContext(manualConfig.use);
        const athleteApi = (route, options={})=>athleteContext.request.fetch(input.origin+'/api/'+route,
            {...options,headers:{Authorization:'Bearer '+data.athlete.token,...options.headers}});
        const profile = await athleteApi('profile/info'); expect(profile.status()).toBe(200);
        const athleteProfile=await profile.json();expect(athleteProfile.info.role).toBe('athlete');
        const athleteUser = athleteProfile.user_data; expect(athleteUser.user_id).toBe(data.athlete_id);
        await athleteContext.addInitScript(initializeActorSession, {identity: data.athlete, input});
        const athletePage=await athleteContext.newPage();
        athletePage.on('pageerror',e=>{errors.push(e.message);(report.athlete_browser_errors ||= []).push({name:e.name,message:e.message,stack:e.stack});});
        athletePage.on('response',r=>{if(r.status()>=400&&r.url().startsWith(input.origin))failed.push({path:new URL(r.url()).pathname,status:r.status()});});
        await athletePage.goto(input.origin+'/#/');
        await expect(athletePage.getByText('Prossime lezioni',{exact:true})).toBeVisible();
        const card = name=>athletePage.locator('.card-widget').filter({has:athletePage.getByText(name,{exact:true})});
        await expect(card('Giulia Bianchi')).toHaveCount(1); await expect(card('Sara Conti')).toHaveCount(1);
        await take(athletePage,'athlete-upcoming-owned-lessons');
        const beforeCancel=attendanceHarness('inspect',input);
        await card('Sara Conti').getByRole('button',{name:'ASSENTE',exact:true}).click();
        const cancelledPopup=athletePage.locator('.swal2-popup');await expect(cancelledPopup).toBeVisible();
        await cancelledPopup.getByRole('button',{name:'Annulla',exact:true}).click();
        await expect(cancelledPopup).not.toBeVisible();
        expect(attendanceHarness('inspect',input)).toEqual(beforeCancel);
        const absent = async (name, value, checkpoint) => {
            const target=card(name); await target.getByRole('button',{name:value?'ASSENTE':'PRESENTE',exact:true}).click();
            const popup=athletePage.locator('.swal2-popup');await expect(popup).toBeVisible();
            if(checkpoint)await take(athletePage,checkpoint,popup);
            const response=athletePage.waitForResponse(r=>new URL(r.url()).pathname===`/api/attendance-day/${data.day_id}/mark-absent`&&r.request().method()==='POST');
            const refreshed=athletePage.waitForResponse(r=>new URL(r.url()).pathname==='/api/statistic/athlete-dashboard'&&r.request().method()==='GET');
            await popup.getByRole('button',{name:value?'Sarò assente':'Sarò presente',exact:true}).click();
            const saved = await response; expect(saved.status()).toBe(200);
            const dashboard=await refreshed;expect(dashboard.status()).toBe(200);
            const dashboardData=await dashboard.json();
            const persisted = attendanceHarness('inspect',input);
            const enrollment = data.cases.find(item => item.name === name).enrollment_id;
            report.athlete_absence_writes ??= [];
            report.athlete_absence_writes.push({name, absent:value, request:saved.request().postDataJSON(), response:await saved.json(),
                persisted_absent:persisted.expected_absences.some(item => item.course_subscription_id === enrollment)});
            expect(persisted.expected_absences.some(item => item.course_subscription_id === enrollment)).toBe(value);
            const lesson=dashboardData.upcoming_lessons.find(item=>item.course_subscription_id===enrollment);
            report.athlete_absence_writes.at(-1).dashboard_lesson=lesson;
            expect(lesson.is_absent).toBe(value);
            await expect(target.getByRole('button',{name:value?'ASSENTE':'PRESENTE',exact:true})).toHaveClass(value?/btn-danger/:/btn-success/);
        };
        await absent('Sara Conti',true,'athlete-absence-confirmation');
        await athletePage.reload();
        await expect(card('Sara Conti').getByRole('button',{name:'ASSENTE',exact:true})).toHaveClass(/btn-danger/);
        await take(athletePage,'athlete-absence-persists');
        await absent('Giulia Bianchi',true); await absent('Giulia Bianchi',false);
        await take(athletePage,'athlete-presence-correction');
        const beforeWorker=attendanceHarness('inspect',input);
        const foreignPerson=data.cases.find(c=>c.key==='unpaid');
        const forbidden=await athleteApi(`attendance-day/${data.day_id}/mark-absent`,{method:'POST',data:{course_subscription_id:foreignPerson.enrollment_id,absent:true}});
        expect(forbidden.status()).toBe(403); expect(attendanceHarness('inspect',input)).toEqual(beforeWorker);
        report.expected_denials.push({identity:'athlete',path:`/api/attendance-day/${data.day_id}/mark-absent`,status:403});
        const result=attendanceHarness('run-worker',input);
        expect(result.auto_marked).toBe(true); expect(result.attendees).toHaveLength(4);
        const again=attendanceHarness('run-worker',input);expect(again).toEqual(result);
        await page.goto(input.origin+`/#/course/overview/${data.course_id}/attendance`);await page.reload();
        await expect(page.locator('#bkn_content').getByText('Lezione automatica di ginnastica',{exact:true})).toBeVisible();
        await page.locator(`a[data-target="#attendance-day-${data.day_id}"]`).click();
        const modal=page.locator(`#attendance-day-${data.day_id}`);await expect(modal).toBeVisible();
        await expect(modal.locator('input[type="checkbox"]:checked')).toHaveCount(4);
        await take(page,'automatic-register-four-real-presences',modal.locator('.modal-content'));
        await modal.getByText('Chiudi',{exact:true}).click();
        for(const key of ['paid','absence']) {
            const member=data.cases.find(c=>c.key===key);const carnet=member.carnets[0];
            await page.goto(input.origin+`/#/course/carnet/list/detail/${carnet.carnet_id}/usage`);await page.reload();
            const row=page.locator('[data-row]').filter({hasText:member.name});
            await expect(row).toContainText(key==='paid'?'4/5':'5/5');
            await take(page,key==='paid'?'automatic-carnet-consumed-once':'automatic-absence-preserves-carnet',row);
        }
        await page.goto(input.origin+'/#/profile?page=settings');await expect(toggle).toBeChecked();
        await setCheckbox(toggle, false);
        const disabledSaved=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/profile/settings'&&r.request().method()==='POST');
        await page.locator('#bkn_form_password_update_submit').click();expect((await disabledSaved).status()).toBe(200);
        await page.reload();await expect(toggle).not.toBeChecked();expect((await getSettings(api)).auto_mark_attendance).toBe(false);
        await take(page,'automatic-setting-disabled-persists',setting);
        expect(errors).toEqual([]);expect(failed).toEqual([]);
        report.automatic_attendance={settings_saved_reloaded:true,reader_denied:true,athlete_owned_lessons:2,
            athlete_absence_saved:true,absence_cancel_preserves_state:true,athlete_presence_corrected:true,athlete_other_member_denied:true,
            real_task_invoked:true,automatic_participants:4,paid_carnet_balance:4,absence_carnet_balance:5,
            unpaid_carnet_unchanged:true,exhausted_not_present:true,disabled_not_consumed:true,
            multiple_lowest_balance_consumed:true,unpaid_lowest_blocks_paid_alternative:true,
            no_carnet_present:true,repeat_task_preserves_state:true,disable_saved_reloaded:true};
    } finally {
        if(athleteContext)await athleteContext.close();
        if(data){attendanceHarness('cleanup',input);report.cleanup_policy={status:'owned-command-cleaned',scope:'manifest-recorded-case-ids'};}
    }
 }});
