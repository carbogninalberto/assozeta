import {expect} from '@playwright/test';

// The styled switch's native input has zero size. Use its visible label,
// just as a user would, and verify the accessible control's resulting state.
export async function setSwitch(scope, id, name, checked) {
    const control = scope.getByRole('switch', {name, exact: true});
    await expect(control).toBeAttached();
    await expect(control).toBeEnabled();
    if (await control.isChecked() !== checked) {
        await scope.locator(`label[for="${id}"]`).click();
    }
    await expect(control).toBeChecked({checked});
}
