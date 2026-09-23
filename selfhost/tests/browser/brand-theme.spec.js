import {test,expect} from '@playwright/test';
const rgb = hex => `rgb(${hex.slice(1).match(/../g).map(v=>parseInt(v,16)).join(', ')})`;
async function palette(page) {return page.evaluate(()=>window.brandTest.theme.getBrandPalette());}
test('saved branding propagates through real shared styles, charts, cache and dark mode',async({page})=>{
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto('/');
    await page.waitForFunction(()=>window.brandTest?.ready);
    await expect(page.locator('#primary')).toHaveCSS('background-color',rgb('#087e54'));
    await page.getByRole('button',{name:'Identità',exact:false}).click();
    await page.getByLabel('Colore principale').fill('#f9e300');
    await page.getByRole('button',{name:'Salva impostazioni',exact:true}).click();
    await expect(page.locator('#primary')).toHaveCSS('background-color',rgb('#f9e300'));
    for(const dark of [false,true]){
        await page.evaluate(dark=>dark?document.documentElement.setAttribute('data-theme','dark'):document.documentElement.removeAttribute('data-theme'),dark);
        await expect.poll(async()=> (await palette(page)).dark).toBe(dark);
        const p=await palette(page);
        for(const id of ['primary','disabled','background','badge','dialog']){
            await expect(page.locator('#'+id)).toHaveCSS('background-color',rgb(p.primary));
            await expect(page.locator('#'+id)).toHaveCSS('color',rgb(p.foreground));
        }
        await expect(page.locator('#associates .card-label')).toHaveCSS('color',rgb(p.foreground));
        await expect(page.locator('#tab')).toHaveCSS('border-bottom-color',rgb(p.primary));
        await expect(page.locator('#check')).toHaveCSS('background-color',rgb(p.primary));
        await expect(page.locator('#link')).toHaveCSS('color',rgb(p.text));
        await expect(page.locator('#outline')).toHaveCSS('color',rgb(p.text));
        await expect(page.locator('#light')).toHaveCSS('background-color',rgb(p.subtle));
        await page.locator('#input').focus();
        await expect(page.locator('#input')).toHaveCSS('border-top-color',rgb(p.text));
        await page.locator('#input').blur();
        expect(await page.locator('#check').evaluate(el=>getComputedStyle(el, '::after').borderTopColor)).toBe(rgb(p.foreground));
        await expect(page.locator('#alert')).toHaveCSS('background-color',rgb(p.subtle));
        await expect(page.locator('#alert')).toHaveCSS('color',rgb(p.subtleText));
        await page.locator('#primary').hover();
        await expect(page.locator('#primary')).toHaveCSS('background-color',rgb(p.hover));
        await expect(page.locator('#primary')).toHaveCSS('color',rgb(p.hoverForeground));
        await page.mouse.down();
        await expect(page.locator('#primary')).toHaveCSS('background-color',rgb(p.active));
        await page.mouse.up();await page.locator('#primary').blur();await page.mouse.move(0,0);
        await expect.poll(()=>page.evaluate(()=>window.brandTest.echarts.getInstanceByDom(document.getElementById('bkn_dashboard_widget_associates'))?.getOption().series[0].areaStyle.color)).toBe(p.primary);
        await expect.poll(()=>page.evaluate(()=>window.brandTest.Chart.getChart(document.getElementById('bkn_dashboard_widget_subscriptions'))?.data.datasets[0].backgroundColor[0])).toBe(p.primary);
    }
    await expect(page.locator('meta[name="theme-color"]')).toHaveAttribute('content','#f9e300');
    await page.reload();await page.waitForFunction(()=>window.brandTest?.ready);
    await expect(page.locator('#primary')).toHaveCSS('background-color',rgb('#f9e300'));
    await page.request.post('/api/test-color',{data:{color:'#1267a4'}});
    await page.reload();await page.waitForFunction(()=>window.brandTest?.ready);
    await expect(page.locator('#primary')).toHaveCSS('background-color',rgb('#1267a4'));
    expect(errors).toEqual([]);
});

for (const cart of [false,true]) test(`mounted ${cart ? 'cart' : 'single'} payment form receives theme changes`,async({page})=>{
    await page.goto('/');await page.waitForFunction(()=>window.brandTest?.ready);
    await page.evaluate(cart=>window.brandTest.mountPayment(cart),cart);
    await page.waitForFunction(()=>window.paymentMounted);
    const initial=await palette(page);
    expect(await page.evaluate(()=>window.paymentOptions.appearance.variables.colorPrimary)).toBe(initial.primary);
    await page.evaluate(()=>window.brandTest.store.saveRuntimeConfig({oem:{primaryColor:'#a32476'},features:{selfHosted:true}}));
    await expect.poll(()=>page.evaluate(()=>window.paymentUpdates.at(-1)?.appearance.variables.colorPrimary)).toBe('#a32476');
    await page.evaluate(()=>document.documentElement.setAttribute('data-theme','dark'));
    await expect.poll(async()=> (await palette(page)).dark).toBe(true);
    expect(await page.evaluate(()=>window.paymentUpdates.at(-1).appearance.variables.colorPrimary)).toBe((await palette(page)).primary);
    await page.evaluate(()=>window.brandTest.destroyExtra());
    const count=await page.evaluate(()=>window.paymentUpdates.length);
    await page.evaluate(()=>window.brandTest.theme.applyBrandColor('#ffffff'));
    expect(await page.evaluate(()=>window.paymentUpdates.length)).toBe(count);
});
test('membership cards inherit brand until an explicit custom color is chosen',async({page})=>{
    await page.goto('/');await page.waitForFunction(()=>window.brandTest?.ready);
    await page.evaluate(()=>window.brandTest.mountCard());
    const card=page.locator('#extra .bg-primary').first();
    await page.evaluate(()=>window.brandTest.theme.applyBrandColor('#ffff00'));
    await expect(card).toHaveCSS('background-color',rgb('#ffff00'));
    await expect(card).toHaveCSS('color',rgb('#000000'));
    await page.evaluate(()=>window.brandTest.setCardColor('#661234'));
    await page.evaluate(()=>window.brandTest.theme.applyBrandColor('#087e54'));
    await expect(card).toHaveCSS('background-color',rgb('#661234'));
    await page.evaluate(()=>window.brandTest.setCardColor(null));
    await expect(card).toHaveCSS('background-color',rgb('#087e54'));
});

test('email editor changes interface theme without changing authored content',async({page})=>{
    await page.goto('/');await page.waitForFunction(()=>window.brandTest?.ready);
    await page.evaluate(()=>window.brandTest.mountEditor());
    const save=page.locator('#extra').getByRole('button',{name:'Salva',exact:true});
    await expect(save).toBeVisible();
    await save.click();
    const before=await page.evaluate(()=>window.savedEmail);
    expect(before?.json).toBeTruthy();
    await page.evaluate(()=>window.brandTest.theme.applyBrandColor('#ffff00'));
    await page.mouse.move(0,0);await save.blur();
    await expect(save).toHaveCSS('background-color',rgb('#ffff00'));
    await expect(save).toHaveCSS('color',rgb('#000000'));
    await page.evaluate(()=>document.documentElement.setAttribute('data-theme','dark'));
    await expect.poll(async()=> (await palette(page)).dark).toBe(true);
    await expect(save).toHaveCSS('color',rgb((await palette(page)).foreground));
    await save.click();
    expect(await page.evaluate(()=>window.savedEmail)).toEqual(before);
    await page.evaluate(()=>window.brandTest.disposeEditor());
    await expect(save).toHaveCount(0);
});

test('setup draft recolors the wizard without changing the saved instance',async({page})=>{
    await page.goto('/');await page.waitForFunction(()=>window.brandTest?.ready);
    const saved=(await palette(page)).brand;
    await page.evaluate(()=>{window.brandTest.mountSetup();});
    const wizard=page.locator('#extra .setup-wizard');
    await wizard.getByPlaceholder('Inserisci il token di bootstrap').fill('fixture-token');
    await wizard.getByRole('button',{name:'Continua'}).click();
    await wizard.getByRole('button',{name:/Inizia da Zero/}).click();
    await wizard.getByPlaceholder('A.S.D. Nome Associazione').fill('Fixture club');
    await wizard.getByPlaceholder('admin@miaassociazione.it').fill('owner@example.test');
    await wizard.getByPlaceholder('Inserisci una password sicura').fill('FixturePassword123!');
    await wizard.getByPlaceholder('Conferma la password').fill('FixturePassword123!');
    await wizard.getByRole('button',{name:'Continua'}).click();
    await wizard.locator('input[type="color"]').fill('#e4da10');
    await expect(wizard.locator('.step-dot.active')).toHaveCSS('background-color',rgb('#e4da10'));
    await expect(wizard.locator('.step-dot.active')).toHaveCSS('color',rgb('#000000'));
    await expect(wizard.locator('.step-icon')).toHaveCSS('color',rgb('#e4da10'));
    expect((await palette(page)).brand).toBe(saved);
});

test('standalone error and offline pages restore the saved palette',async({page})=>{
    await page.goto('/');await page.waitForFunction(()=>window.brandTest?.ready);
    await page.evaluate(()=>{
        window.brandTest.theme.applyBrandColor('#cdec15');
        localStorage.setItem('userData',JSON.stringify({dark_mode:false}));
    });
    for (const path of ['/error.html','/offline.html']) {
        await page.goto(path);
        await expect(page.locator('.btn-primary')).toHaveCSS('background-color',rgb('#cdec15'));
        await expect(page.locator('.btn-primary')).toHaveCSS('color',rgb('#000000'));
        await expect(page.locator('meta[name="theme-color"]')).toHaveAttribute('content','#cdec15');
    }
});
