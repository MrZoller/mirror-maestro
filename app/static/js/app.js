// State management
const state = {
    instances: [],
    pairs: [],
    mirrors: [],
    tokens: [],
    groupDefaults: [],
    selectedPair: null,
    mirrorProjectInstances: { source: null, target: null }
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

    // Pair instance changes can affect token-user defaults
    document.getElementById('pair-source-instance')?.addEventListener('change', () => {
        applyPairTokenUserDefaultIfEmpty();
    });
    document.getElementById('pair-target-instance')?.addEventListener('change', () => {
        applyPairTokenUserDefaultIfEmpty();
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

    // Project typeahead for large instances (Mirrors tab)
    const srcSearch = document.getElementById('mirror-source-project-search');
    const tgtSearch = document.getElementById('mirror-target-project-search');
    if (srcSearch) {
        srcSearch.addEventListener('input', debounce(() => searchProjectsForMirror('source'), 250));
    }
    if (tgtSearch) {
        tgtSearch.addEventListener('input', debounce(() => searchProjectsForMirror('target'), 250));
    }

    // Keep the search field synced with selection
    document.getElementById('mirror-source-project')?.addEventListener('change', (e) => {
        const opt = e.target.selectedOptions?.[0];
        const path = opt?.dataset?.path;
        if (srcSearch && path) srcSearch.value = path;
    });
    document.getElementById('mirror-target-project')?.addEventListener('change', (e) => {
        const opt = e.target.selectedOptions?.[0];
        const path = opt?.dataset?.path;
        if (tgtSearch && path) tgtSearch.value = path;
    });

    // Token form
    document.getElementById('token-form')?.addEventListener('submit', async (e) => {
        e.preventDefault();
        await createToken();
    });

    // Group path typeahead (token form)
    const tokenInstanceSel = document.getElementById('token-instance');
    const tokenGroupPathEl = document.getElementById('token-group-path');
    if (tokenInstanceSel) {
        tokenInstanceSel.addEventListener('change', () => {
            clearDatalist('token-group-path-options');
        });
    }
    if (tokenGroupPathEl) {
        tokenGroupPathEl.addEventListener('input', debounce(() => searchGroupsForToken(), 250));
    }

    // Group defaults form
    document.getElementById('group-defaults-form')?.addEventListener('submit', async (e) => {
        e.preventDefault();
        await upsertGroupDefaults();
    });

    document.getElementById('group-defaults-pair')?.addEventListener('change', () => {
        applyGroupDefaultsTokenUserDefaultIfEmpty();
        clearDatalist('group-defaults-group-path-options');
    });
    document.getElementById('group-defaults-direction')?.addEventListener('change', () => {
        applyGroupDefaultsTokenUserDefaultIfEmpty();
    });

    // Group path typeahead (group defaults form)
    const groupDefaultsPathEl = document.getElementById('group-defaults-group-path');
    if (groupDefaultsPathEl) {
        groupDefaultsPathEl.addEventListener('input', debounce(() => searchGroupsForGroupDefaults(), 250));
    }
}

function clearDatalist(datalistId) {
    const dl = document.getElementById(datalistId);
    if (!dl) return;
    dl.innerHTML = '';
}

function setDatalistOptions(datalistId, values) {
    const dl = document.getElementById(datalistId);
    if (!dl) return;
    dl.innerHTML = values.map(v => `<option value="${escapeHtml(String(v))}"></option>`).join('');
}

async function searchGroupsForToken() {
    const instanceSel = document.getElementById('token-instance');
    const input = document.getElementById('token-group-path');
    if (!instanceSel || !input) return;

    const instanceId = parseInt(instanceSel.value || '0');
    if (!instanceId) {
        clearDatalist('token-group-path-options');
        return;
    }

    const q = (input.value || '').toString().trim();
    if (q.length < 2) {
        clearDatalist('token-group-path-options');
        return;
    }

    const perPage = 50;
    try {
        const res = await apiRequest(
            `/api/instances/${instanceId}/groups?search=${encodeURIComponent(q)}&per_page=${perPage}&page=1&get_all=false`
        );
        const groups = res?.groups || [];
        const values = groups
            .map(g => (g.full_path || g.path || g.name || '').toString())
            .filter(v => v);
        setDatalistOptions('token-group-path-options', values.slice(0, perPage));
    } catch (e) {
        // Suggestions are best-effort; ignore errors here.
        clearDatalist('token-group-path-options');
    }
}

async function searchGroupsForGroupDefaults() {
    const pairSel = document.getElementById('group-defaults-pair');
    const input = document.getElementById('group-defaults-group-path');
    if (!pairSel || !input) return;

    const pairId = parseInt(pairSel.value || '0');
    const pair = state.pairs.find(p => p.id === pairId);
    if (!pair) {
        clearDatalist('group-defaults-group-path-options');
        return;
    }

    const q = (input.value || '').toString().trim();
    if (q.length < 2) {
        clearDatalist('group-defaults-group-path-options');
        return;
    }

    const perPage = 50;
    const urls = [
        `/api/instances/${pair.source_instance_id}/groups?search=${encodeURIComponent(q)}&per_page=${perPage}&page=1&get_all=false`,
        `/api/instances/${pair.target_instance_id}/groups?search=${encodeURIComponent(q)}&per_page=${perPage}&page=1&get_all=false`,
    ];

    try {
        const results = await Promise.allSettled(urls.map(u => apiRequest(u)));
        const seen = new Set();
        const values = [];
        results.forEach(r => {
            if (r.status !== 'fulfilled') return;
            const groups = r.value?.groups || [];
            groups.forEach(g => {
                const v = (g.full_path || g.path || g.name || '').toString();
                if (!v || seen.has(v)) return;
                seen.add(v);
                values.push(v);
            });
        });
        setDatalistOptions('group-defaults-group-path-options', values.slice(0, perPage));
    } catch (e) {
        clearDatalist('group-defaults-group-path-options');
    }
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
        updatePairSelector();
    } catch (error) {
        console.error('Failed to load pairs:', error);
    }
}

function renderPairs(pairs) {
    const tbody = document.getElementById('pairs-list');
    if (!tbody) return;

    if (pairs.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted">No instance pairs configured</td></tr>';
        return;
    }

    const fmtBool = (v) => v ? 'true' : 'false';
    const fmtStr = (v) => (v === null || v === undefined || String(v).trim() === '')
        ? '<span class="text-muted">n/a</span>'
        : `<code>${escapeHtml(String(v))}</code>`;
    const badge = (dir) => `<span class="badge badge-info">${escapeHtml((dir || '').toString().toLowerCase() || 'n/a')}</span>`;

    tbody.innerHTML = pairs.map(pair => {
        const source = state.instances.find(i => i.id === pair.source_instance_id);
        const target = state.instances.find(i => i.id === pair.target_instance_id);

        const direction = (pair.mirror_direction || '').toString().toLowerCase();
        const ownerInstanceId = direction === 'push' ? pair.source_instance_id : pair.target_instance_id;
        const ownerInst = state.instances.find(i => i.id === ownerInstanceId);

        const fmtUser = () => {
            if (direction === 'push') return '<span class="text-muted">n/a</span>';
            if (pair.mirror_user_id === null || pair.mirror_user_id === undefined) return '<span class="text-muted">auto</span>';
            if (ownerInst && ownerInst.token_user_id === pair.mirror_user_id && ownerInst.token_username) {
                return `${escapeHtml(ownerInst.token_username)} <span class="text-muted">(#${escapeHtml(String(pair.mirror_user_id))})</span>`;
            }
            return escapeHtml(String(pair.mirror_user_id));
        };

        const settingsCell = (() => {
            const pieces = [];
            pieces.push(`<span class="text-muted">overwrite:</span> ${fmtBool(!!pair.mirror_overwrite_diverged)}`);
            pieces.push(`<span class="text-muted">only_protected:</span> ${fmtBool(!!pair.only_mirror_protected_branches)}`);
            if (direction === 'pull') {
                pieces.push(`<span class="text-muted">trigger:</span> ${fmtBool(!!pair.mirror_trigger_builds)}`);
                pieces.push(`<span class="text-muted">regex:</span> ${fmtStr(pair.mirror_branch_regex)}`);
                pieces.push(`<span class="text-muted">user:</span> ${fmtUser()}`);
            } else {
                pieces.push(`<span class="text-muted">trigger:</span> <span class="text-muted">n/a</span>`);
                pieces.push(`<span class="text-muted">regex:</span> <span class="text-muted">n/a</span>`);
                pieces.push(`<span class="text-muted">user:</span> <span class="text-muted">n/a</span>`);
            }
            return `<div style="line-height:1.35">${pieces.join('<br>')}</div>`;
        })();

        return `
            <tr>
                <td><strong>${escapeHtml(pair.name)}</strong></td>
                <td>${escapeHtml(source?.name || 'Unknown')}</td>
                <td>${escapeHtml(target?.name || 'Unknown')}</td>
                <td>${badge(pair.mirror_direction)}</td>
                <td>${settingsCell}</td>
                <td>
                    <button class="btn btn-danger btn-small" onclick="deletePair(${pair.id})">Delete</button>
                </td>
            </tr>
        `;
    }).join('');
}

function applyPairTokenUserDefaultIfEmpty() {
    const dirEl = document.getElementById('pair-direction');
    const srcEl = document.getElementById('pair-source-instance');
    const tgtEl = document.getElementById('pair-target-instance');
    const userIdEl = document.getElementById('pair-mirror-user-id');
    if (!dirEl || !srcEl || !tgtEl || !userIdEl) return;

    // Only apply if empty and field enabled
    if (userIdEl.disabled) return;
    if ((userIdEl.value || '').toString().trim()) return;

    const direction = (dirEl.value || '').toString().toLowerCase();
    const srcId = parseInt(srcEl.value || '0');
    const tgtId = parseInt(tgtEl.value || '0');
    const ownerInstanceId = direction === 'push' ? srcId : tgtId;
    const inst = state.instances.find(i => i.id === ownerInstanceId);
    if (inst && inst.token_user_id) {
        userIdEl.value = String(inst.token_user_id);
    }
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
        renderGroupSettings();
        updateTokenInstanceSelector();
        updateGroupDefaultsPairSelector();
        resetGroupDefaultsOverrides();
    } catch (error) {
        console.error('Failed to load tokens:', error);
    }
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
        renderGroupSettings();
        updateGroupDefaultsPairSelector();
        resetGroupDefaultsOverrides();
    } catch (error) {
        console.error('Failed to load group defaults:', error);
    }
}

function renderGroupSettings() {
    const tbody = document.getElementById('group-settings-list');
    if (!tbody) return;

    const pairs = (state.pairs || []).slice().sort((a, b) => (a.name || '').localeCompare(b.name || ''));
    const tokens = state.tokens || [];
    const groupDefaults = state.groupDefaults || [];

    const tokenByKey = new Map();
    tokens.forEach(t => {
        tokenByKey.set(`${t.gitlab_instance_id}:${t.group_path}`, t);
    });

    const defaultsByKey = new Map();
    groupDefaults.forEach(d => {
        defaultsByKey.set(`${d.instance_pair_id}:${d.group_path}`, d);
    });

    const rows = [];

    const fmtBool = (v) => (v === null || v === undefined) ? '<span class="text-muted">n/a</span>' : (v ? 'true' : 'false');
    const fmtStr = (v) => (v === null || v === undefined || String(v).trim() === '')
        ? '<span class="text-muted">n/a</span>'
        : `<code>${escapeHtml(String(v))}</code>`;

    pairs.forEach(pair => {
        const srcInst = state.instances.find(i => i.id === pair.source_instance_id);
        const tgtInst = state.instances.find(i => i.id === pair.target_instance_id);
        const srcName = srcInst?.name || 'Source';
        const tgtName = tgtInst?.name || 'Target';

        const paths = new Set();
        groupDefaults.forEach(d => {
            if (d.instance_pair_id === pair.id) paths.add(d.group_path);
        });
        tokens.forEach(t => {
            if (t.gitlab_instance_id === pair.source_instance_id || t.gitlab_instance_id === pair.target_instance_id) {
                paths.add(t.group_path);
            }
        });

        Array.from(paths).sort().forEach(groupPath => {
            const gd = defaultsByKey.get(`${pair.id}:${groupPath}`);

            const direction = ((gd?.mirror_direction || pair.mirror_direction || '') + '').toLowerCase();
            const overwrite = (gd && gd.mirror_overwrite_diverged !== null && gd.mirror_overwrite_diverged !== undefined)
                ? gd.mirror_overwrite_diverged
                : pair.mirror_overwrite_diverged;
            const onlyProtected = (gd && gd.only_mirror_protected_branches !== null && gd.only_mirror_protected_branches !== undefined)
                ? gd.only_mirror_protected_branches
                : pair.only_mirror_protected_branches;

            let trigger = (gd && gd.mirror_trigger_builds !== null && gd.mirror_trigger_builds !== undefined)
                ? gd.mirror_trigger_builds
                : pair.mirror_trigger_builds;
            let regex = (gd && gd.mirror_branch_regex !== null && gd.mirror_branch_regex !== undefined)
                ? gd.mirror_branch_regex
                : pair.mirror_branch_regex;
            let userId = (gd && gd.mirror_user_id !== null && gd.mirror_user_id !== undefined)
                ? gd.mirror_user_id
                : pair.mirror_user_id;

            if (direction === 'push') {
                trigger = null;
                regex = null;
                userId = null;
            }

            const settingsCell = (() => {
                const pieces = [];
                if (direction) pieces.push(`<span class="badge badge-info">${escapeHtml(direction)}</span>`);
                pieces.push(`<span class="text-muted">overwrite:</span> ${fmtBool(overwrite)}`);
                pieces.push(`<span class="text-muted">only_protected:</span> ${fmtBool(onlyProtected)}`);
                if (direction === 'pull') {
                    pieces.push(`<span class="text-muted">trigger:</span> ${fmtBool(trigger)}`);
                    pieces.push(`<span class="text-muted">regex:</span> ${fmtStr(regex)}`);
                    pieces.push(`<span class="text-muted">user:</span> ${userId === null || userId === undefined ? '<span class="text-muted">auto</span>' : escapeHtml(String(userId))}`);
                } else if (direction === 'push') {
                    pieces.push(`<span class="text-muted">trigger:</span> <span class="text-muted">n/a</span>`);
                    pieces.push(`<span class="text-muted">regex:</span> <span class="text-muted">n/a</span>`);
                    pieces.push(`<span class="text-muted">user:</span> <span class="text-muted">n/a</span>`);
                } else {
                    pieces.push(`<span class="text-muted">trigger:</span> <span class="text-muted">n/a</span>`);
                    pieces.push(`<span class="text-muted">regex:</span> <span class="text-muted">n/a</span>`);
                    pieces.push(`<span class="text-muted">user:</span> <span class="text-muted">n/a</span>`);
                }
                return `<div style="line-height:1.35">${pieces.join('<br>')}</div>`;
            })();

            const srcTok = tokenByKey.get(`${pair.source_instance_id}:${groupPath}`);
            const tgtTok = tokenByKey.get(`${pair.target_instance_id}:${groupPath}`);
            const tokenCell = `
                <div style="line-height:1.35">
                    <span class="text-muted">${escapeHtml(srcName)}:</span> ${srcTok ? escapeHtml(srcTok.token_name) : '<span class="text-muted">missing</span>'}<br>
                    <span class="text-muted">${escapeHtml(tgtName)}:</span> ${tgtTok ? escapeHtml(tgtTok.token_name) : '<span class="text-muted">missing</span>'}
                </div>
            `;

            const actions = [];
            if (gd) actions.push(`<button class="btn btn-danger btn-small" onclick="deleteGroupDefaults(${gd.id})">Delete defaults</button>`);
            if (srcTok) actions.push(`<button class="btn btn-danger btn-small" onclick="deleteToken(${srcTok.id})" title="Delete token on ${escapeHtml(srcName)}">Delete token</button>`);
            if (tgtTok) actions.push(`<button class="btn btn-danger btn-small" onclick="deleteToken(${tgtTok.id})" title="Delete token on ${escapeHtml(tgtName)}">Delete token</button>`);
            const actionsCell = actions.length
                ? `<div class="flex" style="gap:6px; flex-wrap:wrap">${actions.join('')}</div>`
                : '<span class="text-muted">N/A</span>';

            rows.push(`
                <tr>
                    <td><strong>${escapeHtml(pair.name)}</strong></td>
                    <td>${escapeHtml(groupPath)}</td>
                    <td>${settingsCell}</td>
                    <td>${tokenCell}</td>
                    <td>${actionsCell}</td>
                </tr>
            `);
        });
    });

    if (rows.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted">No group settings configured</td></tr>';
        return;
    }

    tbody.innerHTML = rows.join('');
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
    let mirrorUserIdRaw = (formData.get('mirror_user_id') || '').toString().trim();
    const dirRaw = (formData.get('mirror_direction') || '').toString().trim();

    // Auto-fill mirror_user_id from the owning instance token user if empty
    const pair = state.pairs.find(p => p.id === parseInt(formData.get('instance_pair_id')));
    const effDir = (dirRaw || pair?.mirror_direction || '').toString().toLowerCase();
    if (!mirrorUserIdRaw && pair && effDir !== 'push') {
        const ownerInstanceId = pair.target_instance_id;
        const inst = state.instances.find(i => i.id === ownerInstanceId);
        if (inst && inst.token_user_id) {
            mirrorUserIdRaw = String(inst.token_user_id);
        }
    }

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

function applyGroupDefaultsTokenUserDefaultIfEmpty() {
    const pairSel = document.getElementById('group-defaults-pair');
    const dirSel = document.getElementById('group-defaults-direction');
    const userEl = document.getElementById('group-defaults-mirror-user-id');
    if (!pairSel || !dirSel || !userEl) return;
    if ((userEl.value || '').toString().trim()) return;

    const pair = state.pairs.find(p => p.id === parseInt(pairSel.value || '0'));
    if (!pair) return;
    const effDir = ((dirSel.value || '') || pair.mirror_direction || '').toString().toLowerCase();
    if (effDir === 'push') return;
    const inst = state.instances.find(i => i.id === pair.target_instance_id);
    if (inst && inst.token_user_id) {
        userEl.value = String(inst.token_user_id);
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
            '<tr><td colspan="7" class="text-center text-muted">Select an instance pair to view mirrors</td></tr>';
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
        tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted">No mirrors configured for this pair</td></tr>';
        return;
    }

    const fmtBool = (v) => (v === null || v === undefined) ? '<span class="text-muted">n/a</span>' : (v ? 'true' : 'false');
    const fmtStr = (v) => (v === null || v === undefined || String(v).trim() === '')
        ? '<span class="text-muted">n/a</span>'
        : `<code>${escapeHtml(String(v))}</code>`;
    const fmtUser = (v) => (v === null || v === undefined) ? '<span class="text-muted">n/a</span>' : escapeHtml(String(v));

    tbody.innerHTML = mirrors.map(mirror => {
        const statusBadge = mirror.enabled ?
            `<span class="badge badge-success">Enabled</span>` :
            `<span class="badge badge-warning">Disabled</span>`;

        const updateStatus = mirror.last_update_status ?
            `<span class="badge badge-info">${mirror.last_update_status}</span>` :
            '<span class="text-muted">N/A</span>';

        const dir = (mirror.effective_mirror_direction || mirror.mirror_direction || '').toString().toLowerCase();
        const settingsCell = (() => {
            const pieces = [];
            if (dir) pieces.push(`<span class="badge badge-info">${escapeHtml(dir)}</span>`);
            pieces.push(`<span class="text-muted">overwrite:</span> ${fmtBool(mirror.effective_mirror_overwrite_diverged)}`);
            pieces.push(`<span class="text-muted">only_protected:</span> ${fmtBool(mirror.effective_only_mirror_protected_branches)}`);
            if (dir === 'pull') {
                pieces.push(`<span class="text-muted">trigger:</span> ${fmtBool(mirror.effective_mirror_trigger_builds)}`);
                pieces.push(`<span class="text-muted">regex:</span> ${fmtStr(mirror.effective_mirror_branch_regex)}`);
                pieces.push(`<span class="text-muted">user:</span> ${fmtUser(mirror.effective_mirror_user_id)}`);
            } else if (dir === 'push') {
                pieces.push(`<span class="text-muted">trigger:</span> <span class="text-muted">n/a</span>`);
                pieces.push(`<span class="text-muted">regex:</span> <span class="text-muted">n/a</span>`);
                pieces.push(`<span class="text-muted">user:</span> <span class="text-muted">n/a</span>`);
            }
            return `<div style="line-height:1.35">${pieces.join('<br>')}</div>`;
        })();

        return `
            <tr>
                <td>${escapeHtml(mirror.source_project_path)}</td>
                <td>${escapeHtml(mirror.target_project_path)}</td>
                <td>${settingsCell}</td>
                <td>${statusBadge}</td>
                <td>${updateStatus}</td>
                <td>${mirror.last_successful_update ? new Date(mirror.last_successful_update).toLocaleString() : 'Never'}</td>
                <td>
                    <button class="btn btn-success btn-small" onclick="triggerMirrorUpdate(${mirror.id})" title="Trigger an immediate mirror sync">Sync</button>
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

        // Avoid loading every project up-front (can be very large). Use
        // a server-backed typeahead instead.
        state.mirrorProjectInstances.source = pair.source_instance_id;
        state.mirrorProjectInstances.target = pair.target_instance_id;

        const sourceSelect = document.getElementById('mirror-source-project');
        const targetSelect = document.getElementById('mirror-target-project');
        if (sourceSelect) sourceSelect.innerHTML = '<option value="">Type to search for a source project...</option>';
        if (targetSelect) targetSelect.innerHTML = '<option value="">Type to search for a target project...</option>';

        const srcSearch = document.getElementById('mirror-source-project-search');
        const tgtSearch = document.getElementById('mirror-target-project-search');
        if (srcSearch) srcSearch.value = '';
        if (tgtSearch) tgtSearch.value = '';
    } catch (error) {
        console.error('Failed to load projects:', error);
    }
}

async function searchProjectsForMirror(side) {
    const instanceId = side === 'source' ? state.mirrorProjectInstances.source : state.mirrorProjectInstances.target;
    const inputId = side === 'source' ? 'mirror-source-project-search' : 'mirror-target-project-search';
    const selectId = side === 'source' ? 'mirror-source-project' : 'mirror-target-project';
    const placeholder = side === 'source' ? 'Select source project...' : 'Select target project...';

    const input = document.getElementById(inputId);
    const select = document.getElementById(selectId);
    if (!input || !select) return;

    if (!instanceId) {
        select.innerHTML = '<option value="">Select an instance pair first...</option>';
        return;
    }

    const q = (input.value || '').toString().trim();
    if (q.length < 2) {
        select.innerHTML = '<option value="">Type at least 2 characters to search...</option>';
        return;
    }

    const perPage = 50;
    try {
        const res = await apiRequest(
            `/api/instances/${instanceId}/projects?search=${encodeURIComponent(q)}&per_page=${perPage}&page=1&get_all=false`
        );
        const projects = res?.projects || [];

        const options = projects.map(p => {
            const fullPath = (p.path_with_namespace || p.name || '').toString();
            // Show full namespace path (supports multi-level groups).
            return `<option value="${p.id}" data-path="${escapeHtml(fullPath)}">${escapeHtml(fullPath)}</option>`;
        }).join('');

        const moreHint = projects.length >= perPage
            ? `<option value="" disabled>Showing first ${perPage} matches — refine search to narrow</option>`
            : '';

        select.innerHTML = `<option value="">${placeholder}</option>` + options + moreHint;
    } catch (error) {
        console.error('Failed to search projects:', error);
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

function debounce(fn, delayMs) {
    let t = null;
    return (...args) => {
        if (t) clearTimeout(t);
        t = setTimeout(() => fn(...args), delayMs);
    };
}
