(function () {
    'use strict';

    const SEARCH_URL = window.SEARCH_URL;
    const DEBOUNCE_MS = 280;
    const MIN_QUERY_LENGTH = 2;
    const EXCLUDED_TYPES = new Set(['PENSION']);

    const input         = document.getElementById('searchInput');
    const dropdown      = document.getElementById('searchDropdown');
    const overlay       = document.getElementById('loadingOverlay');
    const loadingName   = document.getElementById('loadingCompanyName');
    const loadingTicker = document.getElementById('loadingTicker');
    const loadingBar    = document.getElementById('loadingBar');
    const loadingStatus = document.getElementById('loadingStatus');

    // Card grid elements — only present on the workspace page
    const resultsGrid    = document.getElementById('resultsGrid');
    const emptyState     = document.getElementById('emptyState');
    const searchingState = document.getElementById('searchingState');
    const noResultsState = document.getElementById('noResultsState');

    // CARD_MODE: workspace uses a full card grid; company_detail uses the dropdown
    const CARD_MODE = !!resultsGrid;

    if (!input || !SEARCH_URL) { return; }

    let debounceTimer      = null;
    let activeIndex        = -1;
    let requestController  = null;

    // ── Utilities ─────────────────────────────────────────────────────────────

    function esc(s) {
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g,  '&lt;')
            .replace(/>/g,  '&gt;')
            .replace(/"/g,  '&quot;');
    }

    // ── Loading overlay ───────────────────────────────────────────────────────

    function openOverlay(secId, name, ticker) {
        if (!overlay || !window.COMPANY_URL_TPL) { return; }

        if (loadingName)   { loadingName.textContent   = name; }
        if (loadingTicker) { loadingTicker.textContent = ticker || secId; }
        if (loadingStatus) { loadingStatus.textContent = 'FETCHING DATA...'; }

        if (loadingBar) {
            loadingBar.style.transition = 'none';
            loadingBar.style.width = '0%';
            window.requestAnimationFrame(function () {
                window.requestAnimationFrame(function () {
                    loadingBar.style.transition = 'width 0.6s linear';
                    loadingBar.style.width = '60%';
                });
            });
            window.setTimeout(function () {
                loadingBar.style.transition = 'width 0.8s linear';
                loadingBar.style.width = '85%';
            }, 600);
        }

        overlay.style.display = 'flex';
        overlay.setAttribute('aria-hidden', 'false');

        window.setTimeout(function () {
            window.location.assign(
                window.COMPANY_URL_TPL.replace('__SEC_ID__', encodeURIComponent(secId))
            );
        }, 300);
    }

    // ── Dropdown mode (company_detail + keyboard nav) ─────────────────────────

    function resultRows() {
        return dropdown ? Array.from(dropdown.querySelectorAll('.search-result')) : [];
    }

    function showDropdown() {
        if (!dropdown) { return; }
        dropdown.style.display = 'block';
        input.setAttribute('aria-expanded', 'true');
    }

    function hideDropdown() {
        if (!dropdown) { return; }
        dropdown.style.display = 'none';
        input.setAttribute('aria-expanded', 'false');
        activeIndex = -1;
    }

    function renderDropdownMessage(msg) {
        if (!dropdown) { return; }
        dropdown.replaceChildren();
        const row = document.createElement('div');
        row.className = 'search-message';
        row.textContent = msg;
        dropdown.appendChild(row);
        activeIndex = -1;
        showDropdown();
    }

    function setActiveResult(nextIndex) {
        const rows = resultRows();
        if (!rows.length) { activeIndex = -1; return; }
        activeIndex = (nextIndex + rows.length) % rows.length;
        rows.forEach(function (row, i) {
            const active = (i === activeIndex);
            row.classList.toggle('is-active', active);
            row.setAttribute('aria-selected', String(active));
        });
        rows[activeIndex].scrollIntoView({ block: 'nearest' });
    }

    function selectDropdownRow(row) {
        const secId = row.dataset.secid || '';
        const name  = row.dataset.name  || '';
        if (!secId) { return; }
        hideDropdown();
        openOverlay(secId, name, '');
    }

    function renderDropdownResults(results) {
        if (!dropdown) { return; }
        dropdown.replaceChildren();
        activeIndex = -1;

        const visible = (results || []).filter(
            function (r) { return !EXCLUDED_TYPES.has((r.type || '').toUpperCase()); }
        );

        if (!visible.length) { renderDropdownMessage('NO RESULTS'); return; }

        visible.forEach(function (result) {
            const secId = String(result.secId || '');
            if (!secId) { return; }

            const row = document.createElement('div');
            row.className = 'search-result';
            row.dataset.secid = secId;
            row.dataset.name  = String(result.name || secId);
            row.setAttribute('role', 'option');
            row.setAttribute('aria-selected', 'false');

            const nameEl = document.createElement('span');
            nameEl.className = 'search-result-name';
            nameEl.textContent = row.dataset.name;

            const metaEl = document.createElement('span');
            metaEl.className = 'search-result-meta';
            metaEl.textContent = [result.ticker, result.type, result.exchange]
                .filter(Boolean).join(' · ');

            row.append(nameEl, metaEl);
            row.addEventListener('click', function () { selectDropdownRow(row); });
            dropdown.appendChild(row);
        });

        if (!resultRows().length) { renderDropdownMessage('NO RESULTS'); return; }
        showDropdown();
    }

    // ── Card grid mode (workspace) ────────────────────────────────────────────

    function showEl(el) { if (el) { el.style.display = ''; } }
    function hideEl(el) { if (el) { el.style.display = 'none'; } }

    function resetCardStates() {
        hideEl(emptyState);
        hideEl(searchingState);
        hideEl(noResultsState);
        if (resultsGrid) { resultsGrid.innerHTML = ''; }
    }

    function renderCardResults(results) {
        resetCardStates();

        const visible = (results || []).filter(
            function (r) { return !EXCLUDED_TYPES.has((r.type || '').toUpperCase()); }
        );

        if (!visible.length) { showEl(noResultsState); return; }

        visible.forEach(function (item) {
            const stars = item.star_rating
                ? '★'.repeat(Math.min(5, parseInt(item.star_rating, 10)))
                : '';

            const card = document.createElement('div');
            card.className       = 'result-card';
            card.dataset.secid   = item.secId  || '';
            card.dataset.name    = item.name   || '';
            card.dataset.ticker  = item.ticker || '';

            card.innerHTML =
                '<span class="card-name">' + esc(item.name || '') + '</span>' +
                '<span class="card-meta">' +
                    esc([item.type, item.ticker, item.exchange].filter(Boolean).join(' · ')) +
                '</span>';

            resultsGrid.appendChild(card);
        });
    }

    resultsGrid && resultsGrid.addEventListener('click', function (e) {
        const card = e.target.closest('.result-card');
        if (!card) { return; }
        const secId  = card.dataset.secid;
        const name   = card.dataset.name;
        const ticker = card.dataset.ticker;
        if (!secId) { return; }
        openOverlay(secId, name, ticker);
    });

    // ── Fetch ─────────────────────────────────────────────────────────────────

    async function runSearch(query) {
        if (requestController) { requestController.abort(); }
        requestController = new AbortController();

        if (CARD_MODE) {
            resetCardStates();
            showEl(searchingState);
        } else {
            renderDropdownMessage('SEARCHING...');
        }

        try {
            const response = await fetch(
                SEARCH_URL + '?q=' + encodeURIComponent(query),
                {
                    headers: { 'Accept': 'application/json' },
                    signal: requestController.signal,
                }
            );
            if (!response.ok) { throw new Error('Search failed'); }
            const payload = await response.json();
            if (input.value.trim() !== query) { return; }

            if (CARD_MODE) {
                renderCardResults(payload.results);
            } else {
                renderDropdownResults(payload.results);
            }
        } catch (error) {
            if (error.name !== 'AbortError') {
                if (CARD_MODE) {
                    resetCardStates();
                    showEl(noResultsState);
                } else {
                    renderDropdownMessage('NO RESULTS');
                }
            }
        }
    }

    // ── Input listeners ───────────────────────────────────────────────────────

    input.addEventListener('input', function () {
        window.clearTimeout(debounceTimer);
        const query = input.value.trim();

        if (query.length < MIN_QUERY_LENGTH) {
            if (requestController) { requestController.abort(); }
            if (CARD_MODE) {
                resetCardStates();
                showEl(emptyState);
            } else {
                hideDropdown();
            }
            return;
        }

        debounceTimer = window.setTimeout(function () { runSearch(query); }, DEBOUNCE_MS);
    });

    input.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') {
            if (CARD_MODE) {
                resetCardStates();
                showEl(emptyState);
                input.value = '';
            } else {
                hideDropdown();
            }
            return;
        }

        if (CARD_MODE) { return; }

        const rows = resultRows();
        if (!rows.length || !dropdown || dropdown.style.display === 'none') { return; }

        if (event.key === 'ArrowDown') {
            event.preventDefault();
            setActiveResult(activeIndex + 1);
        } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            setActiveResult(activeIndex < 0 ? rows.length - 1 : activeIndex - 1);
        } else if (event.key === 'Enter' && activeIndex >= 0) {
            event.preventDefault();
            selectDropdownRow(rows[activeIndex]);
        }
    });

    document.addEventListener('click', function (event) {
        if (!CARD_MODE && !event.target.closest('.analysis-search-control')) {
            hideDropdown();
        }
    });
}());
