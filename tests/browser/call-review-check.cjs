// File-based playback regression. No server, credentials, providers or calls.
const assert = require('node:assert/strict');
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
    const tabs = page.getByRole('tab');
    await tabs.first().waitFor();
    assert.equal(await tabs.count(),3);
    assert.equal(await page.locator('#combined').isVisible(),true);
    await page.waitForFunction(() => document.getElementById('conversation').readyState >= 1);
    const duration = await page.locator('#conversation').evaluate(audio => audio.duration);
    assert.ok(duration > 100 && duration < 115);
    assert.match(await page.locator('.badge').textContent(),/Estimated alignment/);
    await page.locator('.seek').last().click();
    assert.ok(await page.locator('#conversation').evaluate(audio => audio.currentTime) > 70);
    await page.locator('#conversation').evaluate(async audio => {audio.currentTime=50; await audio.play();});
    await page.waitForFunction(() => document.getElementById('conversation').currentTime > 50.1);
    await tabs.nth(1).click();
    assert.equal(await page.locator('#originals').isVisible(),true);
    assert.equal(await page.locator('#conversation').evaluate(audio => audio.paused),true);
    assert.equal(await page.locator('#originals audio').count(),2);
    await tabs.nth(2).click();
    assert.match(await page.locator('#evidence').textContent(),/unsigned/);
    await tabs.first().focus();
    await page.keyboard.press('End');
    assert.equal(await tabs.nth(2).getAttribute('aria-selected'),'true');
    await tabs.first().click();
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    if(process.env.HARNESS_REPORT_SCREENSHOT) await page.screenshot({path:process.env.HARNESS_REPORT_SCREENSHOT});
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),true);
    if(process.env.HARNESS_REPORT_MOBILE_SCREENSHOT) await page.screenshot({path:process.env.HARNESS_REPORT_MOBILE_SCREENSHOT});
    assert.deepEqual(errors,[]);
    console.log('PASS: combined audio metadata/playback, seeking, tabs, keyboard and mobile layout');
  } finally {await browser.close();}
})().catch(error => {console.error(error);process.exitCode=1;});
