// Only the existing seeded collaborator is edited. No invitation is dispatched.
import {scenario, expect} from './scenario.mjs';
import {focusRegion, tableFocus} from './focus.mjs';
import {organizationBasicsAuthoredWorkflows} from '../../../../docs/manuale/organization-basics-authored-workflows.mjs';
const spec = organizationBasicsAuthoredWorkflows['collaborator-permissions'];
import {collaboratorPermissionsSources, reloadOrganizationProfile} from './organization-access-sources.mjs';

await scenario({id: 'collaborator-permissions', prefix: 'images/collaboratori/permessi',
    sources: spec.sources, actions: async ({page, api, open, actor, capture, report, input}) => {
        expect(input.fixture_version).toBe(8);
        expect(input.fixture_profile || 'baseline').toBe('baseline');
        const readerId = input.identities.reader.user_id;
        const json = async (route, client = api) => {
            const response = await client(route);
            expect(response.status()).toBe(200);
            return response.json();
        };
        const originalProfile = (await json('profile/info')).user_data;
        const list = await json('collaborators/list');
        const original = list.find(row => row.user_id === readerId);
        expect(original.first_name).toBe('Marco');
        expect(original.last_name).toBe('Neri');
        expect(original.collaborator_role).toBe(3);
        expect(original.is_invite).toBe(false);
        expect(original.collaborator_permissions).toContain('other.settings.read');
        expect(original.collaborator_permissions).toContain('other.users.collaborators.read');
        expect(original.collaborator_permissions).not.toContain('other.settings.update');
        const invitesBefore = list.filter(row => row.is_invite).length;
        let invitationRequests = 0;
        page.on('request', request => {
            if (new URL(request.url()).pathname === '/api/collaborators/add' && request.method() === 'POST') invitationRequests++;
        });
        const take = async (number, checkpoint, target = page, locator) => {
            await expect(target.locator('[data-sonner-toast]:visible')).toHaveCount(0, {timeout: 10000});
            await capture(target, number, checkpoint, locator);
        };
        try {
        await open('Collaboratori', '/#/connected-collaborators');
        const row = () => page.locator('[data-row]').filter({hasText: original.email});
        await expect(row()).toContainText('MARCO NERI');
        await expect(row()).toContainText('Accettato');
        await expect(row()).toContainText('Personalizzato');
        await take(1, 'existing-accepted-custom-collaborator-and-edit-action', page, focusRegion(
            tableFocus(page, ['accepted', 'role'], row()), row().locator('button:is([title="Modifica"],[data-original-title="Modifica"])')));
        const readOnly = await actor('reader');
        await readOnly.open('Collaboratori', '/#/connected-collaborators');
        await expect(readOnly.page.locator('[data-target="#addCollaboratorModal"]')).toHaveCount(0);
        await expect(readOnly.page.locator('[data-row]').filter({hasText: original.email}).locator('button:is([title="Modifica"],[data-original-title="Modifica"])')).toBeDisabled();
        await readOnly.open('Impostazioni', '/#/profile');
        await readOnly.page.getByText('Informazioni Account', {exact: true}).click();
        await expect(readOnly.page.getByPlaceholder('Inserisci denominazione...', {exact: true})).toBeDisabled();
        await readOnly.page.getByPlaceholder('Inserisci denominazione...', {exact: true}).scrollIntoViewIfNeeded();
        await take(2, 'custom-reader-profile-shows-disabled-organization-fields', readOnly.page, focusRegion(
            readOnly.page.getByRole('heading', {name: 'Organizzazione', exact: true}),
            readOnly.page.getByPlaceholder('Inserisci denominazione...', {exact: true})));
        const readerProfile = (await json('profile/info', readOnly.api)).user_data;
        const deniedBefore = await readOnly.api('profile/update', {method: 'PATCH', data: {
            user_data: {...readerProfile, first_name: 'Vietato'}}});
        expect(deniedBefore.status()).toBe(403);
        const deniedPermissions = await readOnly.api(`collaborators/${readerId}/update`, {method: 'PATCH',
            data: {collaborator_role: 1}});
        expect(deniedPermissions.status()).toBe(403);
        expect((await json('profile/info', readOnly.api)).user_data.first_name).toBe('Marco');
        await row().locator('button:is([title="Modifica"],[data-original-title="Modifica"])').click();
        const edit = page.locator(`#editModal-${readerId}`);
        await expect(edit).toBeVisible();
        await expect(edit.locator('select[name="collaborator_role"]')).toHaveValue('3');
        await expect(edit.locator('select[name="collaborator_role"] option')).toHaveText(['Accesso Completo', 'Personalizzato']);
        await edit.locator('input[name="other.settings.update"]').check({force: true});
        await edit.locator('input[name="other.settings.update"]').scrollIntoViewIfNeeded();
        await take(3, 'owner-adds-only-update-settings-to-existing-custom-permissions', page,
            edit.locator('.form-group.border').filter({has: page.locator('input[name="other.settings.update"]')}));
        const savePermissions = async () => {
            const saved = page.waitForResponse(response => new URL(response.url()).pathname === `/api/collaborators/${readerId}/update`
                && response.request().method() === 'PATCH');
            await edit.getByRole('button', {name: 'Salva', exact: true}).click();
            expect((await saved).status()).toBe(200);
            await expect(edit).not.toBeVisible();
        };
        await savePermissions();
        await page.reload();
        const granted = (await json('collaborators/list')).find(item => item.user_id === readerId);
        expect(granted.collaborator_role).toBe(3);
        expect([...granted.collaborator_permissions].sort()).toEqual([...original.collaborator_permissions, 'other.settings.update'].sort());
        const writer = await actor('reader');
        await writer.open('Impostazioni', '/#/profile');
        await writer.page.getByText('Informazioni Account', {exact: true}).click();
        await expect(writer.page.getByPlaceholder('Inserisci denominazione...', {exact: true})).toBeEnabled();
        const nameField = writer.page.locator('#bkn_form_account_update .form-group')
            .filter({has: writer.page.locator('label').filter({hasText: /^Nome$/})}).locator('input');
        await nameField.fill('Matteo');
        const saveOwnName = async () => {
            const saved = writer.page.waitForResponse(response => new URL(response.url()).pathname === '/api/profile/update'
                && response.request().method() === 'PATCH');
            await writer.page.locator('#bkn_form_account_update_submit').click();
            expect((await saved).status()).toBe(200);
        };
        await nameField.scrollIntoViewIfNeeded();
        await take(4, 'collaborator-current-profile-after-grant-and-own-name-edit', writer.page, focusRegion(
            nameField.locator('..').locator('..').locator('label'), nameField));
        await saveOwnName();
        const savedReader = await reloadOrganizationProfile({...writer, expect});
        expect(savedReader.first_name).toBe('Matteo');
        expect(savedReader.user_id).toBe(readerId);
        expect(granted.connected_user).toBe(input.user_id);
        await expect(nameField).toHaveValue('Matteo');
        await nameField.scrollIntoViewIfNeeded();
        await take(5, 'authorized-own-profile-write-persists-and-owner-organization-is-preserved', writer.page, focusRegion(
            nameField.locator('..').locator('..').locator('label'), nameField));
        const ownerAfter = (await json('profile/info')).user_data;
        expect(ownerAfter.first_name).toBe(originalProfile.first_name);
        expect(ownerAfter.sport_association).toEqual(originalProfile.sport_association);
        // Restore the actor's own data while the real permission is still active.
        await nameField.fill('Marco');
        await saveOwnName();
        expect((await json('profile/info', writer.api)).user_data.first_name).toBe('Marco');
        await page.reload();
        await row().locator('button:is([title="Modifica"],[data-original-title="Modifica"])').click();
        await expect(edit).toBeVisible();
        await edit.locator('input[name="other.settings.update"]').uncheck({force: true});
        await savePermissions();
        const restored = (await json('collaborators/list')).find(item => item.user_id === readerId);
        expect([...restored.collaborator_permissions].sort()).toEqual([...original.collaborator_permissions].sort());
        expect(restored.collaborator_role).toBe(3);
        const revoked = await actor('reader');
        await revoked.open('Impostazioni', '/#/profile');
        await revoked.page.getByText('Informazioni Account', {exact: true}).click();
        await expect(revoked.page.getByPlaceholder('Inserisci denominazione...', {exact: true})).toBeDisabled();
        await revoked.page.getByPlaceholder('Inserisci denominazione...', {exact: true}).scrollIntoViewIfNeeded();
        await take(6, 'revoked-update-permission-restores-reader-disabled-organization-controls', revoked.page, focusRegion(
            revoked.page.getByRole('heading', {name: 'Organizzazione', exact: true}),
            revoked.page.getByPlaceholder('Inserisci denominazione...', {exact: true})));
        // The already issued token must also be denied after revocation.
        const deniedAfter = await writer.api('profile/update', {method: 'PATCH',
            data: {user_data: {...savedReader, first_name: 'Vietato'}}});
        expect(deniedAfter.status()).toBe(403);
        expect((await json('profile/info', writer.api)).user_data.first_name).toBe('Marco');
        await page.locator('[data-target="#addCollaboratorModal"]').click();
        const invitation = page.locator('#addCollaboratorModal');
        await expect(invitation).toBeVisible();
        await invitation.locator('input[name="email"]').fill('nuovo.collaboratore@example.test');
        await invitation.locator('select[name="collaborator_role"]').selectOption('3');
        await invitation.locator('input[name="other.settings.read"]').check({force: true});
        await take(7, 'invitation-form-prepared-with-fictitious-email-without-dispatch', page, focusRegion(
            invitation.locator('input[name="email"]').locator('..'),
            invitation.locator('select[name="collaborator_role"]').locator('..')));
        await invitation.getByRole('button', {name: 'Chiudi', exact: true}).first().click();
        expect(invitationRequests).toBe(0);
        const final = await json('collaborators/list');
        expect(final.filter(item => item.is_invite)).toHaveLength(invitesBefore);
        expect(final.find(item => item.user_id === readerId).first_name).toBe('Marco');
        // Exercise the actual full-access role, then restore the original custom role.
        await row().locator('button:is([title="Modifica"],[data-original-title="Modifica"])').click();
        await edit.locator('select[name="collaborator_role"]').selectOption('1');
        await savePermissions(); await page.reload();
        const full = (await json('collaborators/list')).find(item => item.user_id === readerId);
        expect(full.collaborator_role).toBe(1);
        const fullActor = await actor('reader');
        await fullActor.open('Impostazioni', '/#/profile');
        await fullActor.page.getByText('Informazioni Account', {exact: true}).click();
        await expect(fullActor.page.getByPlaceholder('Inserisci denominazione...', {exact: true})).toBeEnabled();
        const fullName = fullActor.page.locator('#bkn_form_account_update .form-group')
            .filter({has: fullActor.page.locator('label').filter({hasText: /^Nome$/})}).locator('input');
        await fullName.fill('Marino');
        const fullSave = fullActor.page.waitForResponse(res => new URL(res.url()).pathname === '/api/profile/update' && res.request().method() === 'PATCH');
        await fullActor.page.locator('#bkn_form_account_update_submit').click(); expect((await fullSave).status()).toBe(200);
        await reloadOrganizationProfile({...fullActor, expect}); await expect(fullName).toHaveValue('Marino');
        await fullName.scrollIntoViewIfNeeded(); await take(8, 'full-access-role-persists-and-allows-own-profile-write', fullActor.page, focusRegion(
            fullName.locator('..').locator('..').locator('label'), fullName));
        expect((await json('profile/info')).user_data.sport_association).toEqual(originalProfile.sport_association);
        await fullName.fill('Marco');
        const restoreName = fullActor.page.waitForResponse(res => new URL(res.url()).pathname === '/api/profile/update' && res.request().method() === 'PATCH');
        await fullActor.page.locator('#bkn_form_account_update_submit').click(); expect((await restoreName).status()).toBe(200);
        await row().locator('button:is([title="Modifica"],[data-original-title="Modifica"])').click();
        await edit.locator('select[name="collaborator_role"]').selectOption('3');
        for (const control of await edit.locator('input[type="checkbox"][name]').all()) {
            const key = await control.getAttribute('name');
            if (original.collaborator_permissions.includes(key)) await control.check({force: true});
            else await control.uncheck({force: true});
        }
        await savePermissions(); await page.reload();
        const afterFull = (await json('collaborators/list')).find(item => item.user_id === readerId);
        expect(afterFull.collaborator_role).toBe(3);
        expect([...afterFull.collaborator_permissions].sort()).toEqual([...original.collaborator_permissions].sort());
        expect(afterFull.first_name).toBe('Marco');
        await expect(page.locator('.datatable-loading')).toHaveCount(0);
        await expect(row()).toContainText(original.email);
        await expect(row()).toContainText('MARCO NERI');
        await expect(row()).toContainText('Accettato');
        await expect(row()).toContainText('Personalizzato');
        await take(9, 'full-access-returned-to-original-custom-permissions', page, tableFocus(page, ['accepted', 'role'], row()));
        report.collaborator_permissions = {existing_actor: true, initial_role: original.collaborator_role,
            final_role: restored.collaborator_role, granted_permission: 'other.settings.update',
            only_requested_permission_added: true, persisted_after_owner_reload: true,
            reader_profile_write_before_status: deniedBefore.status(), reader_permission_write_status: deniedPermissions.status(),
            authorized_own_profile_write_status: 200, authorized_name: savedReader.first_name,
            authorized_own_profile_persisted: true, owner_and_organization_preserved: true,
            revoked_existing_token_write_status: deniedAfter.status(), baseline_permissions_restored: true,
            baseline_name_restored: true, invitation_prepared: true, invitation_dispatches: invitationRequests,
            invitation_records_unchanged: final.filter(item => item.is_invite).length === invitesBefore,
            full_access_role_saved_and_reopened: true, full_access_real_profile_write: true, full_access_returned_to_custom: true};
        report.external_gaps = [{operation: 'collaborator-invitation-delivery-and-acceptance',
            status: 'needs_external_verification', reason: 'Form inspected only; no email dispatched, provider delivery or acceptance demonstrated.'}];
        report.checks = ['owner edits existing accepted custom collaborator using actual permission PATCH',
            'baseline reader profile/permission writes return 403 without mutation',
            'grant enables organization controls and real own profile UI PATCH persists only the collaborator name',
            'revocation denies the same issued token and restores read-only controls',
            'permissions and name restored; invitation form inspected without POST or external email'];
        } finally {
            // Restore only the existing actor touched by this scenario; no invitation is sent.
            const current = (await json('collaborators/list')).find(item => item.user_id === readerId);
            if (current.first_name !== original.first_name) {
                expect(['Matteo', 'Marino']).toContain(current.first_name);
                expect((await api(`collaborators/${readerId}/update`, {method: 'PATCH', data: {collaborator_role: 3,
                    collaborator_permissions: [...new Set([...original.collaborator_permissions, 'other.settings.update'])]}})).status()).toBe(200);
                const cleanup = await actor('reader'); const own = (await json('profile/info', cleanup.api)).user_data;
                expect((await cleanup.api('profile/update', {method: 'PATCH', data: {user_data: {...own, first_name: original.first_name}}})).status()).toBe(200);
            }
            expect((await api(`collaborators/${readerId}/update`, {method: 'PATCH', data: {collaborator_role: original.collaborator_role,
                collaborator_permissions: original.collaborator_permissions}})).status()).toBe(200);
            const restoredActor = (await json('collaborators/list')).find(item => item.user_id === readerId);
            expect(restoredActor.first_name).toBe(original.first_name); expect(restoredActor.collaborator_role).toBe(original.collaborator_role);
            expect([...restoredActor.collaborator_permissions].sort()).toEqual([...original.collaborator_permissions].sort());
        }
        report.collaborator_permissions.scoped_cleanup_completed = true;
    }});
