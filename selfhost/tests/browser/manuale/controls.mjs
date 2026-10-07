// Operate the visible label of styled checkboxes through actual pointer input.
// The native input remains the authority for permissions and saved UI state.
import {expect} from './scenario.mjs';

export async function setCheckbox(control, checked) {
    await expect(control).toHaveCount(1);
    await expect(control).toBeEnabled();
    if (await control.isChecked() !== checked) {
        const label = control.locator('xpath=ancestor::label[1]');
        if (await label.count()) await label.click();
        else await control.setChecked(checked);
    }
    if (checked) await expect(control).toBeChecked();
    else await expect(control).not.toBeChecked();
}
