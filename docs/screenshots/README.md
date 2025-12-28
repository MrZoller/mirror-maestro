# Screenshots

This directory contains screenshots and demo HTML files for the Mirror Maestro application.

## Demo HTML Files

The following HTML files contain mockups with sample data for taking screenshots:

- `demo-dashboard.html` - Dashboard view with quick stats
- `demo-instances.html` - GitLab Instances management view
- `demo-pairs.html` - Instance Pairs configuration view
- `demo-mirrors.html` - Mirrors management view (with token status)
- `demo-topology.html` - Topology graph view

## Taking Screenshots

### Method 1: Using Automated Script (Recommended)

Run the automated screenshot script (requires Node.js and Playwright):

```bash
# Install Playwright if not already installed
npx playwright install chromium

# Run the screenshot script
node scripts/take-screenshots.js
```

This will automatically generate all screenshots in the correct format.

### Method 2: Using Demo HTML Files (Manual)

1. Open each demo HTML file in your browser:
   ```bash
   # From the project root
   open docs/screenshots/demo-dashboard.html
   open docs/screenshots/demo-instances.html
   open docs/screenshots/demo-pairs.html
   open docs/screenshots/demo-mirrors.html
   open docs/screenshots/demo-topology.html
   ```

2. Take screenshots using your browser's built-in tools or OS screenshot utility
   - **macOS**: `Cmd + Shift + 4` (select area) or `Cmd + Shift + 3` (full screen)
   - **Windows**: `Win + Shift + S` (Snipping Tool)
   - **Linux**: `Shift + PrtScn` (area select) or `PrtScn` (full screen)

3. Save screenshots with the following names:
   - `01-dashboard.png`
   - `02-instances.png`
   - `03-pairs.png`
   - `04-mirrors.png`
   - `05-topology.png`

**Tip for Topology screenshot:** click a link to show the mirror drilldown list (health, staleness thresholds, and never-succeeded classification are visible in the details panel).

### Method 3: Using Live Application with Sample Data

1. Seed the database with sample data:
   ```bash
   python scripts/seed_data.py
   ```

2. Start the application:
   ```bash
   uvicorn app.main:app --reload
   ```

3. Open http://localhost:8000 in your browser

4. Navigate to each tab and take screenshots

## Screenshot Guidelines

- **Resolution**: 1920x1080 or higher
- **Format**: PNG (for better quality)
- **Browser**: Use a modern browser (Chrome, Firefox, Safari)
- **Window Size**: Maximize or use a consistent size (at least 1400px wide)
- **Zoom**: Use 100% zoom (no zooming in or out)
- **Clean UI**: Hide bookmarks bar and other browser UI elements if possible

## Screenshot Naming Convention

- `01-dashboard.png` - Main dashboard view
- `02-instances.png` - GitLab Instances management
- `03-pairs.png` - Instance Pairs configuration
- `04-mirrors.png` - Mirrors management (with token status badges)
- `05-topology.png` - Topology graph (health/staleness + drilldown)

## Image Optimization

After taking screenshots, you can optimize them:

```bash
# Using ImageOptim (macOS)
# Drag and drop images into ImageOptim

# Using pngquant (cross-platform)
pngquant --quality=80-90 *.png

# Using optipng (cross-platform)
optipng -o5 *.png
```

## Adding Screenshots to README

Once screenshots are generated, they'll be automatically referenced in the main README.md file.
