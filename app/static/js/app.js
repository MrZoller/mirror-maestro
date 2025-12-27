// State management
const state = {
    instances: [],
    pairs: [],
    mirrors: [],
    tokens: [],
    groupDefaults: [],
    selectedPair: null,
    mirrorProjectInstances: { source: null, target: null },
    editing: {
        instanceId: null,
        pairId: null,
        mirrorId: null,
        tokenId: null,
    },
};

// Initialize the application
document.addEventListener('DOMContentLoaded', () => {
    initDarkMode();
    initTabs();
    setupEventListeners();
    initTableEnhancements();

    // Demo screenshots open static HTML files via file://; avoid API calls there.
    const isFileDemo = (window?.location?.protocol || '') === 'file:';
    if (!isFileDemo) {
        loadDashboard();
        loadInstances();
        loadPairs();
        startLivePolling();
    }
});

// ----------------------------
// Table sorting + filtering
// ----------------------------
const tableEnhancers = new Map();

function initTableEnhancements() {
    const configsByTbodyId = {
        'instances-list': {
            tableLabel: 'Instances',
            columns: [
                { key: 'name', label: 'Name', sortable: true, filter: 'text' },
                { key: 'url', label: 'URL', sortable: true, filter: 'text' },
                { key: 'description', label: 'Description', sortable: true, filter: 'text' },
                { key: 'token', label: 'Access Token', sortable: false, filter: 'none' },
                { key: 'actions', label: 'Actions', sortable: false, filter: 'none' },
            ],
        },
        'pairs-list': {
            tableLabel: 'Instance Pairs',
            columns: [
                { key: 'name', label: 'Name', sortable: true, filter: 'text' },
                { key: 'source', label: 'Source', sortable: true, filter: 'text' },
                { key: 'target', label: 'Target', sortable: true, filter: 'text' },
                { key: 'direction', label: 'Direction', sortable: true, filter: 'select' },
                { key: 'defaults', label: 'Default Settings', sortable: false, filter: 'text' },
                { key: 'actions', label: 'Actions', sortable: false, filter: 'none' },
            ],
        },
        'group-settings-list': {
            tableLabel: 'Group Settings',
            columns: [
                { key: 'pair', label: 'Instance Pair', sortable: true, filter: 'text' },
                { key: 'group', label: 'Group Path', sortable: true, filter: 'text' },
                { key: 'defaults', label: 'Default Settings', sortable: false, filter: 'text' },
                { key: 'tokens', label: 'Tokens', sortable: false, filter: 'text' },
                { key: 'actions', label: 'Actions', sortable: false, filter: 'none' },
            ],
        },
        'mirrors-list': {
            tableLabel: 'Mirrors',
            columns: [
                { key: 'source_project', label: 'Source Project', sortable: true, filter: 'text' },
                { key: 'target_project', label: 'Target Project', sortable: true, filter: 'text' },
                { key: 'settings', label: 'Effective Settings', sortable: false, filter: 'text' },
                { key: 'status', label: 'Status', sortable: true, filter: 'select' },
                { key: 'sync_status', label: 'Sync Status', sortable: true, filter: 'select' },
                { key: 'last_sync', label: 'Last Sync', sortable: true, filter: 'text' },
                { key: 'actions', label: 'Actions', sortable: false, filter: 'none' },
            ],
        },
    };

    Object.entries(configsByTbodyId).forEach(([tbodyId, config]) => {
        const tbody = document.getElementById(tbodyId);
        const table = tbody?.closest('table');
        if (!tbody || !table) return;

        const enhancer = createTableEnhancer(table, tbody, config);
        tableEnhancers.set(table, enhancer);
        enhancer.ensureUI();
        enhancer.refresh();

        // Auto-refresh when rows are re-rendered.
        const debouncedRefresh = debounce(() => enhancer.refresh(), 50);
        const obs = new MutationObserver(() => debouncedRefresh());
        obs.observe(tbody, { childList: true, subtree: true });
    });
}

function createTableEnhancer(table, tbody, config) {
    const state = {
        sort: { colIndex: null, dir: 'asc' },
        filters: new Map(), // colIndex -> string
        globalQuery: '',
        controls: { filterRow: null, globalInput: null, clearBtn: null, selects: new Map() },
    };

    const normalize = (s) => (s || '').toString().trim().toLowerCase();
    const getCellSortText = (cell) => {
        if (!cell) return '';
        const ds = (cell.dataset?.sort || '').toString();
        if (ds) return ds;
        // Prefer visible text; handles badges, nested spans, etc.
        return (cell.innerText || cell.textContent || '').toString().trim();
    };

    const parseComparable = (raw) => {
        const s = (raw || '').toString().trim();
        if (!s) return { kind: 'empty', value: '' };

        // ISO-ish timestamps sort well lexicographically, but we can parse too.
        const ms = Date.parse(s);
        if (!Number.isNaN(ms) && /[0-9]{4}-[0-9]{2}-[0-9]{2}/.test(s)) {
            return { kind: 'number', value: ms };
        }

        // Numeric
        const num = Number(s.replace(/,/g, ''));
        if (!Number.isNaN(num) && /^[+-]?\d+(\.\d+)?$/.test(s.replace(/,/g, ''))) {
            return { kind: 'number', value: num };
        }

        return { kind: 'string', value: s.toLowerCase() };
    };

    const getDataRows = () => {
        const rows = Array.from(tbody.querySelectorAll('tr'));
        const meta = [];
        const data = [];

        rows.forEach((tr) => {
            // Placeholder / info rows typically use colspan and should not be sorted/filtered.
            const hasColspan = !!tr.querySelector('td[colspan]');
            const isEditing = tr.dataset?.editing === 'true';
            if (hasColspan) meta.push(tr);
            else if (isEditing) data.unshift(tr); // pin editing row(s) to top
            else data.push(tr);
        });

        return { meta, data };
    };

    const ensureToolsRow = () => {
        if (table.dataset.enhancedTools === 'true') return;
        table.dataset.enhancedTools = 'true';

        const wrapper = document.createElement('div');
        wrapper.className = 'table-tools';

        const left = document.createElement('div');
        left.className = 'table-tools-left';

        const label = document.createElement('div');
        label.className = 'table-tools-label';
        label.textContent = `${config.tableLabel} — filter & sort`;

        const globalInput = document.createElement('input');
        globalInput.type = 'search';
        globalInput.className = 'table-tools-search';
        globalInput.placeholder = 'Search table…';
        globalInput.value = state.globalQuery;
        globalInput.addEventListener('input', () => {
            state.globalQuery = (globalInput.value || '').toString();
            refresh();
        });

        const clearBtn = document.createElement('button');
        clearBtn.type = 'button';
        clearBtn.className = 'btn btn-secondary btn-small';
        clearBtn.textContent = 'Clear filters';
        clearBtn.addEventListener('click', () => {
            state.globalQuery = '';
            state.filters.clear();
            if (state.controls.globalInput) state.controls.globalInput.value = '';
            if (state.controls.filterRow) {
                state.controls.filterRow.querySelectorAll('input, select').forEach((el) => {
                    if (el.tagName.toLowerCase() === 'select') el.value = '';
                    else el.value = '';
                });
            }
            refresh();
        });

        left.appendChild(label);
        left.appendChild(globalInput);

        const right = document.createElement('div');
        right.className = 'table-tools-right';
        right.appendChild(clearBtn);

        wrapper.appendChild(left);
        wrapper.appendChild(right);

        table.parentNode.insertBefore(wrapper, table);

        state.controls.globalInput = globalInput;
        state.controls.clearBtn = clearBtn;
    };

    const ensureFilterRow = () => {
        const thead = table.querySelector('thead');
        if (!thead) return;

        // Ensure header cells are marked sortable as configured.
        const headerRow = thead.querySelector('tr');
        const ths = headerRow ? Array.from(headerRow.querySelectorAll('th')) : [];
        ths.forEach((th, idx) => {
            const colCfg = config.columns[idx];
            if (!colCfg) return;
            th.classList.toggle('sortable', !!colCfg.sortable);
            th.dataset.colIndex = String(idx);
            th.title = colCfg.sortable ? 'Click to sort' : '';
            if (!th.dataset.sortBound) {
                th.dataset.sortBound = 'true';
                th.addEventListener('click', () => {
                    if (!colCfg.sortable) return;
                    if (state.sort.colIndex === idx) {
                        state.sort.dir = state.sort.dir === 'asc' ? 'desc' : 'asc';
                    } else {
                        state.sort.colIndex = idx;
                        state.sort.dir = 'asc';
                    }
                    refresh();
                });
            }
        });

        if (thead.querySelector('tr.table-filter-row')) {
            state.controls.filterRow = thead.querySelector('tr.table-filter-row');
            return;
        }

        const filterRow = document.createElement('tr');
        filterRow.className = 'table-filter-row';

        config.columns.forEach((colCfg, idx) => {
            const th = document.createElement('th');
            th.className = 'table-filter-cell';

            if (colCfg.filter === 'text') {
                const input = document.createElement('input');
                input.type = 'search';
                input.className = 'table-filter-input';
                input.placeholder = 'Filter…';
                input.value = state.filters.get(idx) || '';
                input.addEventListener('input', () => {
                    state.filters.set(idx, (input.value || '').toString());
                    refresh();
                });
                th.appendChild(input);
            } else if (colCfg.filter === 'select') {
                const sel = document.createElement('select');
                sel.className = 'table-filter-select';
                sel.addEventListener('change', () => {
                    state.filters.set(idx, (sel.value || '').toString());
                    refresh();
                });
                th.appendChild(sel);
                state.controls.selects.set(idx, sel);
            } else {
                // no filter control
                const spacer = document.createElement('div');
                spacer.className = 'table-filter-none';
                spacer.textContent = '';
                th.appendChild(spacer);
            }

            filterRow.appendChild(th);
        });

        thead.appendChild(filterRow);
        state.controls.filterRow = filterRow;
    };

    const updateSelectOptions = () => {
        const selects = state.controls.selects;
        if (!selects || selects.size === 0) return;

        const { data } = getDataRows();

        selects.forEach((sel, idx) => {
            const current = (sel.value || '').toString();
            const values = new Set();
            data.forEach((tr) => {
                const td = tr.children?.[idx];
                const v = normalize(getCellSortText(td));
                if (v) values.add(v);
            });
            const sorted = Array.from(values).sort((a, b) => a.localeCompare(b));

            sel.innerHTML = '';
            const optAll = document.createElement('option');
            optAll.value = '';
            optAll.textContent = 'All';
            sel.appendChild(optAll);
            sorted.forEach((v) => {
                const opt = document.createElement('option');
                opt.value = v;
                opt.textContent = v;
                sel.appendChild(opt);
            });
            sel.value = sorted.includes(current) ? current : '';
            state.filters.set(idx, sel.value);
        });
    };

    const rowMatches = (tr) => {
        const global = normalize(state.globalQuery);
        if (global) {
            const rowText = normalize(tr.innerText || tr.textContent || '');
            if (!rowText.includes(global)) return false;
        }

        for (const [idx, qRaw] of state.filters.entries()) {
            const colCfg = config.columns[idx];
            if (!colCfg || colCfg.filter === 'none') continue;
            const q = normalize(qRaw);
            if (!q) continue;
            const td = tr.children?.[idx];
            const cellText = normalize(getCellSortText(td));

            if (colCfg.filter === 'select') {
                if (cellText !== q) return false;
            } else {
                if (!cellText.includes(q)) return false;
            }
        }
        return true;
    };

    const applyFiltering = () => {
        const { meta, data } = getDataRows();

        // Meta rows (spinner / empty-state) remain visible if there are no data rows.
        if (data.length === 0) {
            meta.forEach((tr) => (tr.style.display = ''));
            return;
        }

        meta.forEach((tr) => (tr.style.display = 'none'));
        data.forEach((tr) => {
            if (tr.dataset?.editing === 'true') {
                tr.style.display = '';
                return;
            }
            tr.style.display = rowMatches(tr) ? '' : 'none';
        });
    };

    const applySorting = () => {
        const colIndex = state.sort.colIndex;
        if (colIndex === null || colIndex === undefined) return;
        const colCfg = config.columns[colIndex];
        if (!colCfg?.sortable) return;

        const { meta, data } = getDataRows();

        // Only sort non-editing rows that are not hidden by filtering.
        const visible = [];
        const hidden = [];
        const pinned = [];

        data.forEach((tr, i) => {
            if (tr.dataset?.editing === 'true') {
                pinned.push({ tr, i });
                return;
            }
            const isHidden = tr.style.display === 'none';
            const td = tr.children?.[colIndex];
            const raw = getCellSortText(td);
            const cmp = parseComparable(raw);
            const rec = { tr, i, cmp };
            if (isHidden) hidden.push(rec);
            else visible.push(rec);
        });

        const dirMul = state.sort.dir === 'desc' ? -1 : 1;
        const compare = (a, b) => {
            if (a.cmp.kind === 'empty' && b.cmp.kind !== 'empty') return 1;
            if (b.cmp.kind === 'empty' && a.cmp.kind !== 'empty') return -1;
            if (a.cmp.kind === 'number' && b.cmp.kind === 'number') {
                if (a.cmp.value < b.cmp.value) return -1 * dirMul;
                if (a.cmp.value > b.cmp.value) return 1 * dirMul;
                return a.i - b.i;
            }
            const av = a.cmp.value.toString();
            const bv = b.cmp.value.toString();
            const c = av.localeCompare(bv);
            if (c !== 0) return c * dirMul;
            return a.i - b.i;
        };

        visible.sort(compare);
        hidden.sort(compare);

        // Rebuild tbody order:
        // - pinned editing row(s) first (original relative order)
        // - visible (sorted)
        // - hidden (sorted, but hidden via display)
        // - meta rows last
        const frag = document.createDocumentFragment();
        pinned.sort((a, b) => a.i - b.i).forEach((r) => frag.appendChild(r.tr));
        visible.forEach((r) => frag.appendChild(r.tr));
        hidden.forEach((r) => frag.appendChild(r.tr));
        meta.forEach((tr) => frag.appendChild(tr));
        tbody.appendChild(frag);
    };

    const updateSortIndicators = () => {
        const thead = table.querySelector('thead');
        const headerRow = thead?.querySelector('tr');
        if (!headerRow) return;
        const ths = Array.from(headerRow.querySelectorAll('th'));
        ths.forEach((th, idx) => {
            th.classList.remove('sort-asc', 'sort-desc');
            if (state.sort.colIndex === idx) {
                th.classList.add(state.sort.dir === 'asc' ? 'sort-asc' : 'sort-desc');
            }
        });
    };

    function ensureUI() {
        ensureToolsRow();
        ensureFilterRow();
        updateSelectOptions();
        updateSortIndicators();
    }

    function refresh() {
        ensureUI();
        updateSelectOptions();
        applyFiltering();
        applySorting();
        updateSortIndicators();
    }

    return { ensureUI, refresh };
}

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
            } else if (targetId === 'topology-tab') {
                if (typeof window.initTopologyTab === 'function') {
                    window.initTopologyTab();
                }
            }
        });
    });
}

// Switch to a tab programmatically (used by Help page links)
function switchTab(tabId) {
    const tab = document.querySelector(`[data-tab="${tabId}"]`);
    if (tab) tab.click();
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
        if (state.editing?.tokenId) {
            await updateToken();
        } else {
            await createToken();
        }
    });
    document.getElementById('token-cancel-edit')?.addEventListener('click', () => {
        cancelTokenEdit();
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
        applyGroupDefaultsDirectionUI();
        clearDatalist('group-defaults-group-path-options');
    });
    document.getElementById('group-defaults-direction')?.addEventListener('change', () => {
        applyGroupDefaultsTokenUserDefaultIfEmpty();
        applyGroupDefaultsDirectionUI();
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

// API Helper with enhanced error handling
class APIError extends Error {
    constructor(message, type = 'unknown', statusCode = 0, details = null) {
        super(message);
        this.name = 'APIError';
        this.type = type; // 'network', 'validation', 'server', 'auth', 'unknown'
        this.statusCode = statusCode;
        this.details = details;
    }
}

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
            let errorData;
            let errorMessage = 'Request failed';
            let errorType = 'server';

            try {
                errorData = await response.json();
                errorMessage = errorData.detail || errorMessage;

                // Handle complex error details (like conflict with existing_mirror_id)
                if (typeof errorData.detail === 'object' && errorData.detail.message) {
                    errorMessage = errorData.detail.message;
                }
            } catch (e) {
                errorMessage = `HTTP ${response.status}: ${response.statusText}`;
            }

            // Classify error type based on status code
            if (response.status === 401 || response.status === 403) {
                errorType = 'auth';
            } else if (response.status === 400 || response.status === 409 || response.status === 422) {
                errorType = 'validation';
            } else if (response.status >= 500) {
                errorType = 'server';
            }

            throw new APIError(errorMessage, errorType, response.status, errorData);
        }

        return await response.json();
    } catch (error) {
        if (error instanceof APIError) {
            throw error;
        }

        // Network error (fetch failed completely)
        if (error instanceof TypeError && error.message.includes('fetch')) {
            throw new APIError(
                'Network error. Please check your connection.',
                'network',
                0,
                error
            );
        }

        throw new APIError(
            error.message || 'An unexpected error occurred',
            'unknown',
            0,
            error
        );
    }
}

// Loading state management
function showLoadingState(container, message = 'Loading...') {
    if (typeof container === 'string') {
        container = document.getElementById(container);
    }
    if (!container) return;

    container.classList.add('loading-overlay');
    container.setAttribute('aria-busy', 'true');
}

function hideLoadingState(container) {
    if (typeof container === 'string') {
        container = document.getElementById(container);
    }
    if (!container) return;

    container.classList.remove('loading-overlay');
    container.removeAttribute('aria-busy');
}

function showSkeletonLoader(container, rowCount = 3) {
    if (typeof container === 'string') {
        container = document.getElementById(container);
    }
    if (!container) return;

    const skeletonHTML = Array(rowCount).fill(0).map(() => `
        <div class="skeleton-row">
            <div class="skeleton skeleton-cell"></div>
            <div class="skeleton skeleton-cell"></div>
            <div class="skeleton skeleton-cell"></div>
        </div>
    `).join('');

    container.innerHTML = `<div class="skeleton-table">${skeletonHTML}</div>`;
}

function showEmptyState(container, title, message, actionButton = null) {
    if (typeof container === 'string') {
        container = document.getElementById(container);
    }
    if (!container) return;

    let actionHTML = '';
    if (actionButton) {
        actionHTML = `
            <div class="empty-state-action">
                <button class="btn btn-primary" onclick="${actionButton.onclick}">
                    ${escapeHtml(actionButton.label)}
                </button>
            </div>
        `;
    }

    container.innerHTML = `
        <div class="empty-state">
            <div class="empty-state-icon">📦</div>
            <div class="empty-state-title">${escapeHtml(title)}</div>
            <div class="empty-state-message">${escapeHtml(message)}</div>
            ${actionHTML}
        </div>
    `;
}

function showErrorState(container, error, retryCallback = null) {
    if (typeof container === 'string') {
        container = document.getElementById(container);
    }
    if (!container) return;

    let errorTitle = 'Something went wrong';
    let errorMessage = error.message || 'An unexpected error occurred';
    let errorIcon = '⚠️';

    // Customize based on error type
    if (error instanceof APIError) {
        switch (error.type) {
            case 'network':
                errorTitle = 'Connection Error';
                errorIcon = '🔌';
                break;
            case 'auth':
                errorTitle = 'Authentication Error';
                errorMessage = 'Your session may have expired. Please refresh the page.';
                errorIcon = '🔒';
                break;
            case 'validation':
                errorTitle = 'Validation Error';
                errorIcon = '✋';
                break;
            case 'server':
                errorTitle = 'Server Error';
                errorMessage += ' Please try again later.';
                errorIcon = '❌';
                break;
        }
    }

    let retryHTML = '';
    if (retryCallback) {
        const retryId = `retry-${Math.random().toString(36).substr(2, 9)}`;
        retryHTML = `
            <button class="btn btn-retry" id="${retryId}">
                Retry
            </button>
        `;

        // Set up retry button after rendering
        setTimeout(() => {
            const btn = document.getElementById(retryId);
            if (btn) {
                btn.addEventListener('click', async (e) => {
                    e.preventDefault();
                    btn.classList.add('loading');
                    btn.disabled = true;
                    try {
                        await retryCallback();
                    } finally {
                        btn.classList.remove('loading');
                        btn.disabled = false;
                    }
                });
            }
        }, 0);
    }

    container.innerHTML = `
        <div class="error-state">
            <div class="error-state-icon">${errorIcon}</div>
            <div class="error-state-title">${escapeHtml(errorTitle)}</div>
            <div class="error-state-message">${escapeHtml(errorMessage)}</div>
            <div class="error-state-actions">
                ${retryHTML}
            </div>
        </div>
    `;
}

function showButtonLoading(button, isLoading = true) {
    if (typeof button === 'string') {
        button = document.getElementById(button);
    }
    if (!button) return;

    if (isLoading) {
        button.classList.add('loading');
        button.disabled = true;
        button.setAttribute('data-original-text', button.textContent);
    } else {
        button.classList.remove('loading');
        button.disabled = false;
        const originalText = button.getAttribute('data-original-text');
        if (originalText) {
            button.textContent = originalText;
            button.removeAttribute('data-original-text');
        }
    }
}

// ----------------------------
// Dark Mode
// ----------------------------

function initDarkMode() {
    // Check localStorage for saved theme, default to light
    const savedTheme = localStorage.getItem('theme') || 'light';
    document.documentElement.setAttribute('data-theme', savedTheme);

    // Set up toggle button
    const toggle = document.getElementById('dark-mode-toggle');
    if (toggle) {
        toggle.addEventListener('click', toggleDarkMode);
    }
}

function toggleDarkMode() {
    const currentTheme = document.documentElement.getAttribute('data-theme');
    const newTheme = currentTheme === 'light' ? 'dark' : 'light';

    document.documentElement.setAttribute('data-theme', newTheme);
    localStorage.setItem('theme', newTheme);
}

// ----------------------------
// Dashboard
// ----------------------------

let healthChart = null;
let livePollingInterval = null;

async function loadDashboard() {
    try {
        const metrics = await apiRequest('/api/dashboard/metrics');
        renderDashboard(metrics);
    } catch (error) {
        console.error('Failed to load dashboard:', error);
    }
}

function renderDashboard(metrics) {
    // Update summary stats
    updateStatCard('stat-total-mirrors', metrics.summary.total_mirrors);
    updateStatCard('stat-health-percentage', `${metrics.summary.health_percentage}%`);
    updateStatCard('stat-failed-mirrors', metrics.health.failed);

    // Update syncing count from quick stats (will be updated by polling)
    updateStatCard('stat-syncing-mirrors', '0');

    // Render health chart
    renderHealthChart(metrics.health);

    // Render recent activity
    renderRecentActivity(metrics.recent_activity);

    // Render pairs distribution
    renderPairsDistribution(metrics.mirrors_by_pair);
}

function updateStatCard(elementId, value) {
    const el = document.getElementById(elementId);
    if (el) {
        // Add count-up animation
        el.classList.add('animating');
        el.textContent = value;
        setTimeout(() => el.classList.remove('animating'), 400);
    }
}

function renderHealthChart(healthData) {
    const canvas = document.getElementById('health-chart');
    if (!canvas) return;

    const ctx = canvas.getContext('2d');

    // Destroy existing chart
    if (healthChart) {
        healthChart.destroy();
    }

    const data = {
        labels: ['Success', 'Failed', 'Pending', 'Unknown'],
        datasets: [{
            data: [
                healthData.success,
                healthData.failed,
                healthData.pending,
                healthData.unknown
            ],
            backgroundColor: [
                getComputedStyle(document.documentElement).getPropertyValue('--success-color').trim(),
                getComputedStyle(document.documentElement).getPropertyValue('--danger-color').trim(),
                getComputedStyle(document.documentElement).getPropertyValue('--warning-color').trim(),
                getComputedStyle(document.documentElement).getPropertyValue('--text-secondary').trim(),
            ],
            borderWidth: 0
        }]
    };

    healthChart = new Chart(ctx, {
        type: 'doughnut',
        data: data,
        options: {
            responsive: true,
            maintainAspectRatio: true,
            plugins: {
                legend: {
                    display: false
                },
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            const label = context.label || '';
                            const value = context.parsed || 0;
                            const total = context.dataset.data.reduce((a, b) => a + b, 0);
                            const percentage = total > 0 ? Math.round((value / total) * 100) : 0;
                            return `${label}: ${value} (${percentage}%)`;
                        }
                    }
                }
            }
        }
    });

    // Update legend counts
    document.getElementById('health-success-count').textContent = healthData.success;
    document.getElementById('health-failed-count').textContent = healthData.failed;
    document.getElementById('health-pending-count').textContent = healthData.pending;
    document.getElementById('health-unknown-count').textContent = healthData.unknown;
}

function renderRecentActivity(activities) {
    const container = document.getElementById('recent-activity-list');
    if (!container) return;

    if (activities.length === 0) {
        container.innerHTML = '<div class="text-center text-muted">No recent activity</div>';
        return;
    }

    const iconClasses = {
        'created': 'activity-icon-created',
        'synced': 'activity-icon-success',
        'failed': 'activity-icon-failed',
        'updated': 'activity-icon-updated'
    };

    container.innerHTML = activities.map(activity => `
        <div class="activity-item">
            <div class="activity-icon ${iconClasses[activity.activity_type] || ''}">
                ${activity.icon}
            </div>
            <div class="activity-content">
                <div class="activity-project">${escapeHtml(activity.project)}</div>
                <div class="activity-description">${activity.activity_type}</div>
            </div>
            <div class="activity-time">${activity.time_ago}</div>
        </div>
    `).join('');

    // Update timestamp
    const timestampEl = document.getElementById('activity-timestamp');
    if (timestampEl) {
        timestampEl.textContent = 'Just now';
    }
}

function renderPairsDistribution(pairs) {
    const container = document.getElementById('pairs-distribution');
    if (!container) return;

    if (pairs.length === 0) {
        container.innerHTML = '<div class="text-center text-muted">No instance pairs configured</div>';
        return;
    }

    container.innerHTML = pairs.map(pair => `
        <div class="pair-item">
            <span class="pair-name">${escapeHtml(pair.pair_name)}</span>
            <span class="pair-count">${pair.count}</span>
        </div>
    `).join('');
}

// ----------------------------
// Live Status Polling
// ----------------------------

function startLivePolling() {
    // Poll every 30 seconds
    livePollingInterval = setInterval(updateLiveStats, 30000);

    // Initial update
    updateLiveStats();
}

async function updateLiveStats() {
    try {
        const quickStats = await apiRequest('/api/dashboard/quick-stats');

        // Update syncing count
        updateStatCard('stat-syncing-mirrors', quickStats.syncing_count);

        // Update status indicators for currently syncing mirrors
        updateMirrorStatusIndicators(quickStats.syncing_mirror_ids);

    } catch (error) {
        console.error('Failed to update live stats:', error);
    }
}

function updateMirrorStatusIndicators(syncingIds) {
    // Add pulsing dots to mirrors that are currently syncing
    const mirrorRows = document.querySelectorAll('#mirrors-list tr[data-mirror-id]');
    mirrorRows.forEach(row => {
        const mirrorId = parseInt(row.dataset.mirrorId);
        const statusCell = row.querySelector('.mirror-status');

        if (statusCell && syncingIds.includes(mirrorId)) {
            // Add syncing indicator
            if (!statusCell.querySelector('.status-dot-syncing')) {
                const indicator = document.createElement('span');
                indicator.className = 'status-indicator';
                indicator.innerHTML = '<span class="status-dot status-dot-syncing"></span> Syncing...';
                statusCell.innerHTML = '';
                statusCell.appendChild(indicator);
            }
        }
    });
}

// GitLab Instances
async function loadInstances() {
    const container = document.getElementById('instances-list');
    if (!container) return;

    try {
        showSkeletonLoader(container, 3);

        const instances = await apiRequest('/api/instances');
        state.instances = instances;
        renderInstances(instances);
        updateInstanceSelectors();
    } catch (error) {
        console.error('Failed to load instances:', error);
        showErrorState(container, error, loadInstances);
        showMessage(error.message, 'error');
    }
}

function renderInstances(instances) {
    const tbody = document.getElementById('instances-list');
    if (!tbody) return;

    if (instances.length === 0) {
        showEmptyState(
            tbody,
            'No GitLab Instances',
            'Get started by adding your first GitLab instance. You\'ll need the instance URL and an access token.',
            null
        );
        return;
    }

    const editingId = state.editing?.instanceId;
    tbody.innerHTML = instances.map(instance => {
        const isEditing = editingId === instance.id;
        if (isEditing) {
            return `
                <tr data-editing="true">
                    <td>
                        <input class="table-input" id="edit-instance-name-${instance.id}" value="${escapeHtml(instance.name)}">
                    </td>
                    <td class="cell-locked" title="Instance URL is locked once it is used by a pair">
                        ${escapeHtml(instance.url)}
                    </td>
                    <td>
                        <input class="table-input" id="edit-instance-description-${instance.id}" value="${escapeHtml(instance.description || '')}" placeholder="Description (optional)">
                    </td>
                    <td>
                        <input class="table-input" type="password" id="edit-instance-token-${instance.id}" value="" placeholder="New access token (leave blank to keep)">
                    </td>
                    <td>
                        <div class="table-actions">
                            <button class="btn btn-primary btn-small" onclick="saveInstanceEdit(${instance.id})">Save</button>
                            <button class="btn btn-secondary btn-small" onclick="cancelInstanceEdit()">Cancel</button>
                        </div>
                    </td>
                </tr>
            `;
        }

        return `
            <tr>
                <td><strong>${escapeHtml(instance.name)}</strong></td>
                <td>${escapeHtml(instance.url)}</td>
                <td><span class="text-muted">${escapeHtml(instance.description || 'N/A')}</span></td>
                <td>
                    <span class="text-muted" title="Token value is never displayed">••••••••</span>
                </td>
                <td>
                    <div class="table-actions">
                        <button class="btn btn-secondary btn-small" onclick="beginInstanceEdit(${instance.id})">Edit</button>
                        <button class="btn btn-danger btn-small" onclick="deleteInstance(${instance.id})">Delete</button>
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}

function beginInstanceEdit(id) {
    state.editing.instanceId = id;
    state.editing.pairId = null;
    state.editing.mirrorId = null;
    state.editing.tokenId = null;
    renderInstances(state.instances);
}

function cancelInstanceEdit() {
    state.editing.instanceId = null;
    renderInstances(state.instances);
}

async function saveInstanceEdit(id) {
    const nameEl = document.getElementById(`edit-instance-name-${id}`);
    const descEl = document.getElementById(`edit-instance-description-${id}`);
    const tokenEl = document.getElementById(`edit-instance-token-${id}`);
    if (!nameEl || !descEl) return;

    const payload = {
        name: (nameEl.value || '').toString().trim(),
        description: (descEl.value || '').toString().trim() || null,
    };
    const tokenRaw = (tokenEl?.value || '').toString().trim();
    if (tokenRaw) {
        payload.token = tokenRaw;
    }

    try {
        await apiRequest(`/api/instances/${id}`, {
            method: 'PUT',
            body: JSON.stringify(payload),
        });
        showMessage('Instance updated successfully', 'success');
        state.editing.instanceId = null;
        await loadInstances();
        await loadPairs(); // names are displayed on the pairs tab
    } catch (error) {
        console.error('Failed to update instance:', error);
    }
}

async function createInstance(event) {
    if (event) event.preventDefault();

    const form = document.getElementById('instance-form');
    const submitButton = form.querySelector('button[type="submit"]');
    const formData = new FormData(form);

    const data = {
        name: formData.get('name'),
        url: formData.get('url'),
        token: formData.get('token'),
        description: formData.get('description') || ''
    };

    try {
        showButtonLoading(submitButton, true);

        await apiRequest('/api/instances', {
            method: 'POST',
            body: JSON.stringify(data)
        });

        showMessage('Instance created successfully', 'success');
        form.reset();
        await loadInstances();
    } catch (error) {
        console.error('Failed to create instance:', error);
        // Error message is already shown by showErrorState/showMessage
        if (error instanceof APIError && error.type === 'validation') {
            // Validation errors should keep the form filled so user can correct
            return;
        }
    } finally {
        showButtonLoading(submitButton, false);
    }
}

async function deleteInstance(id) {
    // Warn about cascading deletes (pairs + mirrors) before taking action.
    let confirmMsg = 'Are you sure you want to delete this instance?';
    try {
        const affectedPairs = (state.pairs || []).filter(p => p.source_instance_id === id || p.target_instance_id === id);
        const pairIds = new Set(affectedPairs.map(p => p.id));

        // Mirrors aren't always loaded in the UI; fetch all to compute an accurate count.
        const allMirrors = await apiRequest('/api/mirrors').catch(() => []);
        const mirrorCount = (allMirrors || []).filter(m => pairIds.has(m.instance_pair_id)).length;

        if (affectedPairs.length || mirrorCount) {
            confirmMsg =
                `Deleting this instance will also delete:\n` +
                `- ${affectedPairs.length} instance pair(s)\n` +
                `- ${mirrorCount} mirror(s)\n\n` +
                `This cannot be undone.\n\n` +
                `Continue?`;
        }
    } catch (e) {
        // Fall back to generic confirm.
    }

    if (!confirm(confirmMsg)) return;

    try {
        await apiRequest(`/api/instances/${id}`, { method: 'DELETE' });
        showMessage('Instance deleted successfully', 'success');
        await loadInstances();
        await loadPairs();
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
    const container = document.getElementById('pairs-list');
    if (!container) return;

    try {
        showSkeletonLoader(container, 3);

        const pairs = await apiRequest('/api/pairs');
        state.pairs = pairs;
        renderPairs(pairs);
        updatePairSelector();
    } catch (error) {
        console.error('Failed to load pairs:', error);
        showErrorState(container, error, loadPairs);
        showMessage(error.message, 'error');
    }
}

function renderPairs(pairs) {
    const tbody = document.getElementById('pairs-list');
    if (!tbody) return;

    if (pairs.length === 0) {
        showEmptyState(
            tbody,
            'No Instance Pairs',
            'Create an instance pair to define the source and target for your mirrors.',
            null
        );
        return;
    }

    const fmtBool = (v) => v ? 'true' : 'false';
    const fmtStr = (v) => (v === null || v === undefined || String(v).trim() === '')
        ? '<span class="text-muted">n/a</span>'
        : `<code>${escapeHtml(String(v))}</code>`;
    const badge = (dir) => `<span class="badge badge-info">${escapeHtml((dir || '').toString().toLowerCase() || 'n/a')}</span>`;

    const editingId = state.editing?.pairId;
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

        const isEditing = editingId === pair.id;
        if (isEditing) {
            return `
                <tr data-editing="true">
                    <td>
                        <div style="display:grid; gap:8px">
                            <input class="table-input" id="edit-pair-name-${pair.id}" value="${escapeHtml(pair.name)}">
                            <input class="table-input" id="edit-pair-description-${pair.id}" value="${escapeHtml(pair.description || '')}" placeholder="Description (optional)">
                        </div>
                    </td>
                    <td class="cell-locked" title="Locked to avoid breaking existing mirrors">${escapeHtml(source?.name || 'Unknown')}</td>
                    <td class="cell-locked" title="Locked to avoid breaking existing mirrors">${escapeHtml(target?.name || 'Unknown')}</td>
                    <td>
                        <select class="table-select" id="edit-pair-direction-${pair.id}" onchange="applyPairEditDirection(${pair.id})">
                            <option value="pull"${direction === 'pull' ? ' selected' : ''}>pull</option>
                            <option value="push"${direction === 'push' ? ' selected' : ''}>push</option>
                        </select>
                    </td>
                    <td>
                        <div style="display:grid; gap:10px">
                            <div class="checkbox-group">
                                <input type="checkbox" id="edit-pair-protected-${pair.id}" ${pair.mirror_protected_branches ? 'checked' : ''}>
                                <label for="edit-pair-protected-${pair.id}">Mirror protected branches</label>
                            </div>
                            <div class="checkbox-group">
                                <input type="checkbox" id="edit-pair-overwrite-${pair.id}" ${pair.mirror_overwrite_diverged ? 'checked' : ''}>
                                <label for="edit-pair-overwrite-${pair.id}">Overwrite divergent branches</label>
                            </div>
                            <div class="checkbox-group">
                                <input type="checkbox" id="edit-pair-trigger-${pair.id}" ${pair.mirror_trigger_builds ? 'checked' : ''}>
                                <label for="edit-pair-trigger-${pair.id}">Trigger builds on mirror update (pull only)</label>
                            </div>
                            <div class="checkbox-group">
                                <input type="checkbox" id="edit-pair-only-protected-${pair.id}" ${pair.only_mirror_protected_branches ? 'checked' : ''}>
                                <label for="edit-pair-only-protected-${pair.id}">Only mirror protected branches</label>
                            </div>
                            <div style="display:grid; gap:8px">
                                <input class="table-input" id="edit-pair-regex-${pair.id}" value="${escapeHtml(pair.mirror_branch_regex || '')}" placeholder="Mirror branch regex (pull only)">
                                <input class="table-input" id="edit-pair-user-${pair.id}" value="${escapeHtml(pair.mirror_user_id === null || pair.mirror_user_id === undefined ? '' : String(pair.mirror_user_id))}" placeholder="Mirror user id (pull only)">
                            </div>
                        </div>
                    </td>
                    <td>
                        <div class="table-actions">
                            <button class="btn btn-primary btn-small" onclick="savePairEdit(${pair.id})">Save</button>
                            <button class="btn btn-secondary btn-small" onclick="cancelPairEdit()">Cancel</button>
                        </div>
                    </td>
                </tr>
            `;
        }

        return `
            <tr>
                <td><strong>${escapeHtml(pair.name)}</strong></td>
                <td>${escapeHtml(source?.name || 'Unknown')}</td>
                <td>${escapeHtml(target?.name || 'Unknown')}</td>
                <td>${badge(pair.mirror_direction)}</td>
                <td>${settingsCell}</td>
                <td>
                    <div class="table-actions">
                        <button class="btn btn-secondary btn-small" onclick="beginPairEdit(${pair.id})">Edit</button>
                        <button class="btn btn-danger btn-small" onclick="deletePair(${pair.id})">Delete</button>
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}

function beginPairEdit(id) {
    state.editing.pairId = id;
    state.editing.instanceId = null;
    state.editing.mirrorId = null;
    state.editing.tokenId = null;
    renderPairs(state.pairs);
    setTimeout(() => applyPairEditDirection(id), 0);
}

function cancelPairEdit() {
    state.editing.pairId = null;
    renderPairs(state.pairs);
}

function applyPairEditDirection(pairId) {
    const dirEl = document.getElementById(`edit-pair-direction-${pairId}`);
    const triggerEl = document.getElementById(`edit-pair-trigger-${pairId}`);
    const regexEl = document.getElementById(`edit-pair-regex-${pairId}`);
    const userEl = document.getElementById(`edit-pair-user-${pairId}`);
    if (!dirEl) return;
    const direction = (dirEl.value || '').toString().toLowerCase();
    const isPush = direction === 'push';

    if (triggerEl) {
        triggerEl.disabled = isPush;
        if (isPush) triggerEl.checked = false;
    }
    if (regexEl) {
        regexEl.disabled = isPush;
        if (isPush) regexEl.value = '';
    }
    if (userEl) {
        userEl.disabled = isPush;
        if (isPush) userEl.value = '';
    }
}

async function savePairEdit(id) {
    const nameEl = document.getElementById(`edit-pair-name-${id}`);
    const descEl = document.getElementById(`edit-pair-description-${id}`);
    const dirEl = document.getElementById(`edit-pair-direction-${id}`);
    const protectedEl = document.getElementById(`edit-pair-protected-${id}`);
    const overwriteEl = document.getElementById(`edit-pair-overwrite-${id}`);
    const triggerEl = document.getElementById(`edit-pair-trigger-${id}`);
    const onlyProtectedEl = document.getElementById(`edit-pair-only-protected-${id}`);
    const regexEl = document.getElementById(`edit-pair-regex-${id}`);
    const userEl = document.getElementById(`edit-pair-user-${id}`);
    if (!nameEl || !dirEl || !protectedEl || !overwriteEl || !onlyProtectedEl) return;

    const direction = (dirEl.value || '').toString().toLowerCase();
    const isPush = direction === 'push';
    const regexRaw = (regexEl?.value || '').toString().trim();
    const userRaw = (userEl?.value || '').toString().trim();

    const payload = {
        name: (nameEl.value || '').toString().trim(),
        description: (descEl?.value || '').toString().trim() || null,
        mirror_direction: direction || 'pull',
        mirror_protected_branches: !!protectedEl.checked,
        mirror_overwrite_diverged: !!overwriteEl.checked,
        only_mirror_protected_branches: !!onlyProtectedEl.checked,
        mirror_trigger_builds: isPush ? false : !!triggerEl?.checked,
        mirror_branch_regex: isPush ? null : (regexRaw || null),
        mirror_user_id: isPush ? null : (userRaw ? parseInt(userRaw) : null),
    };

    try {
        await apiRequest(`/api/pairs/${id}`, {
            method: 'PUT',
            body: JSON.stringify(payload),
        });
        showMessage('Instance pair updated successfully', 'success');
        state.editing.pairId = null;
        await loadPairs();
    } catch (error) {
        console.error('Failed to update pair:', error);
    }
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
    // Warn about cascading deletes (mirrors) before taking action.
    let confirmMsg = 'Are you sure you want to delete this instance pair?';
    try {
        const mirrors = await apiRequest(`/api/mirrors?instance_pair_id=${id}`).catch(() => []);
        const mirrorCount = (mirrors || []).length;
        if (mirrorCount) {
            confirmMsg =
                `Deleting this instance pair will also delete:\n` +
                `- ${mirrorCount} mirror(s)\n\n` +
                `This cannot be undone.\n\n` +
                `Continue?`;
        }
    } catch (e) {
        // Fall back to generic confirm.
    }

    if (!confirm(confirmMsg)) return;

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
        cancelTokenEdit();
        await loadTokens();
    } catch (error) {
        console.error('Failed to create token:', error);
    }
}

function _setTokenFormEditMode(isEditing) {
    const submit = document.getElementById('token-submit');
    const cancel = document.getElementById('token-cancel-edit');
    const instanceSel = document.getElementById('token-instance');
    const groupPathEl = document.getElementById('token-group-path');
    const tokenEl = document.getElementById('token-value');
    if (submit) submit.textContent = isEditing ? 'Update Token' : 'Add Group Token';
    if (cancel) cancel.classList.toggle('hidden', !isEditing);

    // Avoid breaking existing mirror auth by changing the lookup key in-place.
    if (instanceSel) instanceSel.disabled = !!isEditing;
    if (groupPathEl) groupPathEl.disabled = !!isEditing;

    if (!isEditing) {
        if (instanceSel) instanceSel.disabled = false;
        if (groupPathEl) groupPathEl.disabled = false;
    }

    // In edit mode, the user must re-enter a fresh token value (we never display the old token).
    if (tokenEl) {
        tokenEl.required = true;
        tokenEl.placeholder = isEditing ? 'Paste new token value' : 'Group access token';
    }
}

function beginTokenEdit(tokenId) {
    const token = (state.tokens || []).find(t => t.id === tokenId);
    if (!token) return;

    state.editing.tokenId = tokenId;
    state.editing.instanceId = null;
    state.editing.pairId = null;
    state.editing.mirrorId = null;

    const instanceSel = document.getElementById('token-instance');
    const groupPathEl = document.getElementById('token-group-path');
    const nameEl = document.getElementById('token-name');
    const tokenEl = document.getElementById('token-value');

    if (instanceSel) instanceSel.value = String(token.gitlab_instance_id);
    if (groupPathEl) groupPathEl.value = token.group_path || '';
    if (nameEl) nameEl.value = token.token_name || '';
    if (tokenEl) tokenEl.value = '';

    _setTokenFormEditMode(true);
    document.getElementById('token-form')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function cancelTokenEdit() {
    state.editing.tokenId = null;
    _setTokenFormEditMode(false);

    const instanceSel = document.getElementById('token-instance');
    const groupPathEl = document.getElementById('token-group-path');
    const nameEl = document.getElementById('token-name');
    const tokenEl = document.getElementById('token-value');
    if (instanceSel) instanceSel.disabled = false;
    if (groupPathEl) groupPathEl.disabled = false;
    if (nameEl) nameEl.value = '';
    if (tokenEl) tokenEl.value = '';
}

async function updateToken() {
    const tokenId = state.editing?.tokenId;
    if (!tokenId) return;

    const nameEl = document.getElementById('token-name');
    const tokenEl = document.getElementById('token-value');
    if (!nameEl || !tokenEl) return;

    const tokenValue = (tokenEl.value || '').toString().trim();
    if (!tokenValue) {
        showMessage('Please paste a new token value', 'error');
        return;
    }

    const payload = {
        token_name: (nameEl.value || '').toString().trim(),
        token: tokenValue,
    };

    try {
        await apiRequest(`/api/tokens/${tokenId}`, {
            method: 'PUT',
            body: JSON.stringify(payload),
        });
        showMessage('Group access token updated successfully', 'success');
        cancelTokenEdit();
        await loadTokens();
    } catch (error) {
        console.error('Failed to update token:', error);
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
            if (gd) actions.push(`<button class="btn btn-secondary btn-small" onclick="editGroupDefaults(${gd.id})">Edit defaults</button>`);
            if (gd) actions.push(`<button class="btn btn-danger btn-small" onclick="deleteGroupDefaults(${gd.id})">Delete defaults</button>`);
            if (srcTok) actions.push(`<button class="btn btn-secondary btn-small" onclick="beginTokenEdit(${srcTok.id})" title="Update token on ${escapeHtml(srcName)}">Update token</button>`);
            if (tgtTok) actions.push(`<button class="btn btn-secondary btn-small" onclick="beginTokenEdit(${tgtTok.id})" title="Update token on ${escapeHtml(tgtName)}">Update token</button>`);
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

    applyGroupDefaultsDirectionUI();
}

function applyGroupDefaultsDirectionUI() {
    const pairSel = document.getElementById('group-defaults-pair');
    const dirSel = document.getElementById('group-defaults-direction');
    const triggerEl = document.getElementById('group-defaults-trigger');
    const regexEl = document.getElementById('group-defaults-branch-regex');
    const userEl = document.getElementById('group-defaults-mirror-user-id');
    if (!pairSel || !dirSel) return;

    const pair = state.pairs.find(p => p.id === parseInt(pairSel.value || '0'));
    const effDir = ((dirSel.value || '') || pair?.mirror_direction || '').toString().toLowerCase();
    const isPush = effDir === 'push';

    if (triggerEl) {
        triggerEl.disabled = isPush;
        if (isPush) {
            triggerEl.checked = false;
            triggerEl.indeterminate = true;
            triggerEl.title = 'Not applicable for push mirrors';
        }
    }
    if (regexEl) {
        regexEl.disabled = isPush;
        if (isPush) regexEl.value = '';
    }
    if (userEl) {
        userEl.disabled = isPush;
        if (isPush) userEl.value = '';
    }
}

function editGroupDefaults(groupDefaultId) {
    const row = (state.groupDefaults || []).find(gd => gd.id === groupDefaultId);
    if (!row) return;

    const pairSel = document.getElementById('group-defaults-pair');
    const pathEl = document.getElementById('group-defaults-group-path');
    const dirSel = document.getElementById('group-defaults-direction');
    const overwriteEl = document.getElementById('group-defaults-overwrite');
    const triggerEl = document.getElementById('group-defaults-trigger');
    const onlyProtectedEl = document.getElementById('group-defaults-only-protected');
    const regexEl = document.getElementById('group-defaults-branch-regex');
    const userEl = document.getElementById('group-defaults-mirror-user-id');

    if (pairSel) pairSel.value = String(row.instance_pair_id);
    if (pathEl) pathEl.value = row.group_path || '';
    if (dirSel) dirSel.value = row.mirror_direction || '';

    const setTri = (el, v) => {
        if (!el) return;
        if (v === null || v === undefined) {
            el.checked = false;
            el.indeterminate = true;
            el.title = 'Inherit pair default';
        } else {
            el.indeterminate = false;
            el.checked = !!v;
            el.title = '';
        }
        el.onchange = () => {
            el.indeterminate = false;
            el.title = '';
        };
    };
    setTri(overwriteEl, row.mirror_overwrite_diverged);
    setTri(triggerEl, row.mirror_trigger_builds);
    setTri(onlyProtectedEl, row.only_mirror_protected_branches);

    if (regexEl) regexEl.value = row.mirror_branch_regex || '';
    if (userEl) userEl.value = row.mirror_user_id === null || row.mirror_user_id === undefined ? '' : String(row.mirror_user_id);

    applyGroupDefaultsDirectionUI();
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
    const container = document.getElementById('mirrors-list');
    if (!container) return;

    if (!state.selectedPair) {
        showEmptyState(
            container,
            'Select an Instance Pair',
            'Choose an instance pair from the dropdown above to view and manage its mirrors.',
            null
        );
        return;
    }

    try {
        showSkeletonLoader(container, 5);

        const mirrors = await apiRequest(`/api/mirrors?instance_pair_id=${state.selectedPair}`);
        state.mirrors = mirrors;
        renderMirrors(mirrors);
    } catch (error) {
        console.error('Failed to load mirrors:', error);
        showErrorState(container, error, loadMirrors);
        showMessage(error.message, 'error');
    }
}

function renderMirrors(mirrors) {
    const tbody = document.getElementById('mirrors-list');
    if (!tbody) return;

    if (mirrors.length === 0) {
        showEmptyState(
            tbody,
            'No Mirrors Yet',
            'This instance pair doesn\'t have any mirrors configured. Create one using the form above.',
            null
        );
        return;
    }

    const fmtBool = (v) => (v === null || v === undefined) ? '<span class="text-muted">n/a</span>' : (v ? 'true' : 'false');
    const fmtStr = (v) => (v === null || v === undefined || String(v).trim() === '')
        ? '<span class="text-muted">n/a</span>'
        : `<code>${escapeHtml(String(v))}</code>`;
    const fmtUser = (v) => (v === null || v === undefined) ? '<span class="text-muted">n/a</span>' : escapeHtml(String(v));

    const editingId = state.editing?.mirrorId;
    const overrideBoolSelectValue = (v) => (v === null || v === undefined) ? '__inherit__' : (v ? 'true' : 'false');

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

        const isEditing = editingId === mirror.id;
        if (isEditing) {
            const isPush = dir === 'push';
            const overwriteSel = overrideBoolSelectValue(mirror.mirror_overwrite_diverged);
            const onlyProtectedSel = overrideBoolSelectValue(mirror.only_mirror_protected_branches);
            const triggerSel = overrideBoolSelectValue(mirror.mirror_trigger_builds);

            const regexMode = (mirror.mirror_branch_regex === null || mirror.mirror_branch_regex === undefined) ? '__inherit__' : '__override__';
            const userMode = (mirror.mirror_user_id === null || mirror.mirror_user_id === undefined) ? '__inherit__' : '__override__';

            return `
                <tr data-editing="true">
                    <td class="cell-locked" title="Project paths cannot be edited">${escapeHtml(mirror.source_project_path)}</td>
                    <td class="cell-locked" title="Project paths cannot be edited">${escapeHtml(mirror.target_project_path)}</td>
                    <td>
                        <div data-dir="${escapeHtml(dir)}" style="display:grid; gap:10px">
                            <div>
                                <span class="text-muted">direction:</span>
                                <span class="badge badge-info">${escapeHtml(dir || 'n/a')}</span>
                            </div>

                            <div style="display:grid; gap:8px">
                                <label class="text-muted" for="edit-mirror-overwrite-${mirror.id}">overwrite</label>
                                <select class="table-select" id="edit-mirror-overwrite-${mirror.id}">
                                    <option value="__inherit__"${overwriteSel === '__inherit__' ? ' selected' : ''}>Inherit</option>
                                    <option value="true"${overwriteSel === 'true' ? ' selected' : ''}>true</option>
                                    <option value="false"${overwriteSel === 'false' ? ' selected' : ''}>false</option>
                                </select>

                                <label class="text-muted" for="edit-mirror-only-protected-${mirror.id}">only_protected</label>
                                <select class="table-select" id="edit-mirror-only-protected-${mirror.id}">
                                    <option value="__inherit__"${onlyProtectedSel === '__inherit__' ? ' selected' : ''}>Inherit</option>
                                    <option value="true"${onlyProtectedSel === 'true' ? ' selected' : ''}>true</option>
                                    <option value="false"${onlyProtectedSel === 'false' ? ' selected' : ''}>false</option>
                                </select>

                                <label class="text-muted" for="edit-mirror-trigger-${mirror.id}">trigger (pull only)</label>
                                <select class="table-select" id="edit-mirror-trigger-${mirror.id}" ${isPush ? 'disabled' : ''}>
                                    <option value="__inherit__"${triggerSel === '__inherit__' ? ' selected' : ''}>Inherit</option>
                                    <option value="true"${triggerSel === 'true' ? ' selected' : ''}>true</option>
                                    <option value="false"${triggerSel === 'false' ? ' selected' : ''}>false</option>
                                </select>

                                <label class="text-muted">branch regex (pull only)</label>
                                <div style="display:grid; gap:6px">
                                    <select class="table-select" id="edit-mirror-regex-mode-${mirror.id}" onchange="applyMirrorEditControls(${mirror.id})" ${isPush ? 'disabled' : ''}>
                                        <option value="__inherit__"${regexMode === '__inherit__' ? ' selected' : ''}>Inherit</option>
                                        <option value="__override__"${regexMode === '__override__' ? ' selected' : ''}>Override</option>
                                    </select>
                                    <input class="table-input" id="edit-mirror-regex-${mirror.id}" value="${escapeHtml(mirror.mirror_branch_regex || '')}" placeholder="^main$ (optional)" ${isPush ? 'disabled' : ''}>
                                </div>

                                <label class="text-muted">mirror user id (pull only)</label>
                                <div style="display:grid; gap:6px">
                                    <select class="table-select" id="edit-mirror-user-mode-${mirror.id}" onchange="applyMirrorEditControls(${mirror.id})" ${isPush ? 'disabled' : ''}>
                                        <option value="__inherit__"${userMode === '__inherit__' ? ' selected' : ''}>Inherit</option>
                                        <option value="__override__"${userMode === '__override__' ? ' selected' : ''}>Override</option>
                                    </select>
                                    <input class="table-input" id="edit-mirror-user-${mirror.id}" value="${escapeHtml(mirror.mirror_user_id === null || mirror.mirror_user_id === undefined ? '' : String(mirror.mirror_user_id))}" placeholder="123 (optional)" ${isPush ? 'disabled' : ''}>
                                </div>
                            </div>
                        </div>
                    </td>
                    <td>
                        <div class="checkbox-group">
                            <input type="checkbox" id="edit-mirror-enabled-${mirror.id}" ${mirror.enabled ? 'checked' : ''}>
                            <label for="edit-mirror-enabled-${mirror.id}">Enabled</label>
                        </div>
                    </td>
                    <td class="cell-locked">${updateStatus}</td>
                    <td class="cell-locked" data-sort="${escapeHtml(mirror.last_successful_update || '')}">
                        ${mirror.last_successful_update ? new Date(mirror.last_successful_update).toLocaleString() : 'Never'}
                    </td>
                    <td>
                        <div class="table-actions">
                            <button class="btn btn-primary btn-small" onclick="saveMirrorEdit(${mirror.id})">Save</button>
                            <button class="btn btn-secondary btn-small" onclick="cancelMirrorEdit()">Cancel</button>
                            <button class="btn btn-danger btn-small" onclick="deleteMirror(${mirror.id})">Delete</button>
                        </div>
                    </td>
                </tr>
            `;
        }

        return `
            <tr>
                <td>${escapeHtml(mirror.source_project_path)}</td>
                <td>${escapeHtml(mirror.target_project_path)}</td>
                <td>${settingsCell}</td>
                <td>${statusBadge}</td>
                <td>${updateStatus}</td>
                <td data-sort="${escapeHtml(mirror.last_successful_update || '')}">
                    ${mirror.last_successful_update ? new Date(mirror.last_successful_update).toLocaleString() : 'Never'}
                </td>
                <td>
                    <div class="table-actions">
                        <button class="btn btn-secondary btn-small" onclick="beginMirrorEdit(${mirror.id})">Edit</button>
                        <button class="btn btn-success btn-small" onclick="triggerMirrorUpdate(${mirror.id})" title="Trigger an immediate mirror sync">Sync</button>
                        <button class="btn btn-danger btn-small" onclick="deleteMirror(${mirror.id})">Delete</button>
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}

function beginMirrorEdit(id) {
    state.editing.mirrorId = id;
    state.editing.instanceId = null;
    state.editing.pairId = null;
    state.editing.tokenId = null;
    renderMirrors(state.mirrors);
    setTimeout(() => applyMirrorEditControls(id), 0);
}

function cancelMirrorEdit() {
    state.editing.mirrorId = null;
    renderMirrors(state.mirrors);
}

function applyMirrorEditControls(mirrorId) {
    const rowContainer = document.querySelector(`#edit-mirror-overwrite-${mirrorId}`)?.closest('[data-dir]');
    const dir = (rowContainer?.dataset?.dir || '').toString().toLowerCase();
    const isPush = dir === 'push';

    const regexModeEl = document.getElementById(`edit-mirror-regex-mode-${mirrorId}`);
    const regexEl = document.getElementById(`edit-mirror-regex-${mirrorId}`);
    const userModeEl = document.getElementById(`edit-mirror-user-mode-${mirrorId}`);
    const userEl = document.getElementById(`edit-mirror-user-${mirrorId}`);

    if (regexEl) {
        const mode = (regexModeEl?.value || '__inherit__').toString();
        regexEl.disabled = isPush || mode !== '__override__';
        if (isPush) regexEl.value = '';
    }
    if (userEl) {
        const mode = (userModeEl?.value || '__inherit__').toString();
        userEl.disabled = isPush || mode !== '__override__';
        if (isPush) userEl.value = '';
    }
}

async function saveMirrorEdit(id) {
    const mirror = (state.mirrors || []).find(m => m.id === id);
    if (!mirror) return;
    const dir = (mirror.effective_mirror_direction || mirror.mirror_direction || '').toString().toLowerCase();
    const isPush = dir === 'push';

    const enabledEl = document.getElementById(`edit-mirror-enabled-${id}`);
    const overwriteEl = document.getElementById(`edit-mirror-overwrite-${id}`);
    const onlyProtectedEl = document.getElementById(`edit-mirror-only-protected-${id}`);
    const triggerEl = document.getElementById(`edit-mirror-trigger-${id}`);
    const regexModeEl = document.getElementById(`edit-mirror-regex-mode-${id}`);
    const regexEl = document.getElementById(`edit-mirror-regex-${id}`);
    const userModeEl = document.getElementById(`edit-mirror-user-mode-${id}`);
    const userEl = document.getElementById(`edit-mirror-user-${id}`);
    if (!enabledEl || !overwriteEl || !onlyProtectedEl) return;

    const parseBoolOverride = (v) => {
        if (v === '__inherit__') return null;
        if (v === 'true') return true;
        if (v === 'false') return false;
        return null;
    };

    const payload = {
        enabled: !!enabledEl.checked,
        mirror_overwrite_diverged: parseBoolOverride(overwriteEl.value),
        only_mirror_protected_branches: parseBoolOverride(onlyProtectedEl.value),
        mirror_trigger_builds: isPush ? null : parseBoolOverride(triggerEl?.value || '__inherit__'),
        mirror_branch_regex: null,
        mirror_user_id: null,
    };

    if (!isPush) {
        const regexMode = (regexModeEl?.value || '__inherit__').toString();
        const regexRaw = (regexEl?.value || '').toString().trim();
        payload.mirror_branch_regex = regexMode === '__override__' ? (regexRaw || null) : null;

        const userMode = (userModeEl?.value || '__inherit__').toString();
        const userRaw = (userEl?.value || '').toString().trim();
        payload.mirror_user_id = userMode === '__override__' ? (userRaw ? parseInt(userRaw) : null) : null;
    }

    try {
        await apiRequest(`/api/mirrors/${id}`, {
            method: 'PUT',
            body: JSON.stringify(payload),
        });
        showMessage('Mirror updated successfully', 'success');
        state.editing.mirrorId = null;
        await loadMirrors();
    } catch (error) {
        console.error('Failed to update mirror:', error);
    }
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

    // Preflight: check for existing GitLab remote mirrors before attempting to create.
    // This is especially important for pull mirrors, where GitLab effectively supports only one.
    try {
        const preflightPayload = {
            instance_pair_id: data.instance_pair_id,
            source_project_id: data.source_project_id,
            source_project_path: data.source_project_path,
            target_project_id: data.target_project_id,
            target_project_path: data.target_project_path,
        };
        if (mirrorDirection) preflightPayload.mirror_direction = mirrorDirection;

        const preflight = await apiRequest('/api/mirrors/preflight', {
            method: 'POST',
            body: JSON.stringify(preflightPayload),
        });

        const dir = (preflight?.effective_direction || effectiveDirection || '').toString().toLowerCase();
        const same = preflight?.existing_same_direction || [];
        if (same.length > 0) {
            const urls = same.map(m => (m.url || '').toString()).filter(Boolean);
            const list = urls.length ? `\n\nExisting mirror URL(s):\n- ${urls.join('\n- ')}` : '';

            if (dir === 'pull') {
                const msg =
                    `This target project already has a pull mirror configured in GitLab.\n` +
                    `GitLab allows only one pull mirror per project.\n\n` +
                    `Click OK to delete the existing pull mirror now, or Cancel to abort.` +
                    list;

                if (!confirm(msg)) {
                    showMessage('Mirror creation cancelled (existing pull mirror detected)', 'info');
                    return;
                }

                await apiRequest('/api/mirrors/remove-existing', {
                    method: 'POST',
                    body: JSON.stringify({
                        ...preflightPayload,
                        remote_mirror_ids: same.map(m => m.id).filter(v => typeof v === 'number'),
                    }),
                });
            } else if (dir === 'push') {
                const msg =
                    `This source project already has ${same.length} push mirror(s) configured in GitLab.\n\n` +
                    `Click OK to delete them first, or Cancel to keep them and create an additional push mirror.` +
                    list;

                if (confirm(msg)) {
                    await apiRequest('/api/mirrors/remove-existing', {
                        method: 'POST',
                        body: JSON.stringify({
                            ...preflightPayload,
                            remote_mirror_ids: same.map(m => m.id).filter(v => typeof v === 'number'),
                        }),
                    });
                }
            }
        }
    } catch (e) {
        // Preflight is best-effort; creation endpoint will still enforce pull mirror uniqueness.
    }

    const submitButton = form.querySelector('button[type="submit"]');

    try {
        showButtonLoading(submitButton, true);

        await apiRequest('/api/mirrors', {
            method: 'POST',
            body: JSON.stringify(data)
        });

        showMessage('Mirror created successfully', 'success');
        form.reset();
        await loadMirrors();
    } catch (error) {
        console.error('Failed to create mirror:', error);
        // Error message already shown via apiRequest
        if (error instanceof APIError && error.type === 'validation') {
            // Keep form filled for validation errors
            return;
        }
    } finally {
        showButtonLoading(submitButton, false);
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
