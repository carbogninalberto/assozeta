// Disposable seed8 actor and pending invite only. Never delete the baseline reader.
// Accepted-invite deletion and email delivery/acceptance are explicitly outside this proof.
import {scenario, expect} from './scenario.mjs';
import {organizationMaintenanceAuthoredWorkflows} from '../../../../docs/manuale/organization-maintenance-authored-workflows.mjs';
const spec = organizationMaintenanceAuthoredWorkflows['collaborator-removal'];
await scenario({id: 'collaborator-removal', prefix: spec.prefix.replace(/\/$/, ''), sources: spec.sources,
    actions: async ({page, api, open, actor, input, capture, report}) => {
        expect(input.fixture_version).toBe(8);
        expect(input.fixture_profile).toBe('collaborator-removal');
        const removableId = input.identities.removable.user_id, inviteId = input.removable_invite_id;
        expect(removableId).not.toBe(input.identities.reader.user_id);
        expect(removableId).not.toBe(input.user_id);
        const json = async (route, client = api) => {
            const response = await client(route); expect(response.status()).toBe(200); return response.json();
        };
        // Invite tokens are never included in comparisons or diagnostic assertion diffs.
        const list = async () => (await json('collaborators/list')).map(({token, ...row}) => row).sort((a, b) => (a.user_id || a.collaboration_invite_id).localeCompare(b.user_id || b.collaboration_invite_id));
        const initial = await list(), baselineReader = initial.find(row => row.user_id === input.identities.reader.user_id);
        const removable = initial.find(row => row.user_id === removableId), pending = initial.find(row => row.collaboration_invite_id === inviteId);
        expect(removable.first_name).toBe('Paolo'); expect(removable.last_name).toBe('Testa');
        expect(removable.email).toBe('temporaneo@aurora.example.test'); expect(removable.is_invite).toBe(false);
        expect(pending.email).toBe('invito.temporaneo@aurora.example.test'); expect(pending.is_invite).toBe(true);
        expect(pending.accepted).toBe(false);
        const members = async () => Object.values((await json('subscription/list?pagination[perpage]=100')).data).sort((a, b) => a.subscription_id.localeCompare(b.subscription_id));
        const originalMembers = await members();
        const payments = async () => (await json('payment/list?mode=compressed')).data.sort((a, b) => a.payment_id.localeCompare(b.payment_id));
        const originalPayments = await payments();
        const profileBefore = (await json('profile/info')).user_data;
        // Same previously issued authentication credential is reused after UI removal.
        const removedSession = route => api(route, {headers: {Authorization: `Bearer ${input.identities.removable.token}`}});
        expect((await json('profile/info', removedSession)).user_data.user_id).toBe(removableId);
        let invitationRequests = 0;
        page.on('request', request => {
            if (new URL(request.url()).pathname === '/api/collaborators/add' && request.method() === 'POST') invitationRequests++;
        });
        const take = async (number, checkpoint, target = page, locator) => {
            await expect(target.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 10000});
            await capture(target, number, checkpoint, locator);
        };
        // Icon delete buttons lose their accessible name once tooltips move title to data-original-title.
        const row = (target, email) => target.locator('[data-row]').filter({has: target.getByText(email, {exact:true})});
        await open('Collaboratori', '/#/connected-collaborators');
        await expect(row(page, removable.email)).toContainText('PAOLO TESTA');
        await expect(row(page, removable.email)).toContainText('Accettato');
        await expect(row(page, pending.email)).toContainText('In attesa');
        await take(1, 'removable-accepted-account-and-separate-pending-invite');
        const reader = await actor('reader');
        await reader.open('Collaboratori', '/#/connected-collaborators');
        await expect(row(reader.page, removable.email).locator('button:is([title="Elimina"],[data-original-title="Elimina"])')).toBeDisabled();
        await expect(row(reader.page, pending.email).locator('button:is([title="Elimina"],[data-original-title="Elimina"])')).toBeDisabled();
        const deniedAccount = await reader.api(`collaborators/${removableId}/delete`, {method: 'DELETE'});
        const deniedInvite = await reader.api(`collaborators/${inviteId}/delete`, {method: 'DELETE'});
        expect(deniedAccount.status()).toBe(403); expect(deniedInvite.status()).toBe(403);
        expect(await list()).toEqual(initial);
        await take(2, 'reader-cannot-delete-account-or-invite', reader.page);
        await row(page, removable.email).locator('button:is([title="Elimina"],[data-original-title="Elimina"])').click();
        await expect(page.getByText('Vuoi eliminare il collaboratore?', {exact: true})).toBeVisible();
        await take(3, 'account-removal-confirmation-and-cancel', page, page.locator('.swal2-popup'));
        await page.getByRole('button', {name: 'Annulla', exact: true}).click();
        expect(await list()).toEqual(initial);
        expect((await json('profile/info', removedSession)).user_data.user_id).toBe(removableId);
        const removeThroughUi = async (email, uid) => {
            await row(page, email).locator('button:is([title="Elimina"],[data-original-title="Elimina"])').click();
            await expect(page.getByText('Vuoi eliminare il collaboratore?', {exact: true})).toBeVisible();
            const deleted = page.waitForResponse(response => new URL(response.url()).pathname === `/api/collaborators/${uid}/delete`
                && response.request().method() === 'DELETE');
            await page.getByRole('button', {name: 'Elimina', exact: true}).click();
            expect((await deleted).status()).toBe(200);
            await expect(row(page, email)).toHaveCount(0);
            await page.reload(); await expect(row(page, email)).toHaveCount(0);
        };
        await removeThroughUi(removable.email, removableId);
        const afterAccount = await list();
        expect(afterAccount.some(item => item.user_id === removableId)).toBe(false);
        expect(afterAccount.find(item => item.collaboration_invite_id === inviteId)).toEqual(pending);
        expect(afterAccount.find(item => item.user_id === input.identities.reader.user_id)).toEqual(baselineReader);
        await take(4, 'removed-account-absent-after-reload-invite-preserved');
        const removedTokenProfile = await removedSession('profile/info');
        const removedTokenMembers = await removedSession('subscription/list');
        expect(removedTokenProfile.status()).toBe(401); expect(removedTokenMembers.status()).toBe(401);
        expect((await json('profile/info', reader.api)).user_data.user_id).toBe(input.identities.reader.user_id);
        // Pending invite is a different row and identifier; do not demonstrate accepted-invite deletion.
        await row(page, pending.email).locator('button:is([title="Elimina"],[data-original-title="Elimina"])').click();
        await expect(page.getByText('Vuoi eliminare il collaboratore?', {exact: true})).toBeVisible();
        await take(5, 'pending-invite-removal-confirmation', page, page.locator('.swal2-popup'));
        await page.getByRole('button', {name: 'Annulla', exact: true}).click();
        expect((await list()).find(item => item.collaboration_invite_id === inviteId)).toEqual(pending);
        await removeThroughUi(pending.email, inviteId);
        const final = await list();
        expect(final).toEqual(initial.filter(item => item.user_id !== removableId && item.collaboration_invite_id !== inviteId));
        await take(6, 'pending-invite-absent-after-reload-baseline-reader-preserved');
        expect(await members()).toEqual(originalMembers);
        expect(await payments()).toEqual(originalPayments);
        expect((await json('profile/info')).user_data).toEqual(profileBefore);
        expect(invitationRequests).toBe(0);
        report.collaborator_removal_authored_workflow = {
            dedicated_fixture_only: true, account_cancel_preserved_session_and_list: true,
            accepted_account_deleted_after_reload: true, removed_existing_token_profile_status: removedTokenProfile.status(),
            removed_existing_token_members_status: removedTokenMembers.status(), pending_invite_preserved_after_account_deletion: true,
            pending_invite_cancel_preserved_record: true, pending_invite_deleted_after_reload: true,
            reader_delete_account_status: deniedAccount.status(), reader_delete_invite_status: deniedInvite.status(),
            baseline_reader_session_preserved: true, baseline_members_payments_owner_preserved: true,
            disposable_actor_and_invite_absent: true, invitation_dispatches: invitationRequests,
        };
        report.external_gaps = [{operation: 'accepted-invite-deletion', status: 'needs_external_verification',
            reason: 'Only direct linked-user deletion and unaccepted fixture invite deletion were exercised; accepted invite deletion is a distinct branch.'},
            {operation: 'collaborator-invitation-delivery-and-acceptance', status: 'needs_external_verification',
                reason: 'Disposable records were seeded locally; no invitation email or acceptance was sent/executed.'}];
        report.checks = ['owner UI identifies distinct accepted account and unaccepted invite', 'reader UI/delete denials preserve both records',
            'account cancellation retains existing token; confirmed DELETE persists absence after reload',
            'the same issued deleted-user token is denied for profile and membership APIs',
            'pending invite cancellation/DELETE preserve unrelated baseline reader', 'only two scoped disposable fixtures removed; no email dispatched'];
    }});
