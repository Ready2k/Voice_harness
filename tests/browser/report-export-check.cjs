// Opt-in real-browser regression for a self-contained export, opened directly from disk.
// Requires Playwright; HARNESS_BROWSER_EXECUTABLE can select an installed Chromium browser.
const assert = require('node:assert/strict');
const {pathToFileURL} = require('node:url');
const {chromium} = require('playwright');

(async () => {
  const browser = await chromium.launch({headless: true,
    ...(process.env.HARNESS_BROWSER_EXECUTABLE ? {executablePath: process.env.HARNESS_BROWSER_EXECUTABLE} : {})});
  try {
    const page = await browser.newPage({viewport: {width: 1180, height: 900}});
    const errors = [];
    page.on('pageerror', error => errors.push(String(error)));
    await page.goto(pathToFileURL(process.argv[2]).href);
    const tabs = page.getByRole('tab');
    await tabs.first().waitFor();
    const panels = page.getByRole('tabpanel', {includeHidden: true});
    const displays = () => panels.evaluateAll(nodes => nodes.map(node => getComputedStyle(node).display));
    assert.deepEqual(await displays(), ['flex', 'none']);
    await tabs.nth(1).click();
    assert.deepEqual(await displays(), ['none', 'flex']);
    assert.equal(await tabs.nth(1).getAttribute('aria-selected'), 'true');

    const matrix = panels.nth(1);
    const cells = matrix.locator('.bk-chip');
    const count = await cells.count();
    assert.ok(count > 0);
    const filter = matrix.locator('select');
    const group = await filter.locator('option').last().getAttribute('value');
    await filter.selectOption(group);
    assert.ok(await cells.count() > 0 && await cells.count() < count);
    await filter.selectOption('all');
    assert.equal(await cells.count(), count);
    const detail = matrix.locator('.bk-detail:not(.bk-plain)');
    assert.equal(await detail.isVisible(), false);
    await cells.first().click();
    assert.equal(await cells.first().getAttribute('aria-pressed'), 'true');
    assert.equal(await detail.isVisible(), true);
    assert.match(await detail.textContent(), /run_/);

    for (const selected of [0, 1]) {
      await tabs.nth(selected).click();
      await page.emulateMedia({media: 'print'});
      await page.evaluate(() => window.dispatchEvent(new Event('beforeprint')));
      assert.deepEqual(await displays(), ['flex', 'flex']);
      await page.emulateMedia({media: 'screen'});
      await page.evaluate(() => window.dispatchEvent(new Event('afterprint')));
      assert.deepEqual(await displays(), selected === 0 ? ['flex', 'none'] : ['none', 'flex']);
    }
    assert.deepEqual(errors, []);
    if (process.env.HARNESS_REPORT_SCREENSHOT) await page.screenshot({path: process.env.HARNESS_REPORT_SCREENSHOT});
    console.log('PASS: tabs, group filter, cell details and print restoration:', process.argv[2]);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
