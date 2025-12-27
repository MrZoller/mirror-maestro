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
  { file: 'demo-tokens.html', output: '04-tokens.png', name: 'Group Settings' },
  { file: 'demo-mirrors.html', output: '05-mirrors.png', name: 'Mirrors' },
  { file: 'demo-topology.html', output: '06-topology.png', name: 'Topology' },
  { file: 'demo-dashboard-dark.html', output: '07-dashboard-dark.png', name: 'Dashboard (Dark Mode)' }
];

async function takeScreenshots() {
  console.log('🚀 Starting screenshot capture...\n');

  // Launch browser
  const browser = await chromium.launch({
    headless: true
  });

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
