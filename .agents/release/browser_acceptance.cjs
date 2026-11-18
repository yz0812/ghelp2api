const { chromium } = require('../../frontend/node_modules/@playwright/test');
const fs = require('node:fs');
const path = require('node:path');
(async () => {
  const root = path.resolve(__dirname, '../../.cache/release');
  const auth = JSON.parse(fs.readFileSync(path.join(root, 'browser.json'), 'utf8'));
  const browser = await chromium.launch();
  const context = await browser.newContext({ storageState: auth.storage, viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto('https://ghelper.accsunny.com/chains/' + auth.chain_id + '/edit');
  await page.getByRole('heading', { name: '编辑链路', exact: true }).waitFor();
  await page.locator('.hop-card').first().waitFor();
  if (await page.locator('.hop-card').count() !== 3) throw Error('three-hop editor missing');
  const settings = await page.evaluate(async () => (await fetch('/api/v1/settings')).json());
  if (settings.version !== '1.3.0') throw Error('unexpected version');
  for (const theme of ['light', 'dark']) {
    await page.evaluate(t => document.documentElement.classList.toggle('dark', t === 'dark'), theme);
    for (const width of [320, 390, 768, 1440]) {
      await page.setViewportSize({ width, height: 1000 });
      if (await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)) throw Error('overflow: ' + theme + width);
      if ([390, 1440].includes(width)) await page.screenshot({ path: path.join(root, 'oracle1-' + theme + '-' + width + '.png'), fullPage: true });
    }
  }
  await page.getByRole('tab', { name: /备用 1/ }).click();
  if (await page.locator('.hop-card').count() !== 2) throw Error('backup path missing');
  await page.getByRole('button', { name: '返回链路', exact: true }).click();
  await page.getByRole('heading', { name: '链式', exact: true }).waitFor();
  const card = page.locator('.chain-card').filter({ has: page.getByRole('heading', { name: '编排验收20261117链路', exact: true }) });
  await card.getByRole('button', { name: '诊断与历史', exact: true }).click();
  await page.getByRole('tab', { name: '监控与事件', exact: true }).click();
  await page.getByText(/自动监控已开启/).waitFor();
  await page.screenshot({ path: path.join(root, 'oracle1-history.png'), fullPage: true });
  if (errors.length) throw Error('browser runtime errors: ' + errors.length);
  console.log(JSON.stringify({ version: settings.version, editor: true, backup: true, widths: [320,390,768,1440], themes: 2, history: true, runtimeErrors: 0 }));
  await browser.close();
})().catch(e => { console.error(e.message); process.exit(1); });
