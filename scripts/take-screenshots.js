/**
 * Automated screenshot capture script using Playwright
 *
 * Prerequisites:
 *   npm install playwright
 *
 * Usage:
 *   node scripts/take-screenshots.js
 */

const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const SCREENSHOT_DIR = path.join(__dirname, '..', 'docs', 'screenshots');
const DEMO_FILES = [
  { file: 'demo-dashboard.html', output: '01-dashboard.png', name: 'Dashboard' },
  { file: 'demo-instances.html', output: '02-instances.png', name: 'GitLab Instances' },
  { file: 'demo-pairs.html', output: '03-pairs.png', name: 'Instance Pairs' },
  { file: 'demo-mirrors.html', output: '04-mirrors.png', name: 'Mirrors' },
  { file: 'demo-issue-config-bottom.html', output: '04b-issue-config.png', name: 'Issue Configuration' },
  { file: 'demo-topology.html', output: '05-topology.png', name: 'Topology' },
  { file: 'demo-backup.html', output: '06-backup.png', name: 'Backup' },
  { file: 'demo-settings.html', output: '07-settings.png', name: 'Settings' },
  { file: 'demo-about.html', output: '08-about.png', name: 'About' },
  { file: 'demo-help.html', output: '09-help.png', name: 'Help' }
];

async function takeScreenshots() {
  console.log('🚀 Starting screenshot capture...\n');

  // Launch browser (supports PLAYWRIGHT_CHROMIUM_PATH env override)
  const launchOpts = { headless: true };
  if (process.env.PLAYWRIGHT_CHROMIUM_PATH) {
    launchOpts.executablePath = process.env.PLAYWRIGHT_CHROMIUM_PATH;
    console.log(`   Using Chromium at ${launchOpts.executablePath}`);
  }
  const browser = await chromium.launch(launchOpts);

  const context = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    deviceScaleFactor: 2 // Retina display quality
  });

  const page = await context.newPage();

  // Take screenshots of each demo file
  for (const demo of DEMO_FILES) {
    const filePath = path.join(SCREENSHOT_DIR, demo.file);
    const outputPath = path.join(SCREENSHOT_DIR, demo.output);

    console.log(`📸 Capturing ${demo.name}...`);
    console.log(`   File: ${demo.file}`);
    console.log(`   Output: ${demo.output}`);

    // Load the demo HTML file
    await page.goto(`file://${filePath}`, { waitUntil: 'networkidle' });

    // Wait for any animations to complete (longer for topology and dashboard with charts)
    const waitTime = demo.file.includes('topology') ? 2000 : demo.file.includes('dashboard') ? 1500 : 1000;
    await page.waitForTimeout(waitTime);

    // Take screenshot
    await page.screenshot({
      path: outputPath,
      fullPage: true,
      type: 'png'
    });

    console.log(`   ✅ Saved to ${outputPath}\n`);
  }

  await browser.close();

  console.log('✨ All screenshots captured successfully!\n');
  console.log('Screenshots saved to:', SCREENSHOT_DIR);
  console.log('\nGenerated files:');
  DEMO_FILES.forEach(demo => {
    console.log(`  - ${demo.output}`);
  });
}

// Run the script
takeScreenshots().catch(error => {
  console.error('❌ Error taking screenshots:', error);
  process.exit(1);
});
