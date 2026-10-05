// Transfer only the explicitly seeded association-owned subscription to its fixture recipient.
import {scenario, expect} from './scenario.mjs';
import {memberAuthoredWorkflows} from '../../../../docs/manuale/member-authored-workflows.mjs';
const id = 'members-account-access', spec = memberAuthoredWorkflows[id];
await scenario({id, prefix: spec.prefix.replace(/\/$/, ''), sources: spec.sources,
    actions: async ({page, api, open, actor, context, input, capture, report}) => {
        expect(input.fixture_profile).toBe('member-transfer');
        const recipient = input.identities.recipient;
        expect(recipient.user_id).not.toBe(input.user_id); expect(recipient.email).toMatch(/@aurora\.example\.test$/);
        const json = async (route, request = api) => {const response = await request(route);
            expect(response.status()).toBe(200); return response.json();};
        const uid = input.subscription_ids[0];
        const member = () => json(`subscription/${uid}/info`).then(value => value.data.info);
        // The info endpoint deliberately returns a short user profile. The
        // subscription list supplies the persisted ownership UUID.
        const ownership = async (request = api, identityRole = 'association') => {
            const rows = Object.values((await json('subscription/list?pagination[perpage]=100', request)).data);
            const row = rows.find(item => item.subscription_id === uid);
            expect(row).toBeTruthy();
            if (identityRole === 'athlete') {
                // The athlete list exposes the persisted FK as a UUID, while
                // the association list embeds the optimized owner profile.
                expect(typeof row.user).toBe('string');
                return row.user;
            }
            expect(typeof row.user).toBe('object');
            return row.user.user_id;
        };
        const initial = await member(), personaId = initial.associate.associate_id;
        expect(initial.associate.first_name).toBe('Giulia'); expect(await ownership()).toBe(input.user_id);
        const otherMembers = async () => Object.values((await json('subscription/list?pagination[perpage]=100')).data)
            .filter(row => row.subscription_id !== uid).sort((a,b) => a.subscription_id.localeCompare(b.subscription_id));
        const payments = async () => Object.values((await json('payment/list?pagination[perpage]=100')).data)
            .sort((a,b) => a.payment_id.localeCompare(b.payment_id));
        const originals = {members: await otherMembers(), payments: await payments()};
        const facts = {}, proof = (key, value) => {expect(value, key).toEqual(spec.outcome.expected[key]); facts[key] = value;};
        const take = async (checkpoint, browserPage = page, locator) => {
            await expect(browserPage.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 15000});
            const n = spec.checkpoints.findIndex(item => item.id === checkpoint) + 1;
            await capture(browserPage, n, checkpoint, locator);
        };
        const select = async (browserPage = page) => {
            const row = browserPage.locator('[data-row]').filter({hasText: /Giulia Bianchi/i}); await expect(row).toHaveCount(1);
            const checkbox = row.locator('input[type="checkbox"]');
            if (!await checkbox.isChecked()) await checkbox.locator('..').click();
            await expect(checkbox).toBeChecked(); return row;
        };
        let resolvedRecipient;
        const identify = async () => {
            await page.locator('#bkn_datatable_transfer_selected').click();
            const popup = page.locator('.swal2-popup'); await expect(popup).toBeVisible();
            const checking = page.waitForResponse(response => {
                const url = new URL(response.url());
                return response.request().method() === 'GET' && url.pathname === '/api/oauth2/check/email'
                    && url.searchParams.get('email') === recipient.email && url.searchParams.has('get_user');
            });
            await popup.locator('#subscription-receipient').fill(recipient.email);
            const checked = await checking;
            expect(checked.status()).toBe(409);
            const lookup = await checked.json();
            expect(lookup.valid).toBe(false); expect(lookup.exception).toBe('email already taken.');
            expect(lookup.user.user_id).toBe(recipient.user_id); expect(lookup.user.email).toBe(recipient.email);
            resolvedRecipient = lookup.user;
            // An existing recipient is intentionally an email-availability
            // conflict. Declare this one real, query/body-verified UI response.
            report.expected_denials.push({path: '/api/oauth2/check/email', method: 'GET', status: 409,
                identity: 'owner', observed_browser: true, reason: 'existing fixture recipient resolved',
                recipient: recipient.user_id});
            await expect(popup.locator('#warning-message')).toContainText('Lucia Testa');
            await expect(popup.getByRole('button', {name: 'Dai accesso', exact: true})).toBeEnabled();
            return popup;
        };
        const recipientApi = (route, options = {}) => context.request.fetch(input.origin + '/api/' + route,
            {...options, headers: {Authorization: `Bearer ${recipient.token}`}});
        let confirmed = false;
        try {
            await open('Organizzazione', '/#/members/list'); await select();
            await take('access-single-owned-subscription');
            let popup = await identify(); await take('access-recipient-confirmation', page, popup);
            proof('existing_recipient_resolved', resolvedRecipient.user_id === recipient.user_id
                && resolvedRecipient.email === recipient.email);
            await popup.getByRole('button', {name: 'Annulla', exact: true}).click();
            proof('cancel_preserves_owner', await ownership() === input.user_id);
            await take('access-cancelled-unchanged');
            popup = await identify(); await take('access-recipient-rechecked', page, popup);
            const transferring = page.waitForResponse(response => new URL(response.url()).pathname === `/api/subscription/${uid}/transfer`
                && response.request().method() === 'POST');
            await popup.getByRole('button', {name: 'Dai accesso', exact: true}).click();
            const response = await transferring; proof('real_transfer_status', response.status());
            expect(response.request().postDataJSON().recipient).toBe(recipient.user_id);
            const transfer = await response.json(); confirmed = true;
            proof('accepted_immediately', transfer.status === 2 && transfer.recipient === recipient.user_id
                && transfer.requester === input.user_id && transfer.subscription === uid);
            const after = await member();
            proof('subscription_user_updated', await ownership() === recipient.user_id);
            proof('association_preserved', after.sport_association === initial.sport_association);
            proof('persona_user_updated', (await json(`personas/${personaId}/info`)).user === recipient.user_id);
            const recipientInfo = (await json(`subscription/${uid}/info`, recipientApi)).data.info;
            proof('recipient_can_read_subscription', recipientInfo.subscription_id === uid
                && recipientInfo.user.email === recipient.email
                && await ownership(recipientApi, 'athlete') === recipient.user_id);
            await page.reload(); await select();
            proof('state_survives_reload', await ownership() === recipient.user_id);
            await take('access-transfer-persisted');
            await page.locator('#bkn_datatable_transfer_selected').click();
            await expect(page.getByText('Non puoi dare accesso ad un iscritto che già ha accesso.', {exact: true})).toBeVisible();
            await expect(page.locator('.swal2-popup')).toHaveCount(0);
            proof('existing_access_prevents_ui_retry', true);
            // Toast is itself the visible warning being documented.
            await capture(page, 6, 'access-already-owned-warning');
            const repeat = await api(`subscription/${uid}/transfer`, {method: 'POST', data: {recipient: input.user_id}});
            proof('previous_owner_repeat_denied', repeat.status() === 403);
            const reader = await actor('reader');
            const [readerList] = await Promise.all([
                reader.page.waitForResponse(response => new URL(response.url()).pathname === '/api/subscription/list'
                    && response.request().method() === 'GET'),
                reader.open('Organizzazione', '/#/members/list'),
            ]);
            expect(readerList.status()).toBe(200);
            await select(reader.page);
            proof('reader_action_disabled', await reader.page.locator('#bkn_datatable_transfer_selected').isDisabled());
            const denial = await reader.api(`subscription/${uid}/transfer`, {method: 'POST', data: {recipient: input.user_id}});
            proof('reader_transfer_denied', denial.status() === 403);
            await take('access-reader-controls', reader.page);
            proof('denied_calls_preserve_state', await ownership() === recipient.user_id);
            proof('other_subscriptions_preserved', JSON.stringify(await otherMembers()) === JSON.stringify(originals.members));
            proof('payment_records_preserved', JSON.stringify(await payments()) === JSON.stringify(originals.payments));
            proof('no_new_personal_account_created', transfer.recipient === recipient.user_id && response.request().postDataJSON().recipient === recipient.user_id);
        } finally {
            // The original association must not call a pretend reverse-transfer:
            // it is no longer the owner. The runner resets only the explicit IDs.
            if (confirmed) report.private_cleanup = {operation: 'member-transfer-owned-fixture-reset',
                status: 'awaiting_runner_fixture_reset', fixture_profile: 'member-transfer',
                subscription: uid, persona: personaId, recipient: recipient.user_id};
        }
        proof('owned_fixture_cleanup_delegated', confirmed && report.private_cleanup.status === 'awaiting_runner_fixture_reset');
        expect(facts).toEqual(spec.outcome.expected); report[spec.outcome.field] = facts;
        report.external_gaps = [{operation: 'create-new-account-and-deliver-welcome', status: 'needs_external_verification',
            reason: 'This guide transfers to an existing fictional recipient; new-account email delivery is a separate operation.'}];
        report.checks = ['cancel leaves ownership unchanged', 'real UI POST resolves existing recipient',
            'accepted transfer and both owners persisted immediately', 'recipient reads the same subscription',
            'reader and former-owner retries rejected', 'other fixture subscriptions/payments unchanged'];
    }});
