# Screenshot Generation Guide

This guide explains how to generate screenshots for the GitLab Mirror Wizard documentation.

## Quick Start

### Option 1: Use Demo HTML Files (Easiest)

The demo HTML files are pre-populated with sample data and ready to screenshot:

1. **Navigate to the screenshots directory:**
   ```bash
   cd docs/screenshots
   ```

2. **Open each demo file in your browser:**
   - `demo-dashboard.html` - Dashboard view
   - `demo-instances.html` - GitLab Instances
   - `demo-pairs.html` - Instance Pairs
   - `demo-tokens.html` - Group Settings
   - `demo-topology.html` - Topology
   - `demo-mirrors.html` - Mirrors management

3. **Take screenshots:**
   - **macOS**: Press `Cmd + Shift + 4`, then press `Space`, click on browser window
   - **Windows**: Press `Win + Shift + S`, select area
   - **Linux**: Press `Shift + PrtScn`, select area

4. **Save screenshots as:**
   - `01-dashboard.png`
   - `02-instances.png`
   - `03-pairs.png`
   - `04-tokens.png`
   - `05-mirrors.png`
   - `06-topology.png`

### Option 2: Automated with Playwright

1. **Install dependencies:**
   ```bash
   cd scripts
   npm install
   ```

2. **Install Playwright browsers:**
   ```bash
   npx playwright install chromium
   ```

3. **Run the screenshot script:**
   ```bash
   npm run screenshots
   ```

   Or directly:
   ```bash
   node take-screenshots.js
   ```

### Option 3: Use Live Application

1. **Seed the database with sample data:**
   ```bash
   python scripts/seed_data.py
   ```

2. **Start the application:**
   ```bash
   # Using Docker
   docker-compose up

   # Or locally
   uvicorn app.main:app --reload
   ```

3. **Open in browser:**
   ```
   http://localhost:8000
   ```

4. **Navigate through tabs and take screenshots**

## Screenshot Specifications

- **Format**: PNG
- **Minimum Width**: 1400px
- **Recommended Resolution**: 1920x1080
- **Browser Zoom**: 100%
- **Quality**: High (avoid compression artifacts)

## Browser Settings for Best Screenshots

### Chrome/Edge
1. Press `F11` for fullscreen (optional but recommended)
2. Press `F12` to open DevTools
3. Click the device toolbar icon (or press `Ctrl+Shift+M`)
4. Set viewport to `1920 x 1080`
5. Take screenshot using DevTools screenshot feature

### Firefox
1. Press `F12` to open Developer Tools
2. Click the responsive design mode icon (or press `Ctrl+Shift+M`)
3. Set dimensions to `1920 x 1080`
4. Use Firefox's built-in screenshot feature (`Ctrl+Shift+S`)

## Post-Processing (Optional)

After taking screenshots, you can optimize them:

```bash
# Using optipng
optipng -o5 docs/screenshots/*.png

# Using pngquant
pngquant --quality=80-95 docs/screenshots/*.png

# Using ImageMagick (resize if needed)
mogrify -resize 1920x docs/screenshots/*.png
```

## Troubleshooting

### CSS Not Loading in Demo Files
The demo HTML files reference `../../app/static/css/style.css`. Make sure you're opening them from the correct directory or use a local web server:

```bash
# Python
python -m http.server 8080

# Node.js
npx http-server

# Then open: http://localhost:8080/docs/screenshots/demo-dashboard.html
```

### Playwright Installation Issues

If Playwright installation fails:

```bash
# Install system dependencies (Ubuntu/Debian)
sudo apt-get install -y \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2

# Then reinstall Playwright
npx playwright install chromium
```

## Tips for Great Screenshots

1. **Clean Browser**: Use incognito/private mode to avoid extensions
2. **Consistent Window Size**: Use the same window size for all screenshots
3. **Hide OS Elements**: Focus on the application content
4. **Good Timing**: Wait for animations to complete before capturing
5. **Check Quality**: Review screenshots before using them

## Updating README

After generating screenshots, they're automatically referenced in the main README.md. Just commit and push:

```bash
git add docs/screenshots/*.png
git commit -m "Add application screenshots"
git push
```
