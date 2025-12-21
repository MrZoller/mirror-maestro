// State management
const state = {
    instances: [],
    pairs: [],
    mirrors: [],
    tokens: [],
    groupDefaults: [],
    selectedPair: null
};

// Initialize the application
document.addEventListener('DOMContentLoaded', () => {
    initTabs();
    loadInstances();
    loadPairs();
    setupEventListeners();
});

// Tab management
function initTabs() {
    const tabs = document.querySelectorAll('.tab');
    const tabContents = document.querySelectorAll('.tab-content');

    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            const targetId = tab.dataset.tab;

            tabs.forEach(t => t.classList.remove('active'));
            tabContents.forEach(tc => tc.classList.remove('active'));

            tab.classList.add('active');
            document.getElementById(targetId).classList.add('active');

            // Load data when switching to specific tabs
            if (targetId === 'mirrors-tab') {
                loadMirrors();
            } else if (targetId === 'tokens-tab') {
                loadTokens();
                loadGroupDefaults();
            }
        });
    });
}

// Setup event listeners
function setupEventListeners() {
    // Instance form
    document.getElementById('instance-form')?.addEventListener('submit', async (e) => {
        e.preventDefault();
        await createInstance();
    });

    // Pair form
    document.getElementById('pair-form')?.addEventListener('submit', async (e) => {
        e.preventDefault();
        await createPair();
    });

    // Pair direction changes should toggle which settings apply
    document.getElementById('pair-direction')?.addEventListener('change', (e) => {
        const direction = (e.target.value || '').toString().toLowerCase();
        const isPush = direction === 'push';

        const trigger = document.getElementById('pair-trigger');
        if (trigger) {
            trigger.disabled = isPush;
            if (isPush) trigger.checked = false;
        }

        const regex = document.getElementById('pair-branch-regex');
        if (regex) {
            regex.disabled = isPush;
            if (isPush) regex.value = '';
        }

        const userId = document.getElementById('pair-mirror-user-id');
        if (userId) {
            userId.disabled = isPush;
            if (isPush) userId.value = '';
        }
    });

    // Mirror form
    document.getElementById('mirror-form')?.addEventListener('submit', async (e) => {
        e.preventDefault();
        await createMirror();
    });

    // Mirror direction override (or "use pair default") toggles applicable fields
    document.getElementById('mirror-direction')?.addEventListener('change', (e) => {
        const selected = (e.target.value || '').toString().toLowerCase();
        if (selected) {
            applyMirrorDirectionUI(selected);
            return;
        }
        // Use pair default if present
        const pair = state.pairs.find(p => p.id === state.selectedPair);
        applyMirrorDirectionUI(pair?.mirror_direction);
    });

    // Pair selector for mirrors
    document.getElementById('pair-selector')?.addEventListener('change', (e) => {
        state.selectedPair = parseInt(e.target.value);
        if (state.selectedPair) {
            loadMirrors();
            loadProjectsForPair();
        }
    });

    // Token form
    document.getElementById('token-form')?.addEventListener('submit', async (e) => {
        e.preventDefault();
        await createToken();
    });

    // Group defaults form
    document.getElementById('group-defaults-form')?.addEventListener('submit', async (e) => {
        e.preventDefault();
        await upsertGroupDefaults();
    });
}

// API Helper
async function apiRequest(url, options = {}) {
    try {
        const response = await fetch(url, {
            ...options,
            headers: {
                'Content-Type': 'application/json',
                ...options.headers
            }
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Request failed');
        }

        return await response.json();
    } catch (error) {
        showMessage(error.message, 'error');
        throw error;
    }
}

// GitLab Instances
async function loadInstances() {
    try {
        const instances = await apiRequest('/api/instances');
        state.instances = instances;
        renderInstances(instances);
        updateInstanceSelectors();
    } catch (error) {
        console.error('Failed to load instances:', error);
    }
}

function renderInstances(instances) {
    const tbody = document.getElementById('instances-list');
    if (!tbody) return;

    if (instances.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" class="text-center text-muted">No instances configured</td></tr>';
        return;
    }

    tbody.innerHTML = instances.map(instance => `
        <tr>
            <td><strong>${escapeHtml(instance.name)}</strong></td>
            <td>${escapeHtml(instance.url)}</td>
            <td><span class="text-muted">${escapeHtml(instance.description || 'N/A')}</span></td>
            <td>
                <button class="btn btn-danger btn-small" onclick="deleteInstance(${instance.id})">Delete</button>
            </td>
        </tr>
    `).join('');
}

async function createInstance() {
    const form = document.getElementById('instance-form');
    const formData = new FormData(form);

    const data = {
        name: formData.get('name'),
        url: formData.get('url'),
        token: formData.get('token'),
        description: formData.get('description') || ''
    };

    try {
        await apiRequest('/api/instances', {
            method: 'POST',
            body: JSON.stringify(data)
        });

        showMessage('Instance created successfully', 'success');
        form.reset();
        await loadInstances();
    } catch (error) {
        console.error('Failed to create instance:', error);
    }
}

async function deleteInstance(id) {
    if (!confirm('Are you sure you want to delete this instance?')) return;

    try {
        await apiRequest(`/api/instances/${id}`, { method: 'DELETE' });
        showMessage('Instance deleted successfully', 'success');
        await loadInstances();
    } catch (error) {
        console.error('Failed to delete instance:', error);
    }
}

function updateInstanceSelectors() {
    const sourceSelect = document.getElementById('pair-source-instance');
    const targetSelect = document.getElementById('pair-target-instance');

    [sourceSelect, targetSelect].forEach(select => {
        if (!select) return;
        select.innerHTML = '<option value="">Select instance...</option>' +
            state.instances.map(inst =>
                `<option value="${inst.id}">${escapeHtml(inst.name)}</option>`
            ).join('');
    });
}

// Instance Pairs
async function loadPairs() {
    try {
        const pairs = await apiRequest('/api/pairs');
        state.pairs = pairs;
        renderPairs(pairs);
        renderPairDefaults(pairs);
        updatePairSelector();
    } catch (error) {
        console.error('Failed to load pairs:', error);
    }
}

function renderPairs(pairs) {
    const tbody = document.getElementById('pairs-list');
    if (!tbody) return;

    if (pairs.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted">No instance pairs configured</td></tr>';
        return;
    }

    tbody.innerHTML = pairs.map(pair => {
        const source = state.instances.find(i => i.id === pair.source_instance_id);
        const target = state.instances.find(i => i.id === pair.target_instance_id);

        return `
            <tr>
                <td><strong>${escapeHtml(pair.name)}</strong></td>
                <td>${escapeHtml(source?.name || 'Unknown')}</td>
                <td>${escapeHtml(target?.name || 'Unknown')}</td>
                <td><span class="badge badge-info">${pair.mirror_direction}</span></td>
                <td>
                    <button class="btn btn-danger btn-small" onclick="deletePair(${pair.id})">Delete</button>
                </td>
            </tr>
        `;
    }).join('');
}

function renderPairDefaults(pairs) {
    const tbody = document.getElementById('pair-defaults-list');
    if (!tbody) return;

    if (pairs.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted">No instance pairs configured</td></tr>';
        return;
    }

    const fmtBool = (v) => v ? 'true' : 'false';
    const fmtStr = (v) => (v === null || v === undefined || String(v).trim() === '') ? '<span class="text-muted">N/A</span>' : escapeHtml(String(v));
    const badge = (dir) => `<span class="badge badge-info">${escapeHtml((dir || '').toString().toLowerCase() || 'n/a')}</span>`;

    tbody.innerHTML = pairs.map(pair => `
        <tr>
            <td><strong>${escapeHtml(pair.name)}</strong></td>
            <td>${badge(pair.mirror_direction)}</td>
            <td>${fmtBool(!!pair.mirror_protected_branches)}</td>
            <td>${fmtBool(!!pair.mirror_overwrite_diverged)}</td>
            <td>${fmtBool(!!pair.mirror_trigger_builds)}</td>
            <td>${fmtBool(!!pair.only_mirror_protected_branches)}</td>
            <td>${fmtStr(pair.mirror_branch_regex)}</td>
            <td>${fmtStr(pair.mirror_user_id)}</td>
        </tr>
    `).join('');
}

async function createPair() {
    const form = document.getElementById('pair-form');
    const formData = new FormData(form);

    const branchRegexRaw = (formData.get('mirror_branch_regex') || '').toString().trim();
    const mirrorUserIdRaw = (formData.get('mirror_user_id') || '').toString().trim();

    const data = {
        name: formData.get('name'),
        source_instance_id: parseInt(formData.get('source_instance_id')),
        target_instance_id: parseInt(formData.get('target_instance_id')),
        mirror_direction: formData.get('mirror_direction'),
        mirror_protected_branches: formData.get('mirror_protected_branches') === 'on',
        mirror_overwrite_diverged: formData.get('mirror_overwrite_diverged') === 'on',
        mirror_trigger_builds: formData.get('mirror_trigger_builds') === 'on',
        only_mirror_protected_branches: formData.get('only_mirror_protected_branches') === 'on',
        mirror_branch_regex: branchRegexRaw || null,
        mirror_user_id: mirrorUserIdRaw ? parseInt(mirrorUserIdRaw) : null,
        description: formData.get('description') || ''
    };

    try {
        await apiRequest('/api/pairs', {
            method: 'POST',
            body: JSON.stringify(data)
        });

        showMessage('Instance pair created successfully', 'success');
        form.reset();
        await loadPairs();
    } catch (error) {
        console.error('Failed to create pair:', error);
    }
}

async function deletePair(id) {
    if (!confirm('Are you sure you want to delete this instance pair?')) return;

    try {
        await apiRequest(`/api/pairs/${id}`, { method: 'DELETE' });
        showMessage('Instance pair deleted successfully', 'success');
        await loadPairs();
    } catch (error) {
        console.error('Failed to delete pair:', error);
    }
}

function updatePairSelector() {
    const select = document.getElementById('pair-selector');
    if (!select) return;

    select.innerHTML = '<option value="">Select instance pair...</option>' +
        state.pairs.map(pair =>
            `<option value="${pair.id}">${escapeHtml(pair.name)}</option>`
        ).join('');
}

// Group Access Tokens
async function loadTokens() {
    try {
        const tokens = await apiRequest('/api/tokens');
        state.tokens = tokens;
        renderTokens(tokens);
        updateTokenInstanceSelector();
        updateGroupDefaultsPairSelector();
        resetGroupDefaultsOverrides();
    } catch (error) {
        console.error('Failed to load tokens:', error);
    }
}

function renderTokens(tokens) {
    const tbody = document.getElementById('tokens-list');
    if (!tbody) return;

    if (tokens.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted">No group access tokens configured</td></tr>';
        return;
    }

    tbody.innerHTML = tokens.map(token => {
        const instance = state.instances.find(i => i.id === token.gitlab_instance_id);
        const created = new Date(token.created_at).toLocaleDateString();

        return `
            <tr>
                <td><strong>${escapeHtml(instance?.name || 'Unknown')}</strong></td>
                <td>${escapeHtml(token.group_path)}</td>
                <td>${escapeHtml(token.token_name)}</td>
                <td>${created}</td>
                <td>
                    <button class="btn btn-danger btn-small" onclick="deleteToken(${token.id})">Delete</button>
                </td>
            </tr>
        `;
    }).join('');
}

async function createToken() {
    const form = document.getElementById('token-form');
    const formData = new FormData(form);

    const data = {
        gitlab_instance_id: parseInt(formData.get('gitlab_instance_id')),
        group_path: formData.get('group_path'),
        token_name: formData.get('token_name'),
        token: formData.get('token')
    };

    try {
        await apiRequest('/api/tokens', {
            method: 'POST',
            body: JSON.stringify(data)
        });

        showMessage('Group access token added successfully', 'success');
        form.reset();
        await loadTokens();
    } catch (error) {
        console.error('Failed to create token:', error);
    }
}

async function deleteToken(id) {
    if (!confirm('Are you sure you want to delete this group access token?')) return;

    try {
        await apiRequest(`/api/tokens/${id}`, { method: 'DELETE' });
        showMessage('Group access token deleted successfully', 'success');
        await loadTokens();
    } catch (error) {
        console.error('Failed to delete token:', error);
    }
}

function updateTokenInstanceSelector() {
    const select = document.getElementById('token-instance');
    if (!select) return;

    select.innerHTML = '<option value="">Select instance...</option>' +
        state.instances.map(inst =>
            `<option value="${inst.id}">${escapeHtml(inst.name)}</option>`
        ).join('');
}

// Group Mirror Defaults
async function loadGroupDefaults() {
    try {
        const rows = await apiRequest('/api/group-defaults');
        state.groupDefaults = rows;
        renderGroupDefaults(rows);
        updateGroupDefaultsPairSelector();
        resetGroupDefaultsOverrides();
    } catch (error) {
        console.error('Failed to load group defaults:', error);
    }
}

function renderGroupDefaults(rows) {
    const tbody = document.getElementById('group-defaults-list');
    if (!tbody) return;

    if (rows.length === 0) {
        tbody.innerHTML = '<tr><td colspan="9" class="text-center text-muted">No group defaults configured</td></tr>';
        return;
    }

    tbody.innerHTML = rows.map(r => {
        const pair = state.pairs.find(p => p.id === r.instance_pair_id);
        const b = (v) => (v === null || v === undefined) ? '<span class="text-muted">inherit</span>' : (v ? 'true' : 'false');
        const s = (v) => (v === null || v === undefined || v === '') ? '<span class="text-muted">inherit</span>' : escapeHtml(String(v));
        return `
            <tr>
                <td><strong>${escapeHtml(pair?.name || 'Unknown')}</strong></td>
                <td>${escapeHtml(r.group_path)}</td>
                <td>${s(r.mirror_direction)}</td>
                <td>${b(r.mirror_overwrite_diverged)}</td>
                <td>${b(r.mirror_trigger_builds)}</td>
                <td>${b(r.only_mirror_protected_branches)}</td>
                <td>${s(r.mirror_branch_regex)}</td>
                <td>${s(r.mirror_user_id)}</td>
                <td>
                    <button class="btn btn-danger btn-small" onclick="deleteGroupDefaults(${r.id})">Delete</button>
                </td>
            </tr>
        `;
    }).join('');
}

function updateGroupDefaultsPairSelector() {
    const select = document.getElementById('group-defaults-pair');
    if (!select) return;

    select.innerHTML = '<option value="">Select instance pair...</option>' +
        state.pairs.map(pair =>
            `<option value="${pair.id}">${escapeHtml(pair.name)}</option>`
        ).join('');
}

function resetGroupDefaultsOverrides() {
    const triStateCheckboxIds = [
        'group-defaults-overwrite',
        'group-defaults-trigger',
        'group-defaults-only-protected',
    ];
    triStateCheckboxIds.forEach((id) => {
        const el = document.getElementById(id);
        if (!el) return;
        el.checked = false;
        el.indeterminate = true;
        el.title = 'Inherit pair default';
        el.onchange = () => {
            el.indeterminate = false;
            el.title = '';
        };
    });

    const dir = document.getElementById('group-defaults-direction');
    if (dir) dir.value = '';

    const regex = document.getElementById('group-defaults-branch-regex');
    if (regex) regex.value = '';

    const userId = document.getElementById('group-defaults-mirror-user-id');
    if (userId) userId.value = '';
}

async function upsertGroupDefaults() {
    const form = document.getElementById('group-defaults-form');
    const formData = new FormData(form);

    const overwriteEl = document.getElementById('group-defaults-overwrite');
    const triggerEl = document.getElementById('group-defaults-trigger');
    const onlyProtectedEl = document.getElementById('group-defaults-only-protected');

    const branchRegexRaw = (formData.get('mirror_branch_regex') || '').toString().trim();
    const mirrorUserIdRaw = (formData.get('mirror_user_id') || '').toString().trim();
    const dirRaw = (formData.get('mirror_direction') || '').toString().trim();

    const payload = {
        instance_pair_id: parseInt(formData.get('instance_pair_id')),
        group_path: (formData.get('group_path') || '').toString().trim(),
        mirror_direction: dirRaw || null,
        mirror_overwrite_diverged: overwriteEl && overwriteEl.indeterminate ? null : !!overwriteEl?.checked,
        mirror_trigger_builds: triggerEl && triggerEl.indeterminate ? null : !!triggerEl?.checked,
        only_mirror_protected_branches: onlyProtectedEl && onlyProtectedEl.indeterminate ? null : !!onlyProtectedEl?.checked,
        mirror_branch_regex: branchRegexRaw || null,
        mirror_user_id: mirrorUserIdRaw ? parseInt(mirrorUserIdRaw) : null,
    };

    try {
        await apiRequest('/api/group-defaults', {
            method: 'POST',
            body: JSON.stringify(payload)
        });
        showMessage('Group defaults saved successfully', 'success');
        form.reset();
        await loadGroupDefaults();
    } catch (error) {
        console.error('Failed to save group defaults:', error);
    }
}

async function deleteGroupDefaults(id) {
    if (!confirm('Are you sure you want to delete these group defaults?')) return;
    try {
        await apiRequest(`/api/group-defaults/${id}`, { method: 'DELETE' });
        showMessage('Group defaults deleted successfully', 'success');
        await loadGroupDefaults();
    } catch (error) {
        console.error('Failed to delete group defaults:', error);
    }
}

// Mirrors
async function loadMirrors() {
    if (!state.selectedPair) {
        document.getElementById('mirrors-list').innerHTML =
            '<tr><td colspan="6" class="text-center text-muted">Select an instance pair to view mirrors</td></tr>';
        return;
    }

    try {
        const mirrors = await apiRequest(`/api/mirrors?instance_pair_id=${state.selectedPair}`);
        state.mirrors = mirrors;
        renderMirrors(mirrors);
    } catch (error) {
        console.error('Failed to load mirrors:', error);
    }
}

function renderMirrors(mirrors) {
    const tbody = document.getElementById('mirrors-list');
    if (!tbody) return;

    if (mirrors.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted">No mirrors configured for this pair</td></tr>';
        return;
    }

    tbody.innerHTML = mirrors.map(mirror => {
        const statusBadge = mirror.enabled ?
            `<span class="badge badge-success">Enabled</span>` :
            `<span class="badge badge-warning">Disabled</span>`;

        const updateStatus = mirror.last_update_status ?
            `<span class="badge badge-info">${mirror.last_update_status}</span>` :
            '<span class="text-muted">N/A</span>';

        return `
            <tr>
                <td>${escapeHtml(mirror.source_project_path)}</td>
                <td>${escapeHtml(mirror.target_project_path)}</td>
                <td>${statusBadge}</td>
                <td>${updateStatus}</td>
                <td>${mirror.last_successful_update ? new Date(mirror.last_successful_update).toLocaleString() : 'Never'}</td>
                <td>
                    <button class="btn btn-success btn-small" onclick="triggerMirrorUpdate(${mirror.id})">Update</button>
                    <button class="btn btn-danger btn-small" onclick="deleteMirror(${mirror.id})">Delete</button>
                </td>
            </tr>
        `;
    }).join('');
}

async function loadProjectsForPair() {
    if (!state.selectedPair) return;

    const pair = state.pairs.find(p => p.id === state.selectedPair);
    if (!pair) return;

    try {
        resetMirrorOverrides();
        applyMirrorDirectionUI(pair.mirror_direction);

        // Load source projects
        const sourceProjects = await apiRequest(`/api/instances/${pair.source_instance_id}/projects`);
        const sourceSelect = document.getElementById('mirror-source-project');
        sourceSelect.innerHTML = '<option value="">Select source project...</option>' +
            sourceProjects.projects.map(p =>
                `<option value="${p.id}" data-path="${p.path_with_namespace}">${escapeHtml(p.path_with_namespace)}</option>`
            ).join('');

        // Load target projects
        const targetProjects = await apiRequest(`/api/instances/${pair.target_instance_id}/projects`);
        const targetSelect = document.getElementById('mirror-target-project');
        targetSelect.innerHTML = '<option value="">Select target project...</option>' +
            targetProjects.projects.map(p =>
                `<option value="${p.id}" data-path="${p.path_with_namespace}">${escapeHtml(p.path_with_namespace)}</option>`
            ).join('');
    } catch (error) {
        console.error('Failed to load projects:', error);
    }
}

function resetMirrorOverrides() {
    // Tri-state checkboxes (indeterminate => "use pair default" / don't send)
    const triStateCheckboxIds = [
        'mirror-overwrite',
        'mirror-trigger',
        'mirror-only-protected',
    ];
    triStateCheckboxIds.forEach((id) => {
        const el = document.getElementById(id);
        if (!el) return;
        el.checked = false;
        el.indeterminate = true;
        el.title = 'Use pair default';
        el.onchange = () => {
            // Once the user interacts, treat it as an explicit override.
            el.indeterminate = false;
            el.title = '';
        };
    });

    const regex = document.getElementById('mirror-branch-regex');
    if (regex) regex.value = '';
    const userId = document.getElementById('mirror-mirror-user-id');
    if (userId) userId.value = '';

    const dir = document.getElementById('mirror-direction');
    if (dir) dir.value = '';

    const enabled = document.getElementById('mirror-enabled');
    if (enabled) enabled.checked = true;
}

function applyMirrorDirectionUI(direction) {
    const effective = (direction || '').toString().toLowerCase();
    const isPush = effective === 'push';

    const triggerEl = document.getElementById('mirror-trigger');
    if (triggerEl) {
        triggerEl.disabled = isPush;
        if (isPush) {
            triggerEl.checked = false;
            triggerEl.indeterminate = true;
            triggerEl.title = 'Not applicable for push mirrors';
        }
    }

    const regex = document.getElementById('mirror-branch-regex');
    if (regex) {
        regex.disabled = isPush;
        if (isPush) regex.value = '';
    }

    const userId = document.getElementById('mirror-mirror-user-id');
    if (userId) {
        userId.disabled = isPush;
        if (isPush) userId.value = '';
    }
}

async function createMirror() {
    if (!state.selectedPair) {
        showMessage('Please select an instance pair first', 'error');
        return;
    }

    const form = document.getElementById('mirror-form');
    const formData = new FormData(form);

    const sourceSelect = document.getElementById('mirror-source-project');
    const targetSelect = document.getElementById('mirror-target-project');

    const sourceOption = sourceSelect.selectedOptions[0];
    const targetOption = targetSelect.selectedOptions[0];

    const data = {
        instance_pair_id: state.selectedPair,
        source_project_id: parseInt(sourceSelect.value),
        source_project_path: sourceOption.dataset.path,
        target_project_id: parseInt(targetSelect.value),
        target_project_path: targetOption.dataset.path,
        enabled: formData.get('enabled') === 'on'
    };

    const mirrorDirection = (formData.get('mirror_direction') || '').toString().trim();
    const pair = state.pairs.find(p => p.id === state.selectedPair);
    const effectiveDirection = (mirrorDirection || pair?.mirror_direction || '').toString().toLowerCase();
    const isPush = effectiveDirection === 'push';
    if (mirrorDirection) {
        data.mirror_direction = mirrorDirection;
    }

    const overwriteEl = document.getElementById('mirror-overwrite');
    if (overwriteEl && !overwriteEl.indeterminate) {
        data.mirror_overwrite_diverged = overwriteEl.checked;
    }
    const triggerEl = document.getElementById('mirror-trigger');
    if (triggerEl && !triggerEl.indeterminate && !isPush) {
        data.mirror_trigger_builds = triggerEl.checked;
    }
    const onlyProtectedEl = document.getElementById('mirror-only-protected');
    if (onlyProtectedEl && !onlyProtectedEl.indeterminate) {
        data.only_mirror_protected_branches = onlyProtectedEl.checked;
    }

    const regexRaw = (formData.get('mirror_branch_regex') || '').toString().trim();
    if (regexRaw && !isPush) {
        data.mirror_branch_regex = regexRaw;
    }
    const mirrorUserIdRaw = (formData.get('mirror_user_id') || '').toString().trim();
    if (mirrorUserIdRaw && !isPush) {
        data.mirror_user_id = parseInt(mirrorUserIdRaw);
    }

    try {
        await apiRequest('/api/mirrors', {
            method: 'POST',
            body: JSON.stringify(data)
        });

        showMessage('Mirror created successfully', 'success');
        document.getElementById('mirror-form').reset();
        await loadMirrors();
    } catch (error) {
        console.error('Failed to create mirror:', error);
    }
}

async function triggerMirrorUpdate(id) {
    try {
        await apiRequest(`/api/mirrors/${id}/update`, { method: 'POST' });
        showMessage('Mirror update triggered', 'success');
        await loadMirrors();
    } catch (error) {
        console.error('Failed to trigger mirror update:', error);
    }
}

async function deleteMirror(id) {
    if (!confirm('Are you sure you want to delete this mirror?')) return;

    try {
        await apiRequest(`/api/mirrors/${id}`, { method: 'DELETE' });
        showMessage('Mirror deleted successfully', 'success');
        await loadMirrors();
    } catch (error) {
        console.error('Failed to delete mirror:', error);
    }
}

// Import/Export
async function exportMirrors() {
    if (!state.selectedPair) {
        showMessage('Please select an instance pair first', 'error');
        return;
    }

    try {
        const response = await fetch(`/api/export/pair/${state.selectedPair}`);
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `mirrors_export_${Date.now()}.json`;
        a.click();
        window.URL.revokeObjectURL(url);
        showMessage('Mirrors exported successfully', 'success');
    } catch (error) {
        console.error('Failed to export mirrors:', error);
        showMessage('Failed to export mirrors', 'error');
    }
}

async function importMirrors() {
    if (!state.selectedPair) {
        showMessage('Please select an instance pair first', 'error');
        return;
    }

    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json';
    input.onchange = async (e) => {
        const file = e.target.files[0];
        if (!file) return;

        try {
            const text = await file.text();
            const data = JSON.parse(text);

            const result = await apiRequest(`/api/export/pair/${state.selectedPair}`, {
                method: 'POST',
                body: JSON.stringify(data)
            });

            showMessage(`Import complete: ${result.imported} imported, ${result.skipped} skipped`, 'success');
            await loadMirrors();
        } catch (error) {
            console.error('Failed to import mirrors:', error);
            showMessage('Failed to import mirrors', 'error');
        }
    };
    input.click();
}

// Utility functions
function showMessage(message, type = 'info') {
    const container = document.getElementById('message-container');
    if (!container) return;

    const div = document.createElement('div');
    div.className = `message message-${type}`;
    div.textContent = message;

    container.appendChild(div);

    setTimeout(() => {
        div.remove();
    }, 5000);
}

function escapeHtml(text) {
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    };
    return text ? text.replace(/[&<>"']/g, m => map[m]) : '';
}
