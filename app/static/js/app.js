// State management
const state = {
    instances: [],
    pairs: [],
    mirrors: [],
    mirrorsPagination: {
        total: 0,
        page: 1,
        pageSize: 50,
        totalPages: 0,
        orderBy: 'created_at',
        orderDir: 'desc',
        groupPath: '',
        viewMode: 'flat', // 'flat' or 'tree'
    },
    selectedPair: null,
    mirrorProjectInstances: { source: null, target: null },
    editing: {
        instanceId: null,
        pairId: null,
        mirrorId: null,
    },
};

// Initialize the application
document.addEventListener('DOMContentLoaded', async () => {
    initDarkMode();
    initTabs();
    setupEventListeners();
    initTableEnhancements();
    initGlobalSearch();
    initUrlState();
    initUserMenu();

    // Demo screenshots open static HTML files via file://; avoid API calls there.
    const isFileDemo = (window?.location?.protocol || '') === 'file:';
    if (!isFileDemo) {
        // Initialize auth first and wait for it to complete
        // This ensures the JWT token is available for subsequent API calls
        await initAuth();

        // Now load data (token is ready)
        loadInstances();
        loadPairs();

        // Only load dashboard and start polling if dashboard tab is (or will be) active.
        // initUrlState() may switch tabs via a 50ms timeout, so check the URL param.
        const urlTabParam = new URLSearchParams(window.location.search).get('tab');
        if (!urlTabParam || urlTabParam === 'dashboard-tab') {
            loadDashboard();
            startLivePolling();
        }
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
        'mirrors-list': {
            tableLabel: 'Mirrors',
            columns: [
                { key: 'source_project', label: 'Source Project', sortable: true, filter: 'text' },
                { key: 'target_project', label: 'Target Project', sortable: true, filter: 'text' },
                { key: 'settings', label: 'Effective Settings', sortable: false, filter: 'text' },
                { key: 'status', label: 'Status', sortable: true, filter: 'select' },
                { key: 'sync_status', label: 'Sync Status', sortable: true, filter: 'select' },
                { key: 'last_sync', label: 'Last Sync', sortable: true, filter: 'text' },
                { key: 'token', label: 'Token', sortable: true, filter: 'select' },
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
            if (targetId === 'dashboard-tab') {
                // Start live polling when entering dashboard
                loadDashboard();
                startLivePolling();
            } else {
                // Stop live polling when leaving dashboard
                stopLivePolling();

                if (targetId === 'mirrors-tab') {
                    loadMirrors();
                } else if (targetId === 'topology-tab') {
                    if (typeof window.initTopologyTab === 'function') {
                        window.initTopologyTab();
                    }
                } else if (targetId === 'backup-tab') {
                    loadBackupStats();
                } else if (targetId === 'settings-tab') {
                    loadUsers();
                } else if (targetId === 'about-tab') {
                    loadAboutInfo();
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

    // Direction comes from pair (no per-mirror override)
    // The UI is updated in handleEditMirror when a pair is selected

    // Pair selector for mirrors
    document.getElementById('pair-selector')?.addEventListener('change', (e) => {
        state.selectedPair = parseInt(e.target.value);
        if (state.selectedPair) {
            loadMirrors();
            loadProjectsForPair();
        }
    });

    // Project autocomplete for mirrors (Mirrors tab)
    initProjectAutocomplete('source');
    initProjectAutocomplete('target');

    // Backup buttons
    document.getElementById('create-backup-btn')?.addEventListener('click', async () => {
        await createBackup();
    });

    document.getElementById('select-backup-btn')?.addEventListener('click', () => {
        document.getElementById('restore-file-input')?.click();
    });

    document.getElementById('restore-file-input')?.addEventListener('change', (e) => {
        const file = e.target.files?.[0];
        const filenameSpan = document.getElementById('restore-filename');
        const restoreBtn = document.getElementById('restore-backup-btn');

        if (file) {
            filenameSpan.textContent = file.name;
            restoreBtn.disabled = false;
        } else {
            filenameSpan.textContent = 'No file selected';
            restoreBtn.disabled = true;
        }
    });

    document.getElementById('restore-backup-btn')?.addEventListener('click', async () => {
        await restoreBackup();
    });
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
        // Build headers with optional auth token
        const headers = {
            'Content-Type': 'application/json',
            ...options.headers
        };

        // Include JWT token if available (for multi-user mode)
        if (typeof authState !== 'undefined' && authState.token) {
            headers['Authorization'] = `Bearer ${authState.token}`;
        }

        const response = await fetch(url, {
            ...options,
            headers
        });

        if (!response.ok) {
            let errorData;
            let errorMessage = 'Request failed';
            let errorType = 'server';

            try {
                errorData = await response.json();

                if (Array.isArray(errorData.detail)) {
                    // Handle Pydantic validation error format (detail is an array of error objects)
                    errorMessage = errorData.detail
                        .map(err => {
                            let msg = err.msg || '';
                            // Strip "Value error, " prefix from Pydantic v2 messages
                            msg = msg.replace(/^Value error,\s*/i, '');
                            return msg;
                        })
                        .filter(Boolean)
                        .join('; ') || errorMessage;
                } else if (typeof errorData.detail === 'object' && errorData.detail !== null && errorData.detail.message) {
                    // Handle complex error details (like conflict with existing_mirror_id)
                    errorMessage = errorData.detail.message;
                } else if (errorData.detail) {
                    // Handle simple string detail
                    errorMessage = errorData.detail;
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

    // Load system health (non-blocking)
    loadSystemHealth();
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

// ----------------------------
// System Health
// ----------------------------

async function loadSystemHealth() {
    const container = document.getElementById('system-health-content');
    if (!container) return;

    try {
        const health = await apiRequest('/api/health');
        renderSystemHealth(health, container);
    } catch (error) {
        console.error('Failed to load system health:', error);
        container.innerHTML = `
            <div class="system-health-status unhealthy">
                <span class="health-status-icon">⚠️</span>
                <div class="health-status-text">
                    <div class="health-status-title">Unable to check health</div>
                    <div class="health-status-timestamp">${error.message || 'Connection failed'}</div>
                </div>
            </div>
        `;
    }
}

function refreshSystemHealth() {
    loadSystemHealth();
}

function renderSystemHealth(health, container) {
    const statusIcons = {
        healthy: '✓',
        degraded: '⚠',
        unhealthy: '✕'
    };

    const statusLabels = {
        healthy: 'All Systems Operational',
        degraded: 'Some Issues Detected',
        unhealthy: 'System Issues'
    };

    const timestamp = health.timestamp
        ? new Date(health.timestamp).toLocaleString()
        : 'Just now';

    let html = `
        <div class="system-health-status ${health.status}">
            <span class="health-status-icon">${statusIcons[health.status] || '?'}</span>
            <div class="health-status-text">
                <div class="health-status-title">${statusLabels[health.status] || health.status}</div>
                <div class="health-status-timestamp">Last checked: ${timestamp}</div>
            </div>
        </div>
        <div class="health-components">
    `;

    // Render each component
    for (const component of health.components || []) {
        html += `
            <div class="health-component">
                <div>
                    <div class="health-component-name">
                        ${getComponentIcon(component.name)} ${formatComponentName(component.name)}
                    </div>
                    ${component.message ? `<div class="health-component-message">${escapeHtml(component.message)}</div>` : ''}
                </div>
                <span class="health-component-status ${component.status}">${component.status}</span>
            </div>
        `;
    }

    html += '</div>';

    // Add token warnings if any
    if (health.tokens) {
        if (health.tokens.expired > 0) {
            html += `
                <div class="health-token-warning">
                    <span>⚠️</span>
                    <span>${health.tokens.expired} mirror token(s) have expired and need rotation</span>
                </div>
            `;
        } else if (health.tokens.expiring_soon > 0) {
            html += `
                <div class="health-token-warning">
                    <span>⏰</span>
                    <span>${health.tokens.expiring_soon} mirror token(s) expiring within 30 days</span>
                </div>
            `;
        }
    }

    container.innerHTML = html;
}

function getComponentIcon(name) {
    const icons = {
        database: '🗄️',
        mirrors: '🔄',
        tokens: '🔑',
        gitlab_instances: '🦊'
    };
    return icons[name] || '📦';
}

function formatComponentName(name) {
    const names = {
        database: 'Database',
        mirrors: 'Mirror Sync',
        tokens: 'Access Tokens',
        gitlab_instances: 'GitLab Instances'
    };
    return names[name] || name.charAt(0).toUpperCase() + name.slice(1).replace(/_/g, ' ');
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

    // Don't create chart if canvas is not visible (e.g., dashboard tab is hidden).
    // Chart.js internals crash when computing layout on a hidden canvas.
    if (!canvas.offsetParent) return;

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

    // Update legend counts (null-guard in case elements are missing)
    const successEl = document.getElementById('health-success-count');
    const failedEl = document.getElementById('health-failed-count');
    const pendingEl = document.getElementById('health-pending-count');
    const unknownEl = document.getElementById('health-unknown-count');
    if (successEl) successEl.textContent = healthData.success;
    if (failedEl) failedEl.textContent = healthData.failed;
    if (pendingEl) pendingEl.textContent = healthData.pending;
    if (unknownEl) unknownEl.textContent = healthData.unknown;
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
    // Clear any existing interval first
    if (livePollingInterval) {
        clearInterval(livePollingInterval);
        livePollingInterval = null;
    }

    // Poll every 30 seconds
    livePollingInterval = setInterval(updateLiveStats, 30000);

    // Initial update
    updateLiveStats();
}

function stopLivePolling() {
    if (livePollingInterval) {
        clearInterval(livePollingInterval);
        livePollingInterval = null;
    }
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
        showMessage(error.message || 'Failed to update instance', 'error');
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
        showMessage(error.message || 'Failed to create instance', 'error');
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

        const settingsCell = (() => {
            const pieces = [];
            pieces.push(`<span class="text-muted">overwrite:</span> ${fmtBool(!!pair.mirror_overwrite_diverged)}`);
            pieces.push(`<span class="text-muted">only_protected:</span> ${fmtBool(!!pair.only_mirror_protected_branches)}`);
            if (direction === 'pull') {
                pieces.push(`<span class="text-muted">trigger:</span> ${fmtBool(!!pair.mirror_trigger_builds)}`);
                pieces.push(`<span class="text-muted">regex:</span> ${fmtStr(pair.mirror_branch_regex)}`);
            } else {
                pieces.push(`<span class="text-muted">trigger:</span> <span class="text-muted">n/a</span>`);
                pieces.push(`<span class="text-muted">regex:</span> <span class="text-muted">n/a</span>`);
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
                        <button class="btn btn-primary btn-small" onclick="syncAllMirrors(${pair.id})" title="Trigger sync for all enabled mirrors in this pair">Sync All</button>
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
    const overwriteEl = document.getElementById(`edit-pair-overwrite-${id}`);
    const triggerEl = document.getElementById(`edit-pair-trigger-${id}`);
    const onlyProtectedEl = document.getElementById(`edit-pair-only-protected-${id}`);
    const regexEl = document.getElementById(`edit-pair-regex-${id}`);
    const userEl = document.getElementById(`edit-pair-user-${id}`);
    if (!nameEl || !dirEl || !overwriteEl || !onlyProtectedEl) return;

    const direction = (dirEl.value || '').toString().toLowerCase();
    const isPush = direction === 'push';
    const regexRaw = (regexEl?.value || '').toString().trim();
    const userRaw = (userEl?.value || '').toString().trim();

    const payload = {
        name: (nameEl.value || '').toString().trim(),
        description: (descEl?.value || '').toString().trim() || null,
        mirror_direction: direction || 'pull',
        mirror_overwrite_diverged: !!overwriteEl.checked,
        only_mirror_protected_branches: !!onlyProtectedEl.checked,
        mirror_trigger_builds: isPush ? false : !!triggerEl?.checked,
        mirror_branch_regex: isPush ? null : (regexRaw || null),
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
        showMessage(error.message || 'Failed to update pair', 'error');
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

    const data = {
        name: formData.get('name'),
        source_instance_id: parseInt(formData.get('source_instance_id')),
        target_instance_id: parseInt(formData.get('target_instance_id')),
        mirror_direction: formData.get('mirror_direction'),
        mirror_overwrite_diverged: formData.get('mirror_overwrite_diverged') === 'on',
        mirror_trigger_builds: formData.get('mirror_trigger_builds') === 'on',
        only_mirror_protected_branches: formData.get('only_mirror_protected_branches') === 'on',
        mirror_branch_regex: branchRegexRaw || null,
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
        showMessage(error.message || 'Failed to create pair', 'error');
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

async function syncAllMirrors(pairId) {
    // Get mirror count first
    let mirrorCount = 0;
    try {
        const mirrors = await apiRequest(`/api/mirrors?instance_pair_id=${pairId}&enabled=true`);
        mirrorCount = (mirrors || []).length;
    } catch (e) {
        console.error('Failed to get mirror count:', e);
    }

    if (mirrorCount === 0) {
        showMessage('No enabled mirrors found for this pair', 'warning');
        return;
    }

    const pair = state.pairs.find(p => p.id === pairId);
    const pairName = pair ? pair.name : `pair ${pairId}`;

    const confirmMsg =
        `Trigger sync for all ${mirrorCount} enabled mirror(s) in "${pairName}"?\n\n` +
        `This will sequentially trigger updates with rate limiting to avoid overwhelming GitLab.\n` +
        `This may take a few minutes for large mirror sets.`;

    if (!confirm(confirmMsg)) return;

    try {
        showMessage(`Starting batch sync for ${mirrorCount} mirrors...`, 'info');

        const result = await apiRequest(`/api/pairs/${pairId}/sync-mirrors`, {
            method: 'POST'
        });

        // Show detailed results
        const successMsg = [
            `Batch sync completed:`,
            `✓ ${result.succeeded} succeeded`,
            result.failed > 0 ? `✗ ${result.failed} failed` : null,
            result.skipped > 0 ? `⊘ ${result.skipped} skipped` : null,
            `⏱ ${result.duration_seconds}s (${result.operations_per_second} ops/sec)`
        ].filter(Boolean).join('\n');

        if (result.failed > 0) {
            console.error('Batch sync errors:', result.errors);
            showMessage(
                `${successMsg}\n\nCheck console for error details.`,
                'warning'
            );
        } else {
            showMessage(successMsg, 'success');
        }

        // Reload mirrors if we're viewing this pair
        if (state.selectedPair === pairId) {
            await loadMirrors();
        }
    } catch (error) {
        console.error('Failed to sync mirrors:', error);
        showMessage(`Failed to sync mirrors: ${error.message}`, 'error');
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

// Mirrors
async function loadMirrors(resetPage = false) {
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

        // Reset to page 1 if requested (e.g., when changing filters)
        if (resetPage) {
            state.mirrorsPagination.page = 1;
        }

        // Build query params with pagination and filtering
        const params = new URLSearchParams({
            instance_pair_id: state.selectedPair,
            page: state.mirrorsPagination.page,
            page_size: state.mirrorsPagination.pageSize,
            order_by: state.mirrorsPagination.orderBy,
            order_dir: state.mirrorsPagination.orderDir,
        });

        // Add optional filters
        if (state.mirrorsPagination.groupPath) {
            params.append('group_path', state.mirrorsPagination.groupPath);
        }

        const response = await apiRequest(`/api/mirrors?${params}`);

        // Update state with paginated data
        state.mirrors = response.mirrors;
        state.mirrorsPagination.total = response.total;
        state.mirrorsPagination.page = response.page;
        state.mirrorsPagination.pageSize = response.page_size;
        state.mirrorsPagination.totalPages = response.total_pages;

        // Render based on view mode
        if (state.mirrorsPagination.viewMode === 'tree') {
            renderMirrorsTree(response.mirrors);
        } else {
            renderMirrors(response.mirrors);
        }

        // Render pagination controls
        renderMirrorPagination();
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

        // Format update status with appropriate badge color
        const updateStatus = formatMirrorStatus(mirror);

        const dir = (mirror.effective_mirror_direction || '').toString().toLowerCase();
        const settingsCell = (() => {
            const pieces = [];
            if (dir) pieces.push(`<span class="badge badge-info">${escapeHtml(dir)}</span>`);
            pieces.push(`<span class="text-muted">overwrite:</span> ${fmtBool(mirror.effective_mirror_overwrite_diverged)}`);
            pieces.push(`<span class="text-muted">only_protected:</span> ${fmtBool(mirror.effective_only_mirror_protected_branches)}`);
            if (dir === 'pull') {
                pieces.push(`<span class="text-muted">trigger:</span> ${fmtBool(mirror.effective_mirror_trigger_builds)}`);
                pieces.push(`<span class="text-muted">regex:</span> ${fmtStr(mirror.effective_mirror_branch_regex)}`);
            } else if (dir === 'push') {
                pieces.push(`<span class="text-muted">trigger:</span> <span class="text-muted">n/a</span>`);
                pieces.push(`<span class="text-muted">regex:</span> <span class="text-muted">n/a</span>`);
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
                    <td class="cell-locked" data-sort="${escapeHtml(mirror.last_update_at || mirror.last_successful_update || '')}">
                        <div>
                            ${mirror.last_successful_update
                                ? `<span title="Last successful sync">${new Date(mirror.last_successful_update).toLocaleString()}</span>`
                                : '<span class="text-muted">Never synced</span>'}
                            ${mirror.last_update_at && mirror.last_update_at !== mirror.last_successful_update
                                ? `<br><small class="text-muted" title="Last attempt">(attempt: ${new Date(mirror.last_update_at).toLocaleString()})</small>`
                                : ''}
                        </div>
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

        // Token status badge
        const tokenStatusBadge = (() => {
            const status = mirror.token_status;
            if (!status || status === 'none') {
                return '<span class="badge badge-secondary">No token</span>';
            } else if (status === 'active') {
                return '<span class="badge badge-success">Active</span>';
            } else if (status === 'expiring_soon') {
                const expiresAt = mirror.mirror_token_expires_at
                    ? new Date(mirror.mirror_token_expires_at).toLocaleDateString()
                    : 'soon';
                return `<span class="badge badge-warning" title="Expires ${expiresAt}">Expiring</span>`;
            } else if (status === 'expired') {
                return '<span class="badge badge-danger">Expired</span>';
            }
            return '<span class="badge badge-secondary">Unknown</span>';
        })();

        const verifyBadge = getVerificationBadgeHtml(mirror.id);

        return `
            <tr data-mirror-id="${mirror.id}">
                <td>${formatProjectPath(mirror.source_project_path)}</td>
                <td>${formatProjectPath(mirror.target_project_path)}</td>
                <td>${settingsCell}</td>
                <td class="mirror-status">${statusBadge}</td>
                <td>${updateStatus}</td>
                <td data-sort="${escapeHtml(mirror.last_update_at || mirror.last_successful_update || '')}">
                    <div>
                        ${mirror.last_successful_update
                            ? `<span title="Last successful sync">${new Date(mirror.last_successful_update).toLocaleString()}</span>`
                            : '<span class="text-muted">Never synced</span>'}
                        ${mirror.last_update_at && mirror.last_update_at !== mirror.last_successful_update
                            ? `<br><small class="text-muted" title="Last attempt">(attempt: ${new Date(mirror.last_update_at).toLocaleString()})</small>`
                            : ''}
                    </div>
                </td>
                <td>${tokenStatusBadge}</td>
                <td>${verifyBadge}</td>
                <td>
                    <div class="table-actions">
                        <button class="btn btn-secondary btn-small" onclick="beginMirrorEdit(${mirror.id})">Edit</button>
                        <button class="btn btn-success btn-small" onclick="triggerMirrorUpdate(${mirror.id})" title="Trigger an immediate mirror sync">Sync</button>
                        <button class="btn btn-secondary btn-small" data-refresh-btn="${mirror.id}" onclick="refreshMirrorStatus(${mirror.id})" title="Refresh status from GitLab">Status</button>
                        <button class="btn btn-primary btn-small" onclick="showIssueMirrorConfig(${mirror.id})" title="Configure issue mirroring">Issue Sync</button>
                        <button class="btn btn-info btn-small" data-verify-btn="${mirror.id}" onclick="verifyMirror(${mirror.id})" title="Check if mirror exists and settings match GitLab">Verify</button>
                        <button class="btn btn-warning btn-small" onclick="rotateMirrorToken(${mirror.id})" title="Rotate access token">Rotate Token</button>
                        <button class="btn btn-danger btn-small" onclick="deleteMirror(${mirror.id})">Delete</button>
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}

// Rotate mirror token
async function rotateMirrorToken(mirrorId) {
    if (!confirm('This will create a new access token for this mirror and revoke the old one. Continue?')) {
        return;
    }

    try {
        await apiRequest(`/api/mirrors/${mirrorId}/rotate-token`, { method: 'POST' });
        showMessage('Token rotated successfully', 'success');
        await loadMirrors();
    } catch (error) {
        console.error('Failed to rotate token:', error);
        showMessage(`Failed to rotate token: ${error.message || 'Unknown error'}`, 'error');
    }
}

// Refresh mirror status from GitLab
async function refreshMirrorStatus(mirrorId) {
    const btn = document.querySelector(`[data-refresh-btn="${mirrorId}"]`);
    if (btn) {
        btn.disabled = true;
        btn.textContent = '...';
    }

    try {
        const result = await apiRequest(`/api/mirrors/${mirrorId}/refresh-status`, { method: 'POST' });

        if (result.success) {
            showMessage(`Mirror #${mirrorId}: Status refreshed - ${result.last_update_status || 'N/A'}`, 'success');
        } else {
            showMessage(`Mirror #${mirrorId}: ${result.error || 'Failed to refresh status'}`, 'warning');
        }

        // Reload mirrors to show updated status
        await loadMirrors();
    } catch (error) {
        console.error('Failed to refresh status:', error);
        showMessage(`Failed to refresh status: ${error.message || 'Unknown error'}`, 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = 'Status';
        }
    }
}

// Refresh status for all visible mirrors
async function refreshAllMirrorStatus() {
    const mirrorIds = state.mirrors.map(m => m.id);
    if (mirrorIds.length === 0) {
        showMessage('No mirrors to refresh', 'info');
        return;
    }

    showMessage(`Refreshing status for ${mirrorIds.length} mirrors...`, 'info');

    try {
        const results = await apiRequest('/api/mirrors/refresh-status', {
            method: 'POST',
            body: JSON.stringify({ mirror_ids: mirrorIds }),
        });

        const successCount = results.filter(r => r.success).length;
        const failedCount = results.filter(r => !r.success).length;

        if (failedCount === 0) {
            showMessage(`Status refreshed for ${successCount} mirrors`, 'success');
        } else {
            showMessage(`Status refreshed: ${successCount} success, ${failedCount} failed`, 'warning');
        }

        // Reload mirrors to show updated status
        await loadMirrors();
    } catch (error) {
        console.error('Failed to refresh all statuses:', error);
        showMessage(`Failed to refresh status: ${error.message || 'Unknown error'}`, 'error');
    }
}

// Verification status cache (mirror_id -> verification result)
const verificationCache = new Map();

// Verify a single mirror for orphan/drift status
async function verifyMirror(mirrorId, showMessages = true) {
    const btn = document.querySelector(`[data-verify-btn="${mirrorId}"]`);
    if (btn) {
        btn.disabled = true;
        btn.textContent = '...';
    }

    try {
        const result = await apiRequest(`/api/mirrors/${mirrorId}/verify`);
        verificationCache.set(mirrorId, result);

        // Update the verification badge in the UI
        updateVerificationBadge(mirrorId, result);

        if (showMessages) {
            if (result.status === 'healthy') {
                showMessage(`Mirror #${mirrorId}: Healthy ✓`, 'success');
            } else if (result.status === 'orphan') {
                showMessage(`Mirror #${mirrorId}: ORPHAN - Mirror was deleted from GitLab`, 'error');
            } else if (result.status === 'drift') {
                const fields = result.drift.map(d => d.field).join(', ');
                showMessage(`Mirror #${mirrorId}: DRIFT detected in: ${fields}`, 'warning');
            } else if (result.status === 'not_created') {
                showMessage(`Mirror #${mirrorId}: Not yet created on GitLab`, 'info');
            } else if (result.status === 'error') {
                showMessage(`Mirror #${mirrorId}: Error - ${result.error}`, 'error');
            }
        }

        return result;
    } catch (error) {
        console.error('Failed to verify mirror:', error);
        if (showMessages) {
            showMessage(`Failed to verify mirror #${mirrorId}: ${error.message}`, 'error');
        }
        return null;
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = 'Verify';
        }
    }
}

// Verify all visible mirrors
async function verifyAllMirrors() {
    const mirrorIds = state.mirrors.map(m => m.id);
    if (mirrorIds.length === 0) {
        showMessage('No mirrors to verify', 'info');
        return;
    }

    const btn = document.getElementById('verify-all-btn');
    if (btn) {
        btn.disabled = true;
        btn.textContent = 'Verifying...';
    }

    try {
        const results = await apiRequest('/api/mirrors/verify', {
            method: 'POST',
            body: JSON.stringify({ mirror_ids: mirrorIds }),
        });

        // Update cache and UI for each result
        let healthy = 0, orphan = 0, drift = 0, errors = 0, notCreated = 0;

        for (const result of results) {
            verificationCache.set(result.mirror_id, result);
            updateVerificationBadge(result.mirror_id, result);

            switch (result.status) {
                case 'healthy': healthy++; break;
                case 'orphan': orphan++; break;
                case 'drift': drift++; break;
                case 'not_created': notCreated++; break;
                case 'error': errors++; break;
            }
        }

        // Show summary
        const parts = [];
        if (healthy > 0) parts.push(`${healthy} healthy`);
        if (orphan > 0) parts.push(`${orphan} orphan`);
        if (drift > 0) parts.push(`${drift} drifted`);
        if (notCreated > 0) parts.push(`${notCreated} not created`);
        if (errors > 0) parts.push(`${errors} errors`);

        const msgType = (orphan > 0 || errors > 0) ? 'error' : (drift > 0 ? 'warning' : 'success');
        showMessage(`Verification complete: ${parts.join(', ')}`, msgType);

    } catch (error) {
        console.error('Failed to verify mirrors:', error);
        showMessage(`Failed to verify mirrors: ${error.message}`, 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = 'Verify All';
        }
    }
}

// Update the verification badge for a mirror row
function updateVerificationBadge(mirrorId, result) {
    const badge = document.querySelector(`[data-verify-badge="${mirrorId}"]`);
    if (!badge) return;

    badge.className = 'badge';
    badge.title = '';

    switch (result.status) {
        case 'healthy':
            badge.className = 'badge badge-success';
            badge.textContent = '✓';
            badge.title = 'Mirror verified: healthy';
            break;
        case 'orphan':
            badge.className = 'badge badge-danger';
            badge.textContent = 'Orphan';
            badge.title = 'Mirror was deleted from GitLab externally';
            break;
        case 'drift':
            badge.className = 'badge badge-warning';
            const fields = result.drift.map(d => `${d.field}: expected ${d.expected}, got ${d.actual}`).join('\n');
            badge.textContent = 'Drift';
            badge.title = `Settings mismatch:\n${fields}`;
            break;
        case 'not_created':
            badge.className = 'badge badge-secondary';
            badge.textContent = 'N/C';
            badge.title = 'Mirror not yet created on GitLab';
            break;
        case 'error':
            badge.className = 'badge badge-danger';
            badge.textContent = 'Err';
            badge.title = result.error || 'Verification error';
            break;
        default:
            badge.textContent = '?';
            badge.title = 'Unknown status';
    }
}

// Get cached verification status badge HTML
function getVerificationBadgeHtml(mirrorId) {
    const cached = verificationCache.get(mirrorId);
    if (!cached) {
        return `<span data-verify-badge="${mirrorId}" class="badge badge-secondary" title="Not verified">-</span>`;
    }

    let badgeClass = 'badge-secondary';
    let text = '-';
    let title = 'Not verified';

    switch (cached.status) {
        case 'healthy':
            badgeClass = 'badge-success';
            text = '✓';
            title = 'Mirror verified: healthy';
            break;
        case 'orphan':
            badgeClass = 'badge-danger';
            text = 'Orphan';
            title = 'Mirror was deleted from GitLab externally';
            break;
        case 'drift':
            badgeClass = 'badge-warning';
            text = 'Drift';
            const fields = cached.drift.map(d => `${d.field}: expected ${d.expected}, got ${d.actual}`).join('\n');
            title = `Settings mismatch:\n${fields}`;
            break;
        case 'not_created':
            badgeClass = 'badge-secondary';
            text = 'N/C';
            title = 'Mirror not yet created on GitLab';
            break;
        case 'error':
            badgeClass = 'badge-danger';
            text = 'Err';
            title = cached.error || 'Verification error';
            break;
    }

    return `<span data-verify-badge="${mirrorId}" class="badge ${badgeClass}" title="${escapeHtml(title)}">${text}</span>`;
}

// Pagination controls
function renderMirrorPagination() {
    const container = document.getElementById('mirror-pagination');
    if (!container) return;

    const { page, totalPages, total, pageSize } = state.mirrorsPagination;

    if (totalPages <= 1) {
        container.innerHTML = '';
        return;
    }

    const startItem = (page - 1) * pageSize + 1;
    const endItem = Math.min(page * pageSize, total);

    // Generate page number buttons (show current, prev 2, next 2, first, last)
    const pageButtons = [];

    // Always show first page
    if (page > 3) {
        pageButtons.push(`<button class="btn btn-secondary btn-small" onclick="changeMirrorPage(1)">1</button>`);
        if (page > 4) {
            pageButtons.push(`<span class="pagination-ellipsis">...</span>`);
        }
    }

    // Show pages around current page
    for (let i = Math.max(1, page - 2); i <= Math.min(totalPages, page + 2); i++) {
        if (i === page) {
            pageButtons.push(`<button class="btn btn-primary btn-small" disabled>${i}</button>`);
        } else {
            pageButtons.push(`<button class="btn btn-secondary btn-small" onclick="changeMirrorPage(${i})">${i}</button>`);
        }
    }

    // Always show last page
    if (page < totalPages - 2) {
        if (page < totalPages - 3) {
            pageButtons.push(`<span class="pagination-ellipsis">...</span>`);
        }
        pageButtons.push(`<button class="btn btn-secondary btn-small" onclick="changeMirrorPage(${totalPages})">${totalPages}</button>`);
    }

    container.innerHTML = `
        <div class="pagination-controls">
            <div class="pagination-info">
                Showing ${startItem}-${endItem} of ${total} mirrors
            </div>
            <div class="pagination-buttons">
                <button class="btn btn-secondary btn-small" onclick="changeMirrorPage(${page - 1})" ${page === 1 ? 'disabled' : ''}>
                    Previous
                </button>
                ${pageButtons.join('')}
                <button class="btn btn-secondary btn-small" onclick="changeMirrorPage(${page + 1})" ${page === totalPages ? 'disabled' : ''}>
                    Next
                </button>
            </div>
            <div class="pagination-size">
                <select id="mirror-page-size" onchange="changeMirrorPageSize(this.value)" class="table-select">
                    <option value="25" ${pageSize === 25 ? 'selected' : ''}>25 per page</option>
                    <option value="50" ${pageSize === 50 ? 'selected' : ''}>50 per page</option>
                    <option value="100" ${pageSize === 100 ? 'selected' : ''}>100 per page</option>
                    <option value="200" ${pageSize === 200 ? 'selected' : ''}>200 per page</option>
                </select>
            </div>
        </div>
    `;
}

async function changeMirrorPage(newPage) {
    if (newPage < 1 || newPage > state.mirrorsPagination.totalPages) return;
    state.mirrorsPagination.page = newPage;
    await loadMirrors();
}

async function changeMirrorPageSize(newSize) {
    state.mirrorsPagination.pageSize = parseInt(newSize);
    state.mirrorsPagination.page = 1; // Reset to first page
    await loadMirrors();
}

// Format project path with smart truncation and breadcrumbs
function formatProjectPath(path, options = {}) {
    if (!path) return '<span class="text-muted">n/a</span>';
    const { maxParts = 3, showTooltip = true } = options;
    const parts = String(path).split('/');

    if (parts.length <= maxParts) {
        // Short path, show it all
        return showTooltip
            ? `<span title="${escapeHtml(path)}">${escapeHtml(path)}</span>`
            : escapeHtml(path);
    }

    // Long path, show breadcrumbs with truncation
    const displayParts = [
        '...', // Indicate truncation
        ...parts.slice(-maxParts) // Show last N parts
    ];

    const displayPath = displayParts.join(' / ');

    return `<span class="project-path-breadcrumb" title="${escapeHtml(path)}">${escapeHtml(displayPath)}</span>`;
}

// Tree view rendering
function renderMirrorsTree(mirrors) {
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

    // Build tree structure from paths
    const tree = buildMirrorTree(mirrors);

    // Render tree
    tbody.innerHTML = renderTreeNode(tree, 0);
}

function buildMirrorTree(mirrors) {
    const tree = {};

    mirrors.forEach(mirror => {
        // Use source path for grouping
        const parts = mirror.source_project_path.split('/');
        let current = tree;

        // Build tree structure
        parts.forEach((part, index) => {
            if (!current[part]) {
                current[part] = {
                    name: part,
                    level: index,
                    children: {},
                    mirrors: []
                };
            }

            // If this is the last part (project name), add the mirror
            if (index === parts.length - 1) {
                current[part].mirrors.push(mirror);
            }

            current = current[part].children;
        });
    });

    return tree;
}

function renderTreeNode(node, level, parentPath = '') {
    let html = '';

    for (const key in node) {
        const item = node[key];
        const currentPath = parentPath ? `${parentPath}/${item.name}` : item.name;
        const hasChildren = Object.keys(item.children).length > 0;
        const hasMirrors = item.mirrors.length > 0;
        const indent = level * 20;

        if (hasChildren) {
            // Group node (collapsible)
            html += `
                <tr class="tree-group-row" data-level="${level}">
                    <td colspan="8" style="padding-left: ${indent}px;">
                        <div class="tree-group-header" onclick="toggleTreeGroup(this)">
                            <span class="tree-toggle">▼</span>
                            <span class="tree-group-name">${escapeHtml(item.name)}/</span>
                            <span class="tree-group-count">(${countMirrorsInTree(item)} mirrors)</span>
                        </div>
                    </td>
                </tr>
                <tbody class="tree-group-children">
                    ${renderTreeNode(item.children, level + 1, currentPath)}
                </tbody>
            `;
        }

        if (hasMirrors) {
            // Render mirrors for this node
            item.mirrors.forEach(mirror => {
                html += `
                    <tr class="tree-mirror-row" data-mirror-id="${mirror.id}" data-level="${level}" style="padding-left: ${indent}px;">
                        <td style="padding-left: ${indent + 20}px;">${escapeHtml(mirror.source_project_path)}</td>
                        <td>${formatProjectPath(mirror.target_project_path)}</td>
                        <td><!-- Settings --></td>
                        <td class="mirror-status">${mirror.enabled ? '<span class="badge badge-success">Enabled</span>' : '<span class="badge badge-warning">Disabled</span>'}</td>
                        <td>${formatMirrorStatus(mirror)}</td>
                        <td>${formatMirrorSyncTime(mirror)}</td>
                        <td><!-- Token --></td>
                        <td>
                            <div class="table-actions">
                                <button class="btn btn-secondary btn-small" onclick="beginMirrorEdit(${mirror.id})">Edit</button>
                                <button class="btn btn-success btn-small" onclick="triggerMirrorUpdate(${mirror.id})">Sync</button>
                                <button class="btn btn-danger btn-small" onclick="deleteMirror(${mirror.id})">Delete</button>
                            </div>
                        </td>
                    </tr>
                `;
            });
        }
    }

    return html;
}

function countMirrorsInTree(node) {
    let count = node.mirrors.length;
    for (const key in node.children) {
        count += countMirrorsInTree(node.children[key]);
    }
    return count;
}

function toggleTreeGroup(element) {
    const toggle = element.querySelector('.tree-toggle');
    const row = element.closest('tr');
    const childrenBody = row.nextElementSibling;

    if (childrenBody && childrenBody.classList.contains('tree-group-children')) {
        const isCollapsed = childrenBody.style.display === 'none';
        childrenBody.style.display = isCollapsed ? '' : 'none';
        toggle.textContent = isCollapsed ? '▼' : '▶';
    }
}

// View mode toggle
async function toggleMirrorViewMode() {
    const btn = document.querySelector('[onclick="toggleMirrorViewMode()"]');
    if (!btn) return;

    // Toggle view mode
    const isTree = state.mirrorsPagination.viewMode === 'tree';
    state.mirrorsPagination.viewMode = isTree ? 'flat' : 'tree';

    // Update button content
    if (state.mirrorsPagination.viewMode === 'tree') {
        btn.innerHTML = '<span id="view-mode-icon">📋</span> Flat View';
    } else {
        btn.innerHTML = '<span id="view-mode-icon">🌳</span> Tree View';
    }

    // Re-render with new view mode
    if (state.mirrorsPagination.viewMode === 'tree') {
        renderMirrorsTree(state.mirrors);
    } else {
        renderMirrors(state.mirrors);
    }
}

// Group path filtering
async function filterByGroupPath(groupPath) {
    state.mirrorsPagination.groupPath = groupPath.trim();
    await loadMirrors(true); // Reset to page 1
}

// Sort controls
async function changeMirrorSort(orderBy, orderDir) {
    state.mirrorsPagination.orderBy = orderBy;
    state.mirrorsPagination.orderDir = orderDir;
    await loadMirrors(true); // Reset to page 1
}

function beginMirrorEdit(id) {
    state.editing.mirrorId = id;
    state.editing.instanceId = null;
    state.editing.pairId = null;
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
    const dir = (mirror.effective_mirror_direction || '').toString().toLowerCase();
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
    };

    if (!isPush) {
        const regexMode = (regexModeEl?.value || '__inherit__').toString();
        const regexRaw = (regexEl?.value || '').toString().trim();
        payload.mirror_branch_regex = regexMode === '__override__' ? (regexRaw || null) : null;
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

        // Clear the autocomplete inputs and hidden values
        clearProjectAutocomplete('source');
        clearProjectAutocomplete('target');
    } catch (error) {
        console.error('Failed to load projects:', error);
    }
}

// Project autocomplete state
const autocompleteState = {
    source: { highlightedIndex: -1, projects: [] },
    target: { highlightedIndex: -1, projects: [] }
};

function clearProjectAutocomplete(side) {
    const input = document.getElementById(`mirror-${side}-project-input`);
    const hidden = document.getElementById(`mirror-${side}-project`);
    const dropdown = document.getElementById(`${side}-project-dropdown`);

    if (input) {
        input.value = '';
        input.classList.remove('has-selection');
    }
    if (hidden) hidden.value = '';
    if (dropdown) {
        dropdown.innerHTML = '';
        dropdown.classList.remove('active');
    }
    autocompleteState[side].highlightedIndex = -1;
    autocompleteState[side].projects = [];
}

function initProjectAutocomplete(side) {
    const input = document.getElementById(`mirror-${side}-project-input`);
    const hidden = document.getElementById(`mirror-${side}-project`);
    const dropdown = document.getElementById(`${side}-project-dropdown`);

    if (!input || !hidden || !dropdown) return;

    // Debounced search on input
    input.addEventListener('input', debounce(() => searchProjectsForMirror(side), 250));

    // Keyboard navigation
    input.addEventListener('keydown', (e) => {
        const items = dropdown.querySelectorAll('.autocomplete-item');
        const state = autocompleteState[side];

        if (e.key === 'ArrowDown') {
            e.preventDefault();
            if (!dropdown.classList.contains('active') && items.length > 0) {
                dropdown.classList.add('active');
            }
            state.highlightedIndex = Math.min(state.highlightedIndex + 1, items.length - 1);
            updateHighlight(dropdown, state.highlightedIndex);
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            state.highlightedIndex = Math.max(state.highlightedIndex - 1, 0);
            updateHighlight(dropdown, state.highlightedIndex);
        } else if (e.key === 'Enter') {
            e.preventDefault();
            if (state.highlightedIndex >= 0 && items[state.highlightedIndex]) {
                selectProject(side, state.projects[state.highlightedIndex]);
            }
        } else if (e.key === 'Escape') {
            dropdown.classList.remove('active');
            state.highlightedIndex = -1;
        }
    });

    // Show dropdown on focus if there are results
    input.addEventListener('focus', () => {
        if (autocompleteState[side].projects.length > 0 && !hidden.value) {
            dropdown.classList.add('active');
        }
    });

    // Close dropdown when clicking outside
    document.addEventListener('click', (e) => {
        const wrapper = document.getElementById(`${side}-project-autocomplete`);
        if (wrapper && !wrapper.contains(e.target)) {
            dropdown.classList.remove('active');
        }
    });
}

function updateHighlight(dropdown, index) {
    const items = dropdown.querySelectorAll('.autocomplete-item');
    items.forEach((item, i) => {
        if (i === index) {
            item.classList.add('highlighted');
            item.scrollIntoView({ block: 'nearest' });
        } else {
            item.classList.remove('highlighted');
        }
    });
}

function selectProject(side, project) {
    const input = document.getElementById(`mirror-${side}-project-input`);
    const hidden = document.getElementById(`mirror-${side}-project`);
    const dropdown = document.getElementById(`${side}-project-dropdown`);

    if (!project || !input || !hidden) return;

    const fullPath = project.path_with_namespace || project.name || '';
    input.value = fullPath;
    input.classList.add('has-selection');
    hidden.value = project.id;
    dropdown.classList.remove('active');
    autocompleteState[side].highlightedIndex = -1;

    // Check for existing mirrors on the selected project
    checkProjectMirrors(side);
}

async function searchProjectsForMirror(side) {
    const instanceId = side === 'source' ? state.mirrorProjectInstances.source : state.mirrorProjectInstances.target;
    const input = document.getElementById(`mirror-${side}-project-input`);
    const hidden = document.getElementById(`mirror-${side}-project`);
    const dropdown = document.getElementById(`${side}-project-dropdown`);

    if (!input || !dropdown) return;

    // Clear selection when user types (they're searching for something new)
    if (hidden) {
        hidden.value = '';
        input.classList.remove('has-selection');
    }

    if (!instanceId) {
        dropdown.innerHTML = '<div class="autocomplete-hint">Select an instance pair first</div>';
        dropdown.classList.add('active');
        autocompleteState[side].projects = [];
        return;
    }

    const q = (input.value || '').toString().trim();
    if (q.length < 2) {
        dropdown.innerHTML = '<div class="autocomplete-hint">Type at least 2 characters to search</div>';
        dropdown.classList.add('active');
        autocompleteState[side].projects = [];
        return;
    }

    // Show loading state
    dropdown.innerHTML = '<div class="autocomplete-loading"><div class="spinner"></div> Searching...</div>';
    dropdown.classList.add('active');

    const perPage = 50;
    try {
        const res = await apiRequest(
            `/api/instances/${instanceId}/projects?search=${encodeURIComponent(q)}&per_page=${perPage}&page=1&get_all=false`
        );
        const projects = res?.projects || [];
        autocompleteState[side].projects = projects;
        autocompleteState[side].highlightedIndex = -1;

        if (projects.length === 0) {
            dropdown.innerHTML = '<div class="autocomplete-empty">No projects found</div>';
            return;
        }

        const items = projects.map((p, i) => {
            const fullPath = (p.path_with_namespace || p.name || '').toString();
            return `
                <div class="autocomplete-item" data-index="${i}">
                    <span class="autocomplete-item-path">${escapeHtml(fullPath)}</span>
                    <span class="autocomplete-item-id">ID: ${p.id}</span>
                </div>
            `;
        }).join('');

        const moreHint = projects.length >= perPage
            ? '<div class="autocomplete-more-hint">Showing first 50 matches — refine search to narrow</div>'
            : '';

        dropdown.innerHTML = items + moreHint;

        // Add click handlers to items
        dropdown.querySelectorAll('.autocomplete-item').forEach((item) => {
            item.addEventListener('click', () => {
                const index = parseInt(item.dataset.index);
                selectProject(side, projects[index]);
            });
        });

    } catch (error) {
        console.error('Failed to search projects:', error);
        dropdown.innerHTML = '<div class="autocomplete-empty">Search failed</div>';
    }
}

function resetMirrorOverrides() {
    // Reset all select overrides to "inherit from pair"
    const selectIds = ['mirror-overwrite', 'mirror-trigger', 'mirror-only-protected'];
    selectIds.forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.value = '__inherit__';
    });

    const regex = document.getElementById('mirror-branch-regex');
    if (regex) regex.value = '';

    // Direction dropdown removed - direction comes from pair only

    const enabled = document.getElementById('mirror-enabled');
    if (enabled) enabled.checked = true;

    // Update the inherit option texts to show pair defaults
    updateMirrorFormPairDefaults();

    // Clear any project mirror badges
    clearProjectMirrorBadges();
}

// Clear project mirror badges
function clearProjectMirrorBadges() {
    const sourceBadge = document.getElementById('source-project-mirrors-badge');
    const targetBadge = document.getElementById('target-project-mirrors-badge');
    if (sourceBadge) sourceBadge.innerHTML = '';
    if (targetBadge) targetBadge.innerHTML = '';
}

// Check for existing mirrors on a selected project
async function checkProjectMirrors(side) {
    const instanceId = side === 'source' ? state.mirrorProjectInstances.source : state.mirrorProjectInstances.target;
    const selectId = side === 'source' ? 'mirror-source-project' : 'mirror-target-project';
    const badgeId = side === 'source' ? 'source-project-mirrors-badge' : 'target-project-mirrors-badge';

    const select = document.getElementById(selectId);
    const badge = document.getElementById(badgeId);

    if (!badge) return;

    const projectId = select?.value;
    if (!projectId || !instanceId) {
        badge.innerHTML = '';
        return;
    }

    // Show loading state
    badge.innerHTML = '<span class="badge badge-secondary">Checking...</span>';

    try {
        const result = await apiRequest(`/api/instances/${instanceId}/projects/${projectId}/mirrors`);

        if (result.total_count === 0) {
            badge.innerHTML = '';
            return;
        }

        // Build badge content
        const parts = [];
        if (result.push_count > 0) {
            parts.push(`${result.push_count} push`);
        }
        if (result.pull_count > 0) {
            parts.push(`${result.pull_count} pull`);
        }

        const badgeText = parts.length > 0 ? parts.join(', ') : `${result.total_count} mirror(s)`;
        const tooltip = `This project has ${result.total_count} existing mirror(s) on GitLab`;

        badge.innerHTML = `<span class="badge badge-warning" title="${escapeHtml(tooltip)}">⚠ ${escapeHtml(badgeText)}</span>`;
    } catch (error) {
        console.error('Failed to check project mirrors:', error);
        badge.innerHTML = '';
    }
}

function updateMirrorFormPairDefaults() {
    const pair = state.pairs.find(p => p.id === state.selectedPair);
    if (!pair) return;

    const fmtBool = (v) => v ? 'yes' : 'no';

    // Update each select's "inherit" option to show the pair's default
    const overwriteEl = document.getElementById('mirror-overwrite');
    if (overwriteEl) {
        const inheritOpt = overwriteEl.querySelector('option[value="__inherit__"]');
        if (inheritOpt) inheritOpt.textContent = `Inherit from pair (${fmtBool(pair.mirror_overwrite_diverged)})`;
    }

    const onlyProtectedEl = document.getElementById('mirror-only-protected');
    if (onlyProtectedEl) {
        const inheritOpt = onlyProtectedEl.querySelector('option[value="__inherit__"]');
        if (inheritOpt) inheritOpt.textContent = `Inherit from pair (${fmtBool(pair.only_mirror_protected_branches)})`;
    }

    const triggerEl = document.getElementById('mirror-trigger');
    if (triggerEl) {
        const inheritOpt = triggerEl.querySelector('option[value="__inherit__"]');
        if (inheritOpt) inheritOpt.textContent = `Inherit from pair (${fmtBool(pair.mirror_trigger_builds)})`;
    }

    // Update placeholders for text inputs to show pair defaults
    const regexEl = document.getElementById('mirror-branch-regex');
    if (regexEl) {
        const pairRegex = pair.mirror_branch_regex;
        regexEl.placeholder = pairRegex ? `Inherit: ${pairRegex}` : 'Inherit from pair (none)';
    }
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
}

async function createMirror() {
    if (!state.selectedPair) {
        showMessage('Please select an instance pair first', 'error');
        return;
    }

    const form = document.getElementById('mirror-form');
    const formData = new FormData(form);

    const sourceHidden = document.getElementById('mirror-source-project');
    const targetHidden = document.getElementById('mirror-target-project');
    const sourceInput = document.getElementById('mirror-source-project-input');
    const targetInput = document.getElementById('mirror-target-project-input');

    if (!sourceHidden?.value || !targetHidden?.value) {
        showMessage('Please select both source and target projects', 'error');
        return;
    }

    const data = {
        instance_pair_id: state.selectedPair,
        source_project_id: parseInt(sourceHidden.value),
        source_project_path: sourceInput?.value || '',
        target_project_id: parseInt(targetHidden.value),
        target_project_path: targetInput?.value || '',
        enabled: formData.get('enabled') === 'on'
    };

    // Direction comes from pair only (no per-mirror override)
    const pair = state.pairs.find(p => p.id === state.selectedPair);
    const effectiveDirection = (pair?.mirror_direction || '').toString().toLowerCase();
    const isPush = effectiveDirection === 'push';

    // Handle tri-state selects: "__inherit__" means omit (use pair default), otherwise convert to boolean
    const overwriteEl = document.getElementById('mirror-overwrite');
    if (overwriteEl && overwriteEl.value !== '__inherit__') {
        data.mirror_overwrite_diverged = overwriteEl.value === 'true';
    }
    const triggerEl = document.getElementById('mirror-trigger');
    if (triggerEl && triggerEl.value !== '__inherit__' && !isPush) {
        data.mirror_trigger_builds = triggerEl.value === 'true';
    }
    const onlyProtectedEl = document.getElementById('mirror-only-protected');
    if (onlyProtectedEl && onlyProtectedEl.value !== '__inherit__') {
        data.only_mirror_protected_branches = onlyProtectedEl.value === 'true';
    }

    const regexRaw = (formData.get('mirror_branch_regex') || '').toString().trim();
    if (regexRaw && !isPush) {
        data.mirror_branch_regex = regexRaw;
    }

    // Preflight: check for existing GitLab remote mirrors before attempting to create.
    // This is especially important for pull mirrors, where GitLab effectively supports only one.
    // Direction comes from pair, so no need to pass it here.
    try {
        const preflightPayload = {
            instance_pair_id: data.instance_pair_id,
            source_project_id: data.source_project_id,
            source_project_path: data.source_project_path,
            target_project_id: data.target_project_id,
            target_project_path: data.target_project_path,
        };

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
        await loadMirrors(true);
    } catch (error) {
        console.error('Failed to create mirror:', error);
        // Error message already shown via apiRequest
        if (error instanceof APIError && error.type === 'validation') {
            // Keep form filled for validation errors
            return;
        }
        // For non-validation errors (server error, network error), the mirror
        // may have been created on the backend. Refresh the list to show current state.
        try {
            await loadMirrors(true);
        } catch (e) {
            // Best-effort refresh
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

        // Poll status with increasing delays until sync completes or max attempts reached.
        // Delays: 5s, 10s, 15s, 30s, 30s (total ~90s coverage)
        pollMirrorSyncStatus(id);
    } catch (error) {
        console.error('Failed to trigger mirror update:', error);
    }
}

function pollMirrorSyncStatus(mirrorId, attempt = 0) {
    const delays = [5000, 10000, 15000, 30000, 30000];
    if (attempt >= delays.length) return;

    setTimeout(async () => {
        try {
            const result = await apiRequest(`/api/mirrors/${mirrorId}/refresh-status`, { method: 'POST' });
            await loadMirrors();
            // If still syncing, keep polling
            if (result.last_update_status === 'syncing' || result.last_update_status === 'started') {
                pollMirrorSyncStatus(mirrorId, attempt + 1);
            }
        } catch (e) {
            // Retry on error - the sync may still be running
            pollMirrorSyncStatus(mirrorId, attempt + 1);
        }
    }, delays[attempt]);
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
        const exportHeaders = {};
        if (typeof authState !== 'undefined' && authState.token) {
            exportHeaders['Authorization'] = `Bearer ${authState.token}`;
        }
        const response = await fetch(`/api/export/pair/${state.selectedPair}`, { headers: exportHeaders });
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

            // Build detailed message
            let message = `Import complete: ${result.imported} imported, ${result.skipped} skipped`;

            // Add errors if any
            if (result.errors && result.errors.length > 0) {
                message += `\n\nErrors (${result.errors.length}):\n` + result.errors.map(e => `  • ${e}`).join('\n');
            }

            // Add skipped details if any
            if (result.skipped_details && result.skipped_details.length > 0) {
                message += `\n\nSkipped (${result.skipped_details.length}):\n` + result.skipped_details.map(s => `  • ${s}`).join('\n');
            }

            // Show as error if there were errors, warning if only skips, success if all imported
            const messageType = result.errors && result.errors.length > 0 ? 'error' :
                                result.skipped > 0 ? 'warning' : 'success';

            showMessage(message, messageType);
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

// Load About page information
// ----------------------------
// Backup & Restore Functions
// ----------------------------

async function loadBackupStats() {
    try {
        const data = await apiRequest('/api/backup/stats');

        const bkInstEl = document.getElementById('backup-stat-instances');
        const bkPairsEl = document.getElementById('backup-stat-pairs');
        const bkMirrorsEl = document.getElementById('backup-stat-mirrors');
        const bkSizeEl = document.getElementById('backup-stat-size');
        if (bkInstEl) bkInstEl.textContent = data.instances || '0';
        if (bkPairsEl) bkPairsEl.textContent = data.pairs || '0';
        if (bkMirrorsEl) bkMirrorsEl.textContent = data.mirrors || '0';
        if (bkSizeEl) bkSizeEl.textContent = `${data.database_size_mb || '0'} MB`;
    } catch (error) {
        console.error('Failed to load backup statistics:', error);
        showMessage('Failed to load backup statistics', 'error');
    }
}

async function createBackup() {
    const btn = document.getElementById('create-backup-btn');
    const btnText = document.getElementById('create-backup-text');
    const btnSpinner = document.getElementById('create-backup-spinner');

    try {
        // Show loading state
        btn.disabled = true;
        btnText.style.display = 'none';
        btnSpinner.style.display = 'inline-block';

        const headers = {};
        if (typeof authState !== 'undefined' && authState.token) {
            headers['Authorization'] = `Bearer ${authState.token}`;
        }
        const response = await fetch('/api/backup/create', { headers });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to create backup');
        }

        // Get filename from Content-Disposition header
        const contentDisposition = response.headers.get('Content-Disposition');
        let filename = 'mirror-maestro-backup.tar.gz';
        if (contentDisposition) {
            const filenameMatch = contentDisposition.match(/filename="?(.+)"?/);
            if (filenameMatch) {
                filename = filenameMatch[1].replace(/"/g, '');
            }
        }

        // Download the file
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);

        showMessage('Backup created and downloaded successfully', 'success');
    } catch (error) {
        console.error('Backup creation failed:', error);
        showMessage(`Failed to create backup: ${error.message}`, 'error');
    } finally {
        // Reset button state
        btn.disabled = false;
        btnText.style.display = 'inline';
        btnSpinner.style.display = 'none';
    }
}

async function restoreBackup() {
    const fileInput = document.getElementById('restore-file-input');
    const file = fileInput?.files?.[0];

    if (!file) {
        showMessage('Please select a backup file first', 'error');
        return;
    }

    // Confirm with user
    const confirmed = confirm(
        '⚠️  WARNING: This will REPLACE all current data including:\n\n' +
        '• All GitLab instances\n' +
        '• All instance pairs\n' +
        '• All mirrors\n' +
        '• The encryption key\n\n' +
        'This action cannot be undone. Are you sure you want to continue?'
    );

    if (!confirmed) {
        return;
    }

    const btn = document.getElementById('restore-backup-btn');
    const btnText = document.getElementById('restore-backup-text');
    const btnSpinner = document.getElementById('restore-backup-spinner');
    const createBackupFirst = document.getElementById('backup-before-restore')?.checked || false;

    try {
        // Show loading state
        btn.disabled = true;
        btnText.style.display = 'none';
        btnSpinner.style.display = 'inline-block';

        // Create FormData for file upload
        const formData = new FormData();
        formData.append('file', file);
        formData.append('create_backup_first', createBackupFirst.toString());

        const restoreHeaders = {};
        if (typeof authState !== 'undefined' && authState.token) {
            restoreHeaders['Authorization'] = `Bearer ${authState.token}`;
        }
        const response = await fetch('/api/backup/restore', {
            method: 'POST',
            headers: restoreHeaders,
            body: formData
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to restore backup');
        }

        const result = await response.json();

        showMessage('Backup restored successfully! Reloading application...', 'success');

        // Wait a moment for the message to be visible, then reload
        setTimeout(() => {
            window.location.reload();
        }, 2000);

    } catch (error) {
        console.error('Backup restore failed:', error);
        showMessage(`Failed to restore backup: ${error.message}`, 'error');

        // Reset button state on error
        btn.disabled = false;
        btnText.style.display = 'inline';
        btnSpinner.style.display = 'none';
    }
    // Note: We don't reset button state on success because we're reloading the page
}

async function loadAboutInfo() {
    try {
        const data = await apiRequest('/api/about');
        const versionElement = document.getElementById('about-version');
        if (versionElement) {
            versionElement.textContent = data.version || '0.1.0';
        }
    } catch (error) {
        console.error('Failed to load about information:', error);
        // Fallback to default version if API fails
        const versionElement = document.getElementById('about-version');
        if (versionElement) {
            versionElement.textContent = '0.1.0';
        }
    }
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

// Format mirror status with appropriate badge color
function formatMirrorStatus(mirror) {
    const status = mirror.last_update_status;
    if (!status) {
        return '<span class="text-muted">N/A</span>';
    }
    // Map status values to appropriate badge styles
    let badgeClass = 'badge-info';
    let displayStatus = status;
    if (status === 'finished' || status === 'success') {
        badgeClass = 'badge-success';
        displayStatus = 'Success';
    } else if (status === 'failed') {
        badgeClass = 'badge-danger';
        displayStatus = 'Failed';
    } else if (status === 'started' || status === 'updating' || status === 'syncing') {
        badgeClass = 'badge-warning';
        displayStatus = 'Syncing';
    } else if (status === 'pending') {
        badgeClass = 'badge-secondary';
        displayStatus = 'Pending';
    }
    let badge = `<span class="badge ${badgeClass}">${escapeHtml(displayStatus)}</span>`;
    // Add error tooltip if there's an error
    if (mirror.last_error && status === 'failed') {
        const truncatedError = mirror.last_error.substring(0, 50) + (mirror.last_error.length > 50 ? '...' : '');
        badge += `<br><small class="text-danger" title="${escapeHtml(mirror.last_error)}">${escapeHtml(truncatedError)}</small>`;
    }
    return badge;
}

// Format mirror sync time showing last successful and last attempt
function formatMirrorSyncTime(mirror) {
    let html = '';
    if (mirror.last_successful_update) {
        html += `<span title="Last successful sync">${new Date(mirror.last_successful_update).toLocaleString()}</span>`;
    } else {
        html += '<span class="text-muted">Never synced</span>';
    }
    if (mirror.last_update_at && mirror.last_update_at !== mirror.last_successful_update) {
        html += `<br><small class="text-muted" title="Last attempt">(attempt: ${new Date(mirror.last_update_at).toLocaleString()})</small>`;
    }
    return html;
}

function debounce(fn, delayMs) {
    let t = null;
    return (...args) => {
        if (t) clearTimeout(t);
        t = setTimeout(() => fn(...args), delayMs);
    };
}

// ----------------------------
// Global Search
// ----------------------------
let globalSearchController = null;

function initGlobalSearch() {
    const searchInput = document.getElementById('global-search');
    const resultsContainer = document.getElementById('global-search-results');

    if (!searchInput || !resultsContainer) return;

    // Debounced search function
    const doSearch = debounce(async (query) => {
        if (!query || query.length < 1) {
            resultsContainer.classList.remove('active');
            resultsContainer.innerHTML = '';
            return;
        }

        // Cancel any pending request
        if (globalSearchController) {
            globalSearchController.abort();
        }
        globalSearchController = new AbortController();

        // Show loading
        resultsContainer.classList.add('active');
        resultsContainer.innerHTML = '<div class="search-loading"><div class="spinner"></div></div>';

        try {
            const searchHeaders = {};
            if (typeof authState !== 'undefined' && authState.token) {
                searchHeaders['Authorization'] = `Bearer ${authState.token}`;
            }
            const response = await fetch(`/api/search?q=${encodeURIComponent(query)}&limit=5`, {
                headers: searchHeaders,
                signal: globalSearchController.signal
            });
            const data = await response.json();
            renderSearchResults(data, resultsContainer);
        } catch (error) {
            if (error.name !== 'AbortError') {
                console.error('Search failed:', error);
                resultsContainer.innerHTML = '<div class="search-no-results">Search failed</div>';
            }
        }
    }, 200);

    searchInput.addEventListener('input', (e) => {
        doSearch(e.target.value.trim());
    });

    // Close results when clicking outside
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.global-search-container')) {
            resultsContainer.classList.remove('active');
        }
    });

    // Show results again when focusing on search input if there's content
    searchInput.addEventListener('focus', () => {
        if (searchInput.value.trim() && resultsContainer.innerHTML) {
            resultsContainer.classList.add('active');
        }
    });

    // Handle keyboard navigation
    searchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            resultsContainer.classList.remove('active');
            searchInput.blur();
        }
    });
}

function renderSearchResults(data, container) {
    if (data.total_count === 0) {
        container.innerHTML = '<div class="search-no-results">No results found</div>';
        return;
    }

    let html = '';

    if (data.instances.length > 0) {
        html += '<div class="search-results-group">';
        html += '<div class="search-results-header">Instances</div>';
        for (const item of data.instances) {
            html += `
                <a class="search-result-item" href="#" data-type="instance" data-id="${item.id}">
                    <div class="search-result-title">${escapeHtml(item.title)}</div>
                    <div class="search-result-subtitle">${escapeHtml(item.subtitle || '')}</div>
                </a>
            `;
        }
        html += '</div>';
    }

    if (data.pairs.length > 0) {
        html += '<div class="search-results-group">';
        html += '<div class="search-results-header">Instance Pairs</div>';
        for (const item of data.pairs) {
            html += `
                <a class="search-result-item" href="#" data-type="pair" data-id="${item.id}">
                    <div class="search-result-title">${escapeHtml(item.title)}</div>
                    <div class="search-result-subtitle">${escapeHtml(item.subtitle || '')}</div>
                </a>
            `;
        }
        html += '</div>';
    }

    if (data.mirrors.length > 0) {
        html += '<div class="search-results-group">';
        html += '<div class="search-results-header">Mirrors</div>';
        for (const item of data.mirrors) {
            html += `
                <a class="search-result-item" href="#" data-type="mirror" data-id="${item.id}">
                    <div class="search-result-title">${escapeHtml(item.title)}</div>
                    <div class="search-result-subtitle">${escapeHtml(item.subtitle || '')}</div>
                </a>
            `;
        }
        html += '</div>';
    }

    container.innerHTML = html;

    // Add click handlers for navigation
    container.querySelectorAll('.search-result-item').forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const type = item.dataset.type;
            const id = parseInt(item.dataset.id);
            navigateToSearchResult(type, id);
            container.classList.remove('active');
            document.getElementById('global-search').value = '';
        });
    });
}

function navigateToSearchResult(type, id) {
    // Navigate to the appropriate tab and highlight the item
    switch (type) {
        case 'instance':
            switchTab('instances-tab');
            setTimeout(() => highlightTableRow('instances-list', id), 100);
            break;
        case 'pair':
            switchTab('pairs-tab');
            setTimeout(() => highlightTableRow('pairs-list', id), 100);
            break;
        case 'mirror':
            switchTab('mirrors-tab');
            setTimeout(() => highlightTableRow('mirrors-list', id), 100);
            break;
    }
}

function highlightTableRow(tbodyId, id) {
    const tbody = document.getElementById(tbodyId);
    if (!tbody) return;

    // Remove any existing highlights
    tbody.querySelectorAll('tr.search-highlight').forEach(tr => {
        tr.classList.remove('search-highlight');
    });

    // Find the row with matching ID
    const row = tbody.querySelector(`tr[data-id="${id}"], tr[data-instance-id="${id}"], tr[data-pair-id="${id}"], tr[data-mirror-id="${id}"]`);
    if (row) {
        row.classList.add('search-highlight');
        row.scrollIntoView({ behavior: 'smooth', block: 'center' });

        // Remove highlight after 3 seconds
        setTimeout(() => {
            row.classList.remove('search-highlight');
        }, 3000);
    }
}

// ----------------------------
// URL State Persistence
// ----------------------------
function initUrlState() {
    // Check for tab parameter in URL
    const params = new URLSearchParams(window.location.search);
    const tabParam = params.get('tab');

    if (tabParam) {
        const tabElement = document.querySelector(`[data-tab="${tabParam}"]`);
        if (tabElement) {
            // Small delay to ensure tabs are initialized
            setTimeout(() => tabElement.click(), 50);
        }
    }

    // Update URL when tab changes
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', () => {
            const tabId = tab.dataset.tab;
            updateUrlState({ tab: tabId });
        });
    });
}

function updateUrlState(updates) {
    const params = new URLSearchParams(window.location.search);

    for (const [key, value] of Object.entries(updates)) {
        if (value === null || value === undefined || value === '') {
            params.delete(key);
        } else {
            params.set(key, value);
        }
    }

    const newUrl = params.toString()
        ? `${window.location.pathname}?${params.toString()}`
        : window.location.pathname;

    window.history.replaceState({}, '', newUrl);
}

// ----------------------------
// Authentication
// ----------------------------

const authState = {
    isAuthenticated: false,
    isMultiUser: false,
    user: null,
    token: null
};

// Promise that resolves when user is authenticated (for multi-user mode)
let authReadyResolve = null;
const authReady = new Promise(resolve => { authReadyResolve = resolve; });

async function initAuth() {
    // Check auth mode from server
    try {
        const mode = await apiRequest('/api/auth/mode');
        authState.isMultiUser = mode.multi_user_enabled;

        if (authState.isMultiUser) {
            // Hide main content until authenticated
            document.body.classList.add('auth-required');

            // Check for stored token
            const storedToken = localStorage.getItem('auth_token');
            if (storedToken) {
                authState.token = storedToken;
                try {
                    await loadCurrentUser();
                    document.body.classList.remove('auth-required');
                    authReadyResolve();
                } catch (error) {
                    // Token is invalid, clear it
                    logout(false);
                    showLoginModal();
                    // Wait for login to complete
                    await authReady;
                }
            } else {
                showLoginModal();
                // Wait for login to complete
                await authReady;
            }
        } else {
            // Legacy mode - hide user menu, auth is ready immediately
            hideUserMenu();
            authReadyResolve();
        }
    } catch (error) {
        console.error('Failed to check auth mode:', error);
        hideUserMenu();
        authReadyResolve(); // Resolve anyway to not block the app
    }
}

async function loadCurrentUser() {
    const user = await authApiRequest('/api/auth/me');
    authState.user = user;
    authState.isAuthenticated = true;
    updateUserMenu();

    // Show Settings tab if admin
    if (user.is_admin) {
        const settingsTab = document.getElementById('settings-tab-btn');
        if (settingsTab) settingsTab.style.display = '';
    }
}

function updateUserMenu() {
    const userMenu = document.getElementById('user-menu');
    const userName = document.getElementById('user-name');
    const userMenuUsername = document.getElementById('user-menu-username');
    const userMenuRole = document.getElementById('user-menu-role');

    if (authState.isAuthenticated && authState.user) {
        if (userMenu) userMenu.style.display = '';
        if (userName) userName.textContent = authState.user.username;
        if (userMenuUsername) userMenuUsername.textContent = authState.user.username;
        if (userMenuRole) userMenuRole.textContent = authState.user.is_admin ? 'Administrator' : 'User';
    } else {
        hideUserMenu();
    }
}

function hideUserMenu() {
    const userMenu = document.getElementById('user-menu');
    if (userMenu) userMenu.style.display = 'none';
}

// Toggle user menu dropdown
function initUserMenu() {
    const toggle = document.getElementById('user-menu-toggle');
    const menu = document.getElementById('user-menu');

    if (toggle && menu) {
        toggle.addEventListener('click', (e) => {
            e.stopPropagation();
            menu.classList.toggle('open');
        });

        // Close when clicking outside
        document.addEventListener('click', (e) => {
            if (!e.target.closest('.user-menu')) {
                menu.classList.remove('open');
            }
        });
    }
}

// API request with JWT token
async function authApiRequest(url, options = {}) {
    const headers = {
        'Content-Type': 'application/json',
        ...options.headers
    };

    if (authState.token) {
        headers['Authorization'] = `Bearer ${authState.token}`;
    }

    const response = await fetch(url, {
        ...options,
        headers
    });

    if (!response.ok) {
        let errorData;
        let errorMessage = 'Request failed';

        try {
            errorData = await response.json();
            errorMessage = errorData.detail || errorMessage;
        } catch (e) {
            errorMessage = `HTTP ${response.status}: ${response.statusText}`;
        }

        // Handle auth errors
        if (response.status === 401) {
            logout(false);
            showLoginModal();
        }

        throw new APIError(errorMessage, 'server', response.status, errorData);
    }

    return await response.json();
}

// Login Modal
function showLoginModal() {
    const modal = document.getElementById('login-modal');
    if (modal) {
        modal.style.display = 'flex';

        // Hide close button and disable backdrop click when auth is required
        const closeBtn = modal.querySelector('.modal-close');
        const backdrop = modal.querySelector('.modal-backdrop');
        if (authState.isMultiUser && !authState.isAuthenticated) {
            if (closeBtn) closeBtn.style.display = 'none';
            if (backdrop) backdrop.style.cursor = 'default';
        } else {
            if (closeBtn) closeBtn.style.display = '';
            if (backdrop) backdrop.style.cursor = 'pointer';
        }

        document.getElementById('login-username')?.focus();
    }
}

function closeLoginModal() {
    // Don't allow closing the modal if multi-user mode is enabled and not authenticated
    if (authState.isMultiUser && !authState.isAuthenticated) {
        return;
    }

    const modal = document.getElementById('login-modal');
    if (modal) modal.style.display = 'none';

    // Clear form
    document.getElementById('login-form')?.reset();
    const errorEl = document.getElementById('login-error');
    if (errorEl) errorEl.style.display = 'none';
}

async function handleLogin(event) {
    event.preventDefault();

    const username = document.getElementById('login-username')?.value;
    const password = document.getElementById('login-password')?.value;
    const submitBtn = document.getElementById('login-submit-btn');
    const errorEl = document.getElementById('login-error');

    if (!username || !password) {
        if (errorEl) {
            errorEl.textContent = 'Please enter username and password';
            errorEl.style.display = 'block';
        }
        return;
    }

    try {
        submitBtn.disabled = true;
        submitBtn.textContent = 'Signing in...';

        const response = await fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
        });

        if (!response.ok) {
            // Handle non-JSON error responses (e.g., HTML error pages from proxy/server)
            let errorMessage = 'Login failed';
            try {
                const error = await response.json();
                errorMessage = error.detail || errorMessage;
            } catch {
                // Response is not JSON (e.g., HTML error page)
                if (response.status === 500) {
                    errorMessage = 'Server error. Please check the server logs and try again.';
                } else if (response.status === 502 || response.status === 503 || response.status === 504) {
                    errorMessage = 'Service unavailable. Please check the server is running.';
                } else {
                    errorMessage = `Server error (${response.status}). Please try again.`;
                }
            }
            throw new Error(errorMessage);
        }

        const data = await response.json();

        // Store token
        authState.token = data.access_token;
        localStorage.setItem('auth_token', data.access_token);

        // Load user info
        await loadCurrentUser();

        // Show main content and signal auth is complete
        document.body.classList.remove('auth-required');
        if (authReadyResolve) authReadyResolve();

        closeLoginModal();
        showMessage('Logged in successfully', 'success');

    } catch (error) {
        if (errorEl) {
            errorEl.textContent = error.message;
            errorEl.style.display = 'block';
        }
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Sign In';
    }
}

function logout(showMsg = true) {
    authState.token = null;
    authState.user = null;
    authState.isAuthenticated = false;
    localStorage.removeItem('auth_token');

    hideUserMenu();

    // Hide settings tab
    const settingsTab = document.getElementById('settings-tab-btn');
    if (settingsTab) settingsTab.style.display = 'none';

    if (showMsg) {
        showMessage('Logged out successfully', 'success');
    }

    if (authState.isMultiUser) {
        showLoginModal();
    }
}

// Change Password Modal
function showChangePasswordModal() {
    const modal = document.getElementById('change-password-modal');
    if (modal) {
        modal.style.display = 'flex';
        document.getElementById('current-password')?.focus();
    }

    // Close user menu
    document.getElementById('user-menu')?.classList.remove('open');
}

function closeChangePasswordModal() {
    const modal = document.getElementById('change-password-modal');
    if (modal) modal.style.display = 'none';

    // Clear form
    document.getElementById('change-password-form')?.reset();
    const errorEl = document.getElementById('change-password-error');
    if (errorEl) errorEl.style.display = 'none';
}

async function handleChangePassword(event) {
    event.preventDefault();

    const currentPassword = document.getElementById('current-password')?.value;
    const newPassword = document.getElementById('new-password')?.value;
    const confirmPassword = document.getElementById('confirm-new-password')?.value;
    const submitBtn = document.getElementById('change-password-submit-btn');
    const errorEl = document.getElementById('change-password-error');

    // Validation
    if (!currentPassword || !newPassword || !confirmPassword) {
        if (errorEl) {
            errorEl.textContent = 'Please fill in all fields';
            errorEl.style.display = 'block';
        }
        return;
    }

    if (newPassword !== confirmPassword) {
        if (errorEl) {
            errorEl.textContent = 'New passwords do not match';
            errorEl.style.display = 'block';
        }
        return;
    }

    if (newPassword.length < 8) {
        if (errorEl) {
            errorEl.textContent = 'Password must be at least 8 characters';
            errorEl.style.display = 'block';
        }
        return;
    }

    try {
        submitBtn.disabled = true;
        submitBtn.textContent = 'Changing...';

        await authApiRequest('/api/auth/change-password', {
            method: 'POST',
            body: JSON.stringify({
                current_password: currentPassword,
                new_password: newPassword
            })
        });

        closeChangePasswordModal();
        showMessage('Password changed successfully', 'success');

    } catch (error) {
        if (errorEl) {
            errorEl.textContent = error.message;
            errorEl.style.display = 'block';
        }
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Change Password';
    }
}

// ----------------------------
// User Management (Admin only)
// ----------------------------

let usersData = [];

async function loadUsers() {
    const tbody = document.getElementById('users-list');
    if (!tbody) return;

    try {
        showSkeletonLoader(tbody, 3);
        usersData = await authApiRequest('/api/users');
        renderUsers(usersData);
    } catch (error) {
        console.error('Failed to load users:', error);
        showErrorState(tbody, error, loadUsers);
    }
}

function renderUsers(users) {
    const tbody = document.getElementById('users-list');
    if (!tbody) return;

    if (users.length === 0) {
        showEmptyState(tbody, 'No Users', 'No users have been created yet.', null);
        return;
    }

    tbody.innerHTML = users.map(user => {
        const roleBadge = user.is_admin
            ? '<span class="admin-badge">Admin</span>'
            : '<span class="user-badge">User</span>';

        const statusClass = user.is_active ? 'status-active' : 'status-inactive';
        const statusText = user.is_active ? 'Active' : 'Inactive';

        const isSelf = authState.user && authState.user.id === user.id;
        const deleteDisabled = isSelf ? 'disabled title="Cannot delete yourself"' : '';

        return `
            <tr data-id="${user.id}">
                <td>${escapeHtml(user.username)}${isSelf ? ' <span class="text-muted">(you)</span>' : ''}</td>
                <td>${user.email ? escapeHtml(user.email) : '<span class="text-muted">-</span>'}</td>
                <td>${roleBadge}</td>
                <td><span class="${statusClass}">${statusText}</span></td>
                <td>${new Date(user.created_at).toLocaleDateString()}</td>
                <td>
                    <div class="table-actions">
                        <button class="btn btn-secondary btn-small" onclick="openEditUserModal(${user.id})">Edit</button>
                        <button class="btn btn-danger btn-small" onclick="deleteUser(${user.id})" ${deleteDisabled}>Delete</button>
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}

async function createUser() {
    const username = document.getElementById('user-username')?.value;
    const email = document.getElementById('user-email')?.value || null;
    const password = document.getElementById('user-password')?.value;
    const passwordConfirm = document.getElementById('user-password-confirm')?.value;
    const isAdmin = document.getElementById('user-is-admin')?.checked || false;

    if (!username || !password) {
        showMessage('Username and password are required', 'error');
        return;
    }

    if (password !== passwordConfirm) {
        showMessage('Passwords do not match', 'error');
        return;
    }

    if (password.length < 8) {
        showMessage('Password must be at least 8 characters', 'error');
        return;
    }

    try {
        await authApiRequest('/api/users', {
            method: 'POST',
            body: JSON.stringify({ username, email, password, is_admin: isAdmin })
        });

        showMessage('User created successfully', 'success');
        document.getElementById('user-form')?.reset();
        await loadUsers();

    } catch (error) {
        showMessage(error.message, 'error');
    }
}

function openEditUserModal(userId) {
    const user = usersData.find(u => u.id === userId);
    if (!user) return;

    document.getElementById('edit-user-id').value = user.id;
    document.getElementById('edit-user-username').value = user.username;
    document.getElementById('edit-user-email').value = user.email || '';
    document.getElementById('edit-user-password').value = '';
    document.getElementById('edit-user-is-admin').checked = user.is_admin;
    document.getElementById('edit-user-is-active').checked = user.is_active;

    const modal = document.getElementById('edit-user-modal');
    if (modal) modal.style.display = 'flex';
}

function closeEditUserModal() {
    const modal = document.getElementById('edit-user-modal');
    if (modal) modal.style.display = 'none';

    document.getElementById('edit-user-form')?.reset();
    const errorEl = document.getElementById('edit-user-error');
    if (errorEl) errorEl.style.display = 'none';
}

async function handleEditUser(event) {
    event.preventDefault();

    const userId = document.getElementById('edit-user-id')?.value;
    const username = document.getElementById('edit-user-username')?.value;
    const email = document.getElementById('edit-user-email')?.value || null;
    const password = document.getElementById('edit-user-password')?.value || null;
    const isAdmin = document.getElementById('edit-user-is-admin')?.checked;
    const isActive = document.getElementById('edit-user-is-active')?.checked;
    const submitBtn = document.getElementById('edit-user-submit-btn');
    const errorEl = document.getElementById('edit-user-error');

    if (!username) {
        if (errorEl) {
            errorEl.textContent = 'Username is required';
            errorEl.style.display = 'block';
        }
        return;
    }

    if (password && password.length < 8) {
        if (errorEl) {
            errorEl.textContent = 'Password must be at least 8 characters';
            errorEl.style.display = 'block';
        }
        return;
    }

    const payload = { username, email, is_admin: isAdmin, is_active: isActive };
    if (password) payload.password = password;

    try {
        submitBtn.disabled = true;
        submitBtn.textContent = 'Saving...';

        await authApiRequest(`/api/users/${userId}`, {
            method: 'PUT',
            body: JSON.stringify(payload)
        });

        closeEditUserModal();
        showMessage('User updated successfully', 'success');
        await loadUsers();

        // Reload current user in case they edited themselves
        if (authState.user && authState.user.id === parseInt(userId)) {
            await loadCurrentUser();
        }

    } catch (error) {
        if (errorEl) {
            errorEl.textContent = error.message;
            errorEl.style.display = 'block';
        }
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Save Changes';
    }
}

async function deleteUser(userId) {
    if (authState.user && authState.user.id === userId) {
        showMessage('Cannot delete your own account', 'error');
        return;
    }

    if (!confirm('Are you sure you want to delete this user?')) {
        return;
    }

    try {
        await authApiRequest(`/api/users/${userId}`, { method: 'DELETE' });
        showMessage('User deleted successfully', 'success');
        await loadUsers();
    } catch (error) {
        showMessage(error.message, 'error');
    }
}

// User form submission handler (for Settings tab)
document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('user-form')?.addEventListener('submit', async (e) => {
        e.preventDefault();
        await createUser();
    });
});

// ==========================================
// Issue Mirroring Functions
// ==========================================

async function showIssueMirrorConfig(mirrorId) {
    const modal = document.getElementById('issue-mirror-config-modal');
    if (!modal) return;

    // Store mirror ID
    const mirrorIdEl = document.getElementById('issue-config-mirror-id');
    if (mirrorIdEl) mirrorIdEl.value = mirrorId;

    // Try to load existing configuration
    try {
        const config = await apiRequest(`/api/issue-mirrors/by-mirror/${mirrorId}`);

        // Populate form with existing config
        document.getElementById('issue-config-id').value = config.id;
        document.getElementById('issue-sync-enabled').checked = config.enabled;
        document.getElementById('issue-sync-comments').checked = config.sync_comments;
        document.getElementById('issue-sync-labels').checked = config.sync_labels;
        document.getElementById('issue-sync-attachments').checked = config.sync_attachments;
        document.getElementById('issue-sync-weight').checked = config.sync_weight;
        document.getElementById('issue-sync-time-estimate').checked = config.sync_time_estimate;
        document.getElementById('issue-sync-time-spent').checked = config.sync_time_spent;
        document.getElementById('issue-sync-closed').checked = config.sync_closed_issues;
        document.getElementById('issue-update-existing').checked = config.update_existing;
        document.getElementById('issue-sync-existing').checked = config.sync_existing_issues;
        document.getElementById('issue-sync-interval').value = config.sync_interval_minutes;

        // Show status
        const statusEl = document.getElementById('issue-config-status');
        if (statusEl && config.last_sync_at) {
            const lastSync = new Date(config.last_sync_at).toLocaleString();
            const status = config.last_sync_status || 'unknown';
            statusEl.innerHTML = `
                <p class="text-muted">
                    <strong>Last Sync:</strong> ${lastSync}<br>
                    <strong>Status:</strong> <span class="badge badge-${status === 'success' ? 'success' : 'warning'}">${status}</span>
                </p>
            `;
        }
    } catch (error) {
        // No existing config - use defaults (form already has default values)
        const configIdEl = document.getElementById('issue-config-id');
        if (configIdEl) configIdEl.value = '';
        const statusEl = document.getElementById('issue-config-status');
        if (statusEl) statusEl.innerHTML = '<p class="text-muted">No issue sync configured for this mirror</p>';
    }

    // Show modal
    modal.style.display = 'flex';
}

function closeIssueMirrorConfigModal() {
    const modal = document.getElementById('issue-mirror-config-modal');
    if (modal) modal.style.display = 'none';

    // Clear form
    document.getElementById('issue-mirror-config-form')?.reset();
    const configId = document.getElementById('issue-config-id');
    if (configId) configId.value = '';
    const mirrorId = document.getElementById('issue-config-mirror-id');
    if (mirrorId) mirrorId.value = '';
    const statusEl = document.getElementById('issue-config-status');
    if (statusEl) statusEl.innerHTML = '';

    const errorEl = document.getElementById('issue-config-error');
    if (errorEl) errorEl.style.display = 'none';
}

async function handleIssueMirrorConfig(event) {
    event.preventDefault();

    const submitBtn = document.getElementById('issue-config-submit-btn');
    const errorEl = document.getElementById('issue-config-error');

    try {
        showButtonLoading(submitBtn, true);
        if (errorEl) errorEl.style.display = 'none';

        const mirrorId = document.getElementById('issue-config-mirror-id').value;
        const configId = document.getElementById('issue-config-id').value;

        const data = {
            mirror_id: parseInt(mirrorId),
            enabled: document.getElementById('issue-sync-enabled').checked,
            sync_comments: document.getElementById('issue-sync-comments').checked,
            sync_labels: document.getElementById('issue-sync-labels').checked,
            sync_attachments: document.getElementById('issue-sync-attachments').checked,
            sync_weight: document.getElementById('issue-sync-weight').checked,
            sync_time_estimate: document.getElementById('issue-sync-time-estimate').checked,
            sync_time_spent: document.getElementById('issue-sync-time-spent').checked,
            sync_closed_issues: document.getElementById('issue-sync-closed').checked,
            update_existing: document.getElementById('issue-update-existing').checked,
            sync_existing_issues: document.getElementById('issue-sync-existing').checked,
            sync_interval_minutes: parseInt(document.getElementById('issue-sync-interval').value)
        };

        let result;
        if (configId) {
            // Update existing config
            result = await apiRequest(`/api/issue-mirrors/${configId}`, {
                method: 'PUT',
                body: JSON.stringify(data)
            });
            showMessage('Issue sync configuration updated', 'success');
        } else {
            // Create new config
            result = await apiRequest('/api/issue-mirrors', {
                method: 'POST',
                body: JSON.stringify(data)
            });
            showMessage('Issue sync configuration created', 'success');
        }

        closeIssueMirrorConfigModal();
        await loadMirrors(); // Refresh mirrors list to show issue sync status

    } catch (error) {
        console.error('Failed to save issue mirror config:', error);
        if (errorEl) {
            errorEl.textContent = error.message || 'Failed to save configuration';
            errorEl.style.display = 'block';
        }
    } finally {
        showButtonLoading(submitBtn, false);
    }
}

async function deleteIssueMirrorConfig(mirrorId) {
    if (!confirm('Are you sure you want to disable issue synchronization for this mirror?')) return;

    try {
        // Get config ID first
        const config = await apiRequest(`/api/issue-mirrors/by-mirror/${mirrorId}`);

        await apiRequest(`/api/issue-mirrors/${config.id}`, { method: 'DELETE' });
        showMessage('Issue synchronization disabled', 'success');
        await loadMirrors();
    } catch (error) {
        console.error('Failed to delete issue mirror config:', error);
        showMessage(error.message || 'Failed to disable issue sync', 'error');
    }
}

async function triggerIssueSync(mirrorId) {
    try {
        // Get config ID first
        const config = await apiRequest(`/api/issue-mirrors/by-mirror/${mirrorId}`);

        await apiRequest(`/api/issue-mirrors/${config.id}/trigger-sync`, { method: 'POST' });
        showMessage('Issue sync triggered (will be implemented in Phase 2)', 'info');
    } catch (error) {
        console.error('Failed to trigger issue sync:', error);
        showMessage(error.message || 'Failed to trigger issue sync', 'error');
    }
}
