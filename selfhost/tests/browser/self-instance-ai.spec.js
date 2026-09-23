import {test, expect} from '@playwright/test';

for (const viewport of [{width: 1440, height: 1000}, {width: 390, height: 844}]) {
    test(`AI configuration and navigation at ${viewport.width}px`, async ({page, request}) => {
        await page.setViewportSize(viewport);
        await request.post('/fixture/reset');
        const errors = [];
        page.on('pageerror', error => {errors.push(error.message); console.error(error.message);});
        await page.goto('/');
        await expect(page.getByRole('heading', {name: 'Panoramica', exact: true})).toBeVisible();
        await expect(page.getByRole('button', {name: 'Agente AI', exact: true})).toHaveCount(0);
        const tabs = page.getByRole('group', {name: 'Sezioni Self Instance'});
        await tabs.getByRole('button', {name: 'Bot AI', exact: true}).focus();
        await page.keyboard.press('Enter');
        const card = page.getByRole('region', {name: 'Bot AI', exact: true});
        await expect(card).toBeVisible();
        const toggle = card.getByRole('switch', {name: 'Abilita Bot AI', exact: true});
        await expect(card.getByRole('button', {name: 'Verifica Bot AI', exact: true})).toBeDisabled();
        await toggle.focus();
        await page.keyboard.press('Space');
        await expect(toggle).toBeChecked();
        await expect(card.getByRole('button', {name: 'Verifica Bot AI', exact: true})).toBeDisabled();
        await expect(page.getByRole('button', {name: 'Agente AI', exact: true})).toHaveCount(0); // Only saved state changes the navbar.
        await card.getByLabel('Chiave API', {exact: true}).fill('fixture-secret');
        await card.getByLabel('URL del provider', {exact: false}).fill('https://provider.example.test/v1');
        await card.getByLabel('Modello principale', {exact: false}).fill('fixture-model');
        await card.getByLabel('Modello economico', {exact: false}).fill('fixture-small');
        for (const [label, value] of [['Iterazioni massime', '8'], ['Risultati massimi', '321'], ['Timeout query (secondi)', '12'], ['Messaggi al minuto', '7'], ['Timeout risposta (secondi)', '150'], ['Messaggi nella cronologia', '30']]) {
            await card.getByLabel(label, {exact: false}).fill(value);
        }
        await tabs.getByRole('button', {name: 'Panoramica', exact: true}).click();
        await tabs.getByRole('button', {name: 'Bot AI •', exact: true}).click();
        await expect(card.getByLabel('Modello principale', {exact: false})).toHaveValue('fixture-model');
        await card.getByRole('button', {name: 'Salva Bot AI', exact: true}).click();
        await expect(card.getByRole('button', {name: 'Salva Bot AI', exact: true})).toBeDisabled();
        await expect(page.getByRole('button', {name: 'Agente AI', exact: true})).toBeVisible();
        await expect(page.getByText('Bot AI: impostazioni salvate.', {exact: true})).toBeVisible();
        await expect(card.getByLabel('Chiave API', {exact: true})).toHaveValue('');
        await card.getByRole('button', {name: 'Verifica Bot AI', exact: true}).click();
        await expect(card.locator('.diagnostic-result')).toContainText('Elenco modelli accessibile');
        await page.reload();
        await tabs.getByRole('button', {name: 'Bot AI', exact: true}).click();
        await expect(toggle).toBeChecked();
        await expect(card.locator('.diagnostic-result')).toContainText('Elenco modelli accessibile');
        await expect(card.getByLabel('Risultati massimi', {exact: false})).toHaveValue('321');
        const quick = await page.locator('#navbar .bk-dropdown-toggle').boundingBox();
        const bot = await page.getByRole('button', {name: 'Agente AI', exact: true}).boundingBox();
        expect(Math.abs((quick.y + quick.height / 2) - (bot.y + bot.height / 2))).toBeLessThan(1);
        expect(quick.height).toBe(bot.height);
        const quickIcon = await page.locator('#navbar .bk-dropdown-toggle span svg').boundingBox();
        const botIcon = await page.locator('#navbar .agent-toggle-btn svg').boundingBox();
        expect(Math.abs((quickIcon.y + quickIcon.height / 2) - (botIcon.y + botIcon.height / 2))).toBeLessThan(1);
        await page.screenshot({path: `/tmp/assozeta-ai-${viewport.width}.png`, fullPage: true});
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
        // A conflict retains the draft and the saved navbar state.
        await request.post('/fixture/failure', {data: {enabled: true}});
        await toggle.focus();
        await page.keyboard.press('Space');
        await card.getByRole('button', {name: 'Salva Bot AI', exact: true}).click();
        await expect(card.getByRole('alert')).toContainText('un’altra sessione');
        await expect(page.getByRole('button', {name: 'Agente AI', exact: true})).toBeVisible();
        await request.post('/fixture/failure', {data: {enabled: false}});
        await card.getByRole('button', {name: 'Salva Bot AI', exact: true}).click();
        await expect(page.getByRole('button', {name: 'Agente AI', exact: true})).toHaveCount(0);
        await page.reload();
        await tabs.getByRole('button', {name: 'Bot AI', exact: true}).click();
        await expect(toggle).not.toBeChecked();
        await expect(page.getByRole('button', {name: 'Agente AI', exact: true})).toHaveCount(0);
        await card.getByText('Ripristina Bot AI da .env', {exact: true}).click();
        await card.getByRole('switch', {name: 'Confermo il ripristino di Bot AI da .env'}).focus();
        await page.keyboard.press('Space');
        await card.getByRole('button', {name: 'Ripristina Bot AI', exact: true}).click();
        await expect(card.getByLabel('Modello principale', {exact: false})).toHaveValue('deepseek-v4-flash');
        await request.post('/fixture/maintenance', {data: {enabled: true}});
        await page.reload();
        await tabs.getByRole('button', {name: 'Bot AI', exact: true}).click();
        await expect(toggle).toBeDisabled();
        await expect(card.getByRole('button', {name: 'Verifica Bot AI', exact: true})).toBeDisabled();
        expect(errors).toEqual([]);
    });
}
