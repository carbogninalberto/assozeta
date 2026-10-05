import {scenario, expect} from './scenario.mjs';
import {attendanceHarness,settleAttendanceCases} from './attendance-task-harness.mjs';
import {automaticAttendanceAuthoredWorkflows} from '../../../../docs/manuale/automatic-attendance-authored-workflows.mjs';
const id='attendance-carnet-variants';const spec=automaticAttendanceAuthoredWorkflows[id];
await scenario({id,prefix:spec.prefix.replace(/\/$/,''),sources:spec.sources,
 actions:async({page,api,actor,input,capture,report})=>{
    expect(input.fixture_version).toBe(8);expect(input.fixture_profile).toBe('baseline');let data;
    const take=async(checkpoint,focus,toast=false)=>{
        const index=spec.checkpoints.findIndex(c=>c.id===checkpoint);expect(index).toBeGreaterThanOrEqual(0);
        if(!toast)await expect(page.locator('[data-sonner-toast]:visible')).toHaveCount(0,{timeout:15000});
        await capture(page,index+1,checkpoint,toast?undefined:focus);
    };
    try {
        data=attendanceHarness('prepare',input);await settleAttendanceCases(api,data,input,expect);
        const initial=attendanceHarness('inspect',input);
        await page.goto(input.origin+`/#/course/overview/${data.course_id}/attendance`);
        await page.locator(`a[data-target="#attendance-day-${data.day_id}"]`).click();
        const modal=page.locator(`#attendance-day-${data.day_id}`);await expect(modal).toBeVisible();
        const item=key=>{const member=data.cases.find(c=>c.key===key);const [first,last]=member.name.split(' ');
            return modal.locator('.list-item-attendees').filter({hasText:new RegExp(last+'\\s+'+first)});};
        await expect(modal.locator('.list-item-attendees')).toHaveCount(8);
        await take('carnet-variants-eight-owned-enrollments',modal.locator('.modal-content'));
        const route=`/api/course/${data.course_id}/attendees/${data.day_id}/update`;
        const update=async(key,checked,status,checkpoint,message)=>{
            const checkbox=item(key).locator('input[type="checkbox"]');const before=attendanceHarness('inspect',input);
            const response=page.waitForResponse(r=>new URL(r.url()).pathname===route&&r.request().method()==='POST');
            await checkbox.locator('..').click();const result=await response;expect(result.status()).toBe(status);
            if(status===412){
                await expect(checkbox).not.toBeChecked();
                await expect(page.getByText(message,{exact:true})).toBeVisible();
                // Only this actual, asserted response is eligible to consume a page failure.
                report.expected_denials.push({path:route,method:'POST',status:412,identity:'owner',observed_browser:true});
                await take(checkpoint,modal.locator('.modal-content'),true);
                expect(attendanceHarness('inspect',input)).toEqual(before);
            } else {
                if(checked)await expect(checkbox).toBeChecked();else await expect(checkbox).not.toBeChecked();
                if(checkpoint)await take(checkpoint,modal.locator('.modal-content'));
            }
        };
        await update('unpaid',true,412,'carnet-unpaid-real-ui-refusal','Il carnet non è ancora stato pagato.');
        await update('exhausted',true,412,'carnet-exhausted-real-ui-refusal','Carnet finito.');
        await update('unpaid-priority',true,412,'carnet-lowest-unpaid-blocks-alternative','Il carnet non è ancora stato pagato.');
        await update('multiple',true,200,'carnet-multiple-real-presence');
        let current=attendanceHarness('inspect',input);
        expect(current.cases.multiple.map(c=>c.balance)).toEqual([1,5]);
        expect(current.cases.multiple[0].usage).toHaveLength(1);expect(current.cases.multiple[1].usage).toEqual([]);
        await update('multiple',false,200,'carnet-multiple-removal-restores');
        expect(attendanceHarness('inspect',input)).toEqual(initial);
        await update('disabled',true,200,'carnet-disabled-presence-without-consumption');
        current=attendanceHarness('inspect',input);expect(current.cases.disabled).toEqual(initial.cases.disabled);
        expect(current.attendees).toEqual([{course_subscription_id:data.cases.find(c=>c.key==='disabled').enrollment_id}]);
        await update('disabled',false,200);
        await update('no-carnet',true,200,'carnet-no-package-presence');
        expect(attendanceHarness('inspect',input).attendees).toEqual([{course_subscription_id:data.cases.find(c=>c.key==='no-carnet').enrollment_id}]);
        await update('no-carnet',false,200);
        expect(attendanceHarness('inspect',input)).toEqual(initial);
        await modal.getByText('Chiudi',{exact:true}).click();
        const reader=await actor('reader');
        const denied=await reader.api(`course/${data.course_id}/attendees/${data.day_id}/update`,{method:'POST',data:{attendees:[]}});
        expect(denied.status()).toBe(403);expect(attendanceHarness('inspect',input)).toEqual(initial);
        report.expected_denials.push({identity:'reader',path:route,status:403});
        await page.reload();await page.locator(`a[data-target="#attendance-day-${data.day_id}"]`).click();
        await expect(modal.locator('input[type="checkbox"]:checked')).toHaveCount(0);
        await take('carnet-variants-no-residual-consumptions',modal.locator('.modal-content'));
        report.carnet_variants={unpaid_ui_denied:true,exhausted_ui_denied:true,lowest_unpaid_blocks_paid_alternative:true,
            lowest_positive_balance_consumed:true,other_assignment_unchanged:true,removal_restores_original_state:true,
            disabled_assignment_not_consumed:true,no_carnet_presence_saved:true,reader_denied:true,
            reload_preserves_final_state:true,receipt_or_email_requested:false};
    } finally {if(data){attendanceHarness('cleanup',input);report.cleanup_policy={status:'owned-command-cleaned',scope:'manifest-recorded-case-ids'};}}
 }});
