# Screenshots

This directory contains screenshots and demo HTML files for the Mirror Maestro application.

## Demo HTML Files

The following HTML files contain mockups with sample data for taking screenshots:

- `demo-dashboard.html` - Dashboard view with quick stats
- `demo-instances.html` - GitLab Instances management view
- `demo-pairs.html` - Instance Pairs configuration view
- `demo-tokens.html` - Group Settings view (tokens + group defaults)
- `demo-topology.html` - Topology graph view
- `demo-mirrors.html` - Mirrors management view

## Taking Screenshots

### Method 1: Using Demo HTML Files (Recommended)

1. Open each demo HTML file in your browser:
   ```bash
   # From the project root
   open docs/screenshots/demo-dashboard.html
   open docs/screenshots/demo-instances.html
   open docs/screenshots/demo-pairs.html
   open docs/screenshots/demo-tokens.html
   open docs/screenshots/demo-topology.html
   open docs/screenshots/demo-mirrors.html
   ```

2. Take screenshots using your browser's built-in tools or OS screenshot utility
   - **macOS**: `Cmd + Shift + 4` (select area) or `Cmd + Shift + 3` (full screen)
   - **Windows**: `Win + Shift + S` (Snipping Tool)
   - **Linux**: `Shift + PrtScn` (area select) or `PrtScn` (full screen)

3. Save screenshots with the following names:
   - `01-dashboard.png`
   - `02-instances.png`
   - `03-pairs.png`
   - `04-tokens.png`
   - `05-mirrors.png`
   - `06-topology.png`

**Tip for Topology screenshot:** click a link to show the mirror drilldown list (health, staleness thresholds, and never-succeeded classification are visible in the details panel).

### Method 2: Using Automated Script

Run the automated screenshot script (requires Node.js and Playwright):

```bash
cd scripts
npm install playwright
node take-screenshots.js
```

This will automatically generate all screenshots in the correct format.

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
- `04-tokens.png` - Group Settings (tokens + group defaults)
- `05-mirrors.png` - Mirrors management
- `06-topology.png` - Topology graph (health/staleness + drilldown)
- `05-mirror-detail.png` - (Optional) Detailed view of a specific mirror
 - `07-export.png` - (Optional) Import/Export functionality

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
