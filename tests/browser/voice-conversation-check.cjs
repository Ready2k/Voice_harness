// Offline browser check. Pass a populated, synthetic rating-page fixture as argv[2].
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const {pathToFileURL} = require('node:url');
const {chromium} = require('playwright');

(async () => {
  const browser = await chromium.launch({headless:true,
    ...(process.env.HARNESS_BROWSER_EXECUTABLE ? {executablePath:process.env.HARNESS_BROWSER_EXECUTABLE} : {})});
  try {
    const page = await browser.newPage({viewport:{width:1180,height:950}});
    const errors = [];
    page.on('pageerror', error => errors.push(String(error)));
    await page.goto(pathToFileURL(process.argv[2]).href);
    assert.ok(await page.locator('audio').count() > 0);
    assert.equal(await page.locator('select').first().isDisabled(), true);
    await page.locator('#reviewer').fill('reviewer-a');
    await page.locator('#reviewer').press('Tab');
    await page.locator('select').first().selectOption('4');
    await page.locator('audio').first().evaluate(audio => audio.load());
    await page.waitForFunction(() => document.querySelector('audio').readyState >= 1);
    assert.ok(await page.locator('audio').first().evaluate(audio => audio.duration) > 0);
    const downloaded = page.waitForEvent('download');
    await page.getByRole('button', {name:'Download ratings'}).click();
    const exportFile = await downloaded;
    const data = JSON.parse(await fs.readFile(await exportFile.path(), 'utf8'));
    assert.equal(data.reviewer_id, 'reviewer-a');
    assert.match(data.plan_hash, /^[a-f0-9]{64}$/);
    assert.equal(Object.values(data.ratings)[0].naturalness, 4);
    assert.ok(!JSON.stringify(data).includes('provider'));
    await page.locator('#reviewer').fill('reviewer-b');
    await page.locator('#reviewer').press('Tab');
    assert.equal(await page.locator('select').first().inputValue(), '');
    await page.locator('#reviewer').fill('reviewer-a');
    await page.locator('#reviewer').press('Tab');
    assert.equal(await page.locator('select').first().inputValue(), '4');
    if(process.env.HARNESS_REPORT_SCREENSHOT) await page.screenshot({path:process.env.HARNESS_REPORT_SCREENSHOT});
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),true);
    assert.deepEqual(errors, []);
    console.log('PASS: blind page, audio loading, per-reviewer storage, rating export and mobile layout');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode=1; });
