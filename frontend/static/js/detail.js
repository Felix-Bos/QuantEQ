'use strict';

(function () {
    var secId   = window.DETAIL_SEC_ID;
    var apiUrl  = window.DETAIL_API_URL;
    if (!secId || !apiUrl) { return; }

    var loadingEl = document.getElementById('detailLoading');
    var errorEl   = document.getElementById('detailError');
    var contentEl = document.getElementById('detailContent');

    // ── Utilities ─────────────────────────────────────────────────────────

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function showEl(el) { if (el) { el.style.display = ''; } }
    function hideEl(el) { if (el) { el.style.display = 'none'; } }
    function displayValue(v) { return (v === null || v === undefined || v === '') ? '—' : esc(v); }
    function formatNumber(value, digits) {
        if (value === null || value === undefined || value === '' || !isFinite(Number(value))) { return '—'; }
        return Number(value).toLocaleString(undefined, {
            minimumFractionDigits: digits == null ? 2 : digits,
            maximumFractionDigits: digits == null ? 2 : digits,
        });
    }
    function formatPercent(value, digits) {
        if (value === null || value === undefined || value === '' || !isFinite(Number(value))) { return '—'; }
        return (Number(value) * 100).toFixed(digits == null ? 2 : digits) + '%';
    }
    function valueTone(value, inverse) {
        var number = Number(value);
        if (!isFinite(number) || number === 0) { return ''; }
        var positive = inverse ? number < 0 : number > 0;
        return positive ? 'metric-positive' : 'metric-negative';
    }
    function panelHeader(kicker, title, description, meta) {
        return '<header class="panel-header">' +
            '<div><div class="panel-kicker">' + esc(kicker) + '</div>' +
            '<h2 class="panel-title">' + esc(title) + '</h2>' +
            (description ? '<p class="panel-description">' + esc(description) + '</p>' : '') +
            '</div>' +
            (meta ? '<div class="panel-meta">' + esc(meta) + '</div>' : '') +
            '</header>';
    }
    function panelShell(kicker, title, description, content, meta) {
        return '<div class="panel-shell">' +
            panelHeader(kicker, title, description, meta) +
            '<div class="panel-body">' + content + '</div></div>';
    }
    function metricCards(title, metrics, className) {
        var visible = metrics.filter(function (metric) {
            return metric.value !== null && metric.value !== undefined && metric.value !== '';
        });
        if (!visible.length) { return ''; }
        return '<section class="metric-section ' + (className || '') + '">' +
            (title ? '<div class="section-heading">' + esc(title) + '</div>' : '') +
            '<div class="metric-grid">' +
            visible.map(function (metric) {
                return '<article class="metric-card ' + (metric.tone || '') + '">' +
                    '<div class="metric-label">' + esc(metric.label) + '</div>' +
                    '<div class="metric-value">' + displayValue(metric.display) + '</div>' +
                    (metric.note ? '<div class="metric-note">' + esc(metric.note) + '</div>' : '') +
                    '</article>';
            }).join('') + '</div></section>';
    }

    // ── Sidebar toggle ────────────────────────────────────────────────────

    function setupSidebar() {
        var toggle  = document.getElementById('navToggle');
        var nav     = document.getElementById('detailNav');
        var body    = document.getElementById('detailBody');
        if (!toggle || !nav || !body) { return; }
        toggle.addEventListener('click', function () {
            var collapsed = nav.classList.toggle('collapsed');
            body.classList.toggle('nav-collapsed', collapsed);
            toggle.textContent = collapsed ? '›' : '‹';
        });
    }

    // ── Tab switching ─────────────────────────────────────────────────────

    function setupTabs() {
        document.querySelectorAll('[data-tab]').forEach(function (tab) {
            tab.addEventListener('click', function () {
                document.querySelectorAll('[data-tab]').forEach(function (t) { t.classList.remove('active'); });
                document.querySelectorAll('[data-panel]').forEach(function (p) { p.classList.remove('active'); });
                this.classList.add('active');
                var panel = document.querySelector('[data-panel="' + this.dataset.tab + '"]');
                if (panel) { panel.classList.add('active'); }
            });
        });
    }

    // ── Financial table renderer ──────────────────────────────────────────

    var _KEY_ROW_LABELS = [
        'total', 'net income', 'gross profit', 'operating income', 'ebit', 'ebitda',
        'free cash flow', 'operating cash flow', 'investing cash flow', 'financing cash flow',
        'total assets', 'total liabilities', 'total equity', 'total revenue',
        'cash and cash equivalents', 'pretax income', 'total capitalization',
    ];

    function isKeyRow(label) {
        var l = (label || '').toLowerCase().trim();
        for (var i = 0; i < _KEY_ROW_LABELS.length; i++) {
            if (l.indexOf(_KEY_ROW_LABELS[i]) === 0 || l === _KEY_ROW_LABELS[i]) { return true; }
        }
        return false;
    }

    function parseFinVal(str) {
        if (!str || str === '—' || str === '') { return null; }
        var s = String(str).replace(/,/g, '').trim();
        var m = 1;
        if (s.slice(-1) === 'T') { m = 1e12; s = s.slice(0, -1); }
        else if (s.slice(-1) === 'B') { m = 1e9; s = s.slice(0, -1); }
        else if (s.slice(-1) === 'M') { m = 1e6; s = s.slice(0, -1); }
        else if (s.slice(-1) === 'K') { m = 1e3; s = s.slice(0, -1); }
        var n = parseFloat(s);
        return isFinite(n) ? n * m : null;
    }

    function buildTableRows(flat_rows, columns) {
        var n = (columns || []).length;
        return flat_rows.map(function (row) {
            var d = row.depth || 0;
            var cells = row.cells || [];
            var hasValues = cells.some(function (c) { return c !== '' && c !== null && c !== undefined; });

            // Category separator (depth 0, no values)
            if (d === 0 && !hasValues) {
                return '<tr class="fin-cat-head"><td colspan="' + (n + 1) + '" class="fin-cat-label">' +
                    esc(row.label) + '</td></tr>';
            }

            var key  = d === 0 || isKeyRow(row.label);
            var cls  = 'fn-row fn-d' + d + (key ? ' fn-key-row' : '') + (hasValues ? ' fn-chartable' : '');
            var data = hasValues ? ' data-fin=\'' + JSON.stringify({ label: row.label, cells: cells }) + '\'' : '';

            var cellsHtml = cells.map(function (cell) {
                var c = cell && String(cell).charAt(0) === '-' ? 'fn-neg' : cell ? 'fn-pos' : 'fn-nil';
                return '<td class="fn-cell ' + c + '">' + (cell ? esc(cell) : '—') + '</td>';
            }).join('');

            var icon = hasValues ? '<span class="fn-chart-icon" aria-hidden="true"></span>' : '';

            return '<tr class="' + cls + '"' + data + '>' +
                '<td class="fn-label">' + icon + esc(row.label) + '</td>' + cellsHtml + '</tr>';
        }).join('');
    }

    function renderFinTable(table, title) {
        if (!table || !Array.isArray(table.flat_rows) || !table.flat_rows.length) { return ''; }
        var columns = table.columns || [];
        var cols = columns.map(function (c) {
            return '<th class="fn-th fn-num">' + esc(c) + '</th>';
        }).join('');
        var rows = buildTableRows(table.flat_rows, columns);
        return '<section class="research-card fin-block" data-fin-cols=\'' + JSON.stringify(columns) + '\'>' +
            '<div class="qc-card-header">' +
            '<div><div class="qc-card-kicker">FINANCIAL DATA</div>' +
            '<div class="qc-card-title">' + esc(title) + '</div></div>' +
            '<div class="qc-card-meta">' + esc(columns.length + ' PERIODS') + '</div></div>' +
            '<div class="fin-scroll"><table class="fin-table">' +
            '<thead><tr><th class="fn-th fn-indicator">INDICATOR</th>' + cols + '</tr></thead>' +
            '<tbody>' + rows + '</tbody></table></div></section>';
    }

    // ── Financial row chart modal ─────────────────────────────────────────

    function fmtBarLabel(v) {
        var a = Math.abs(v);
        if (a >= 1e12) { return (v / 1e12).toFixed(2) + 'T'; }
        if (a >= 1e9)  { return (v / 1e9).toFixed(2) + 'B'; }
        if (a >= 1e6)  { return (v / 1e6).toFixed(2) + 'M'; }
        if (a >= 1e3)  { return (v / 1e3).toFixed(1) + 'K'; }
        return v.toFixed(2);
    }

    function renderBarChart(pairs) {
        if (!pairs.length) { return '<div class="detail-empty">No numeric data to chart</div>'; }
        var W = 640, H = 300, PL = 72, PR = 16, PT = 28, PB = 44;
        var vals = pairs.map(function (p) { return p.num; });
        var minV = Math.min.apply(null, vals);
        var maxV = Math.max.apply(null, vals);
        var rng  = maxV - minV || Math.abs(maxV) || 1;
        minV -= rng * 0.1; maxV += rng * 0.1;
        var chartW = W - PL - PR;
        var chartH = H - PT - PB;
        var barW   = Math.max(6, Math.floor(chartW / pairs.length) - 5);

        function yp(v) { return PT + (maxV - v) / (maxV - minV) * chartH; }
        var zeroY = yp(0);
        var hasNeg = minV < 0;

        // Grid
        var grid = '';
        for (var gi = 0; gi <= 4; gi++) {
            var gv = minV + (maxV - minV) * gi / 4;
            var gy = yp(gv);
            grid += '<line x1="' + PL + '" y1="' + gy.toFixed(1) + '" x2="' + (W - PR) + '" y2="' + gy.toFixed(1) + '" class="chart-grid-line"/>' +
                '<text x="' + (PL - 6) + '" y="' + (gy + 3.5).toFixed(1) + '" text-anchor="end" class="chart-axis-label">' + esc(fmtBarLabel(gv)) + '</text>';
        }
        if (hasNeg) {
            grid += '<line x1="' + PL + '" y1="' + zeroY.toFixed(1) + '" x2="' + (W - PR) + '" y2="' + zeroY.toFixed(1) + '" class="fin-zero-line"/>';
        }

        // Bars + labels + crosshair data
        var bars = '', xlabels = '', tooltipData = [];
        pairs.forEach(function (p, i) {
            var bx = PL + i * (chartW / pairs.length) + (chartW / pairs.length - barW) / 2;
            var by = p.num >= 0 ? yp(p.num) : zeroY;
            var bh = Math.max(1, Math.abs(yp(p.num) - zeroY));
            var col = p.num >= 0 ? 'var(--color-positive)' : 'var(--color-negative)';
            var cx = bx + barW / 2;
            bars += '<rect class="fin-bar" x="' + bx.toFixed(1) + '" y="' + by.toFixed(1) + '" width="' + barW + '" height="' + bh.toFixed(1) + '" fill="' + col + '" rx="1.5" data-idx="' + i + '"/>';
            xlabels += '<text x="' + cx.toFixed(1) + '" y="' + (H - 10) + '" text-anchor="middle" class="chart-axis-label">' + esc(p.col) + '</text>';
            tooltipData.push({ x: cx, val: p.num, col: p.col, raw: p.raw });
        });

        // Tooltip elements
        var tipHtml = '<g id="finBarTip" style="display:none;pointer-events:none">' +
            '<rect id="finBarTipBg" class="qt-tooltip-bg" rx="3" x="0" y="0" width="120" height="38"/>' +
            '<text id="finBarTipDate" class="qt-tdate" x="0" y="0"/>' +
            '<text id="finBarTipVal" class="qt-tval" x="0" y="0"/>' +
            '</g>';

        // Hover area
        var hoverArea = '<rect id="finBarHover" x="' + PL + '" y="' + PT + '" width="' + chartW + '" height="' + chartH + '" fill="transparent" style="cursor:crosshair"/>';
        var crosshair = '<line id="finBarCh" class="qt-crosshair" x1="0" x2="0" y1="' + PT + '" y2="' + (H - PB) + '" style="display:none"/>';

        var dataTag = '<script type="application/json" id="finBarData">' + JSON.stringify({ points: tooltipData, PT: PT, PB: PB, H: H }) + '<\/script>';

        return dataTag + '<svg viewBox="0 0 ' + W + ' ' + H + '" class="fin-bar-chart">' +
            grid + bars + xlabels + crosshair + tipHtml + hoverArea + '</svg>';
    }

    function setupBarChartTooltip() {
        var dataEl = document.getElementById('finBarData');
        var svg = document.querySelector('.fin-bar-chart');
        if (!dataEl || !svg) { return; }
        var d = JSON.parse(dataEl.textContent || '{}');
        var pts = d.points || [];
        if (!pts.length) { return; }

        var hoverArea = document.getElementById('finBarHover');
        var crosshair = document.getElementById('finBarCh');
        var tip       = document.getElementById('finBarTip');
        var tipBg     = document.getElementById('finBarTipBg');
        var tipDate   = document.getElementById('finBarTipDate');
        var tipVal    = document.getElementById('finBarTipVal');
        if (!hoverArea) { return; }

        var svgW = 640, chartW = svgW - 72 - 16;

        hoverArea.addEventListener('mousemove', function (e) {
            var rect = svg.getBoundingClientRect();
            var scale = svgW / rect.width;
            var mx    = (e.clientX - rect.left) * scale;
            var idx   = Math.max(0, Math.min(pts.length - 1, Math.round((mx - 72) / chartW * (pts.length - 1))));
            var pt    = pts[idx];
            if (!pt) { return; }

            var cx = pt.x;
            crosshair.setAttribute('x1', cx.toFixed(1)); crosshair.setAttribute('x2', cx.toFixed(1));
            crosshair.style.display = '';

            var valStr = fmtBarLabel(pt.val);
            tipDate.textContent = pt.col;
            tipVal.textContent  = valStr;
            var tipX = cx + 8, tipY = d.PT + 4;
            var tipW = Math.max(80, Math.max(pt.col.length, valStr.length) * 7 + 16);
            if (tipX + tipW > svgW - 16) { tipX = cx - tipW - 8; }
            tipBg.setAttribute('x', tipX - 4); tipBg.setAttribute('y', tipY);
            tipBg.setAttribute('width', tipW + 8); tipBg.setAttribute('height', 36);
            tipDate.setAttribute('x', tipX); tipDate.setAttribute('y', tipY + 13);
            tipVal.setAttribute('x', tipX); tipVal.setAttribute('y', tipY + 28);
            tip.style.display = '';
        });

        hoverArea.addEventListener('mouseleave', function () {
            crosshair.style.display = 'none';
            tip.style.display = 'none';
        });
    }

    function showFinRowChart(label, cells, columns) {
        var pairs = (columns || []).map(function (col, i) {
            var raw = (cells || [])[i] || '';
            var num = parseFinVal(raw);
            return { col: col, raw: raw || '—', num: num };
        }).filter(function (p) { return p.num !== null; }).reverse();

        var existing = document.getElementById('finChartModal');
        if (existing) { existing.remove(); }

        var modal = document.createElement('div');
        modal.id = 'finChartModal';
        modal.className = 'fin-chart-modal';
        modal.innerHTML =
            '<div class="fin-chart-panel">' +
            '<div class="fin-chart-hdr">' +
            '<div class="fin-chart-hdr-left">' +
            '<div class="fin-chart-kicker">HISTORICAL DATA</div>' +
            '<div class="fin-chart-lbl">' + esc(label) + '</div>' +
            '</div>' +
            '<button class="fin-chart-close" aria-label="Close">&#x2715;</button>' +
            '</div>' +
            '<div class="fin-chart-body">' +
            (pairs.length >= 2 ? renderBarChart(pairs) : '<div class="detail-empty">Not enough data to chart</div>') +
            '</div>' +
            '</div>';

        document.body.appendChild(modal);

        // Close handlers
        modal.querySelector('.fin-chart-close').addEventListener('click', function () { modal.remove(); });
        modal.addEventListener('click', function (e) { if (e.target === modal) { modal.remove(); } });

        // Tooltip setup
        setupBarChartTooltip();
    }

    function setupFinTableEvents(panel) {
        if (!panel) { return; }
        panel.querySelectorAll('tr.fn-chartable').forEach(function (row) {
            row.addEventListener('click', function () {
                var raw = row.getAttribute('data-fin');
                if (!raw) { return; }
                var data = JSON.parse(raw);
                var section = row.closest('.fin-block');
                var colsRaw = section && section.getAttribute('data-fin-cols');
                var columns = colsRaw ? JSON.parse(colsRaw) : [];
                showFinRowChart(data.label, data.cells, columns);
            });
        });
    }

    // ── Description block ─────────────────────────────────────────────────

    function renderDescription(text) {
        if (!text) { return ''; }
        return '<section class="research-card company-narrative">' +
            '<div class="card-header"><div><div class="card-kicker">COMPANY</div>' +
            '<div class="card-title">BUSINESS DESCRIPTION</div></div></div>' +
            '<div class="company-description">' + esc(text) + '</div></section>';
    }

    // ── Company facts ─────────────────────────────────────────────────────

    function renderFacts(profile) {
        if (!profile) { return ''; }
        var website = profile.url || '';
        var websiteHref = website && /^https?:\/\//i.test(website) ? website
                        : website ? 'https://' + website : '';
        var pairs = [
            ['EMPLOYEES', profile.employees
                ? profile.employees + (profile.employeesDate ? ' (' + profile.employeesDate + ')' : '')
                : null],
            ['ADDRESS', profile.address
                ? profile.address + (profile.country ? ', ' + profile.country : '')
                : null],
            ['WEBSITE', website || null, websiteHref],
            ['PHONE', profile.phone || null],
            ['SECTOR / INDUSTRY', profile.sector
                ? profile.sector + (profile.industry ? ' / ' + profile.industry : '')
                : null],
            ['FISCAL YEAR END', profile.fiscalYearEnd || null],
        ].filter(function (p) { return p[1]; });

        if (!pairs.length) { return ''; }
        return '<section class="research-card"><div class="card-header">' +
            '<div><div class="card-kicker">PROFILE</div><div class="card-title">COMPANY FACTS</div></div>' +
            '</div><div class="detail-facts">' +
            pairs.map(function (p) {
                var value = p[2]
                    ? '<a href="' + esc(p[2]) + '" target="_blank" rel="noopener">' + esc(p[1]) + '</a>'
                    : esc(p[1]);
                return '<div class="fact"><div class="fact-label">' + esc(p[0]) + '</div>' +
                       '<div class="fact-value">' + value + '</div></div>';
            }).join('') + '</div></section>';
    }

    // ── ESG renderer ──────────────────────────────────────────────────────

    function renderFactGrid(title, fields) {
        var visible = fields.filter(function (field) {
            return field[1] !== null && field[1] !== undefined && field[1] !== '';
        });
        if (!visible.length) { return ''; }
        return '<section class="research-card"><div class="card-header">' +
            '<div><div class="card-kicker">DATASET</div><div class="card-title">' + esc(title) + '</div></div>' +
            '</div><div class="detail-facts">' +
            visible.map(function (field) {
                return '<div class="fact"><div class="fact-label">' + esc(field[0]) + '</div>' +
                    '<div class="fact-value">' + esc(String(field[1])) + '</div></div>';
            }).join('') + '</div></section>';
    }

    // ── ESG score donut ───────────────────────────────────────────────────

    function esgScoreBadge(score, label, className) {
        var num = parseFloat(score);
        var display = isFinite(num) ? num.toFixed(1) : (score || '—');
        return '<div class="esg-score-badge ' + (className || '') + '">' +
            '<div class="esg-score-num">' + esc(display) + '</div>' +
            '<div class="esg-score-lbl">' + esc(label) + '</div>' +
            '</div>';
    }

    function esgRiskLevel(score) {
        var num = parseFloat(score);
        if (!isFinite(num)) { return { label: '—', cls: '' }; }
        if (num < 10)  { return { label: 'NEGLIGIBLE', cls: 'esg-level-negligible' }; }
        if (num < 20)  { return { label: 'LOW', cls: 'esg-level-low' }; }
        if (num < 30)  { return { label: 'MEDIUM', cls: 'esg-level-medium' }; }
        if (num < 40)  { return { label: 'HIGH', cls: 'esg-level-high' }; }
        return { label: 'SEVERE', cls: 'esg-level-severe' };
    }

    function renderEsgScoreCard(esgRisk, sustainability) {
        var score = (esgRisk && esgRisk.score) || (sustainability && sustainability.esgRiskScore);
        var env   = sustainability && sustainability.companyExposureScore || (esgRisk && esgRisk.environmentScore);
        var soc   = esgRisk && esgRisk.socialScore;
        var gov   = esgRisk && esgRisk.governanceScore;
        var category = (esgRisk && (esgRisk.category || esgRisk.esgPerformance)) ||
                       (sustainability && sustainability.esgRiskCategory);
        var level = esgRiskLevel(score);
        var mgmt  = sustainability && sustainability.overallManagementScore;
        var source = (esgRisk && esgRisk.source) || 'Morningstar Sustainalytics';

        var html = '<section class="research-card esg-overview-card">' +
            '<div class="card-header">' +
            '<div><div class="card-kicker">MORNINGSTAR SUSTAINALYTICS</div>' +
            '<div class="card-title">ESG RISK OVERVIEW</div></div>' +
            '<div class="card-meta">' + esc(source) + '</div>' +
            '</div>' +
            '<div class="esg-scores-row">';

        if (score) {
            html += '<div class="esg-main-score">' +
                '<div class="esg-main-num ' + level.cls + '">' + esc(parseFloat(score).toFixed(1)) + '</div>' +
                '<div class="esg-main-label">Total ESG Risk Score</div>' +
                (level.label ? '<div class="esg-risk-pill ' + level.cls + '">' + esc(level.label) + '</div>' : '') +
                (category ? '<div class="esg-category-tag">' + esc(category) + '</div>' : '') +
                '</div>';
        }

        html += '<div class="esg-pillars">';
        if (env) { html += esgScoreBadge(env, 'Environmental', 'esg-env'); }
        if (soc) { html += esgScoreBadge(soc, 'Social', 'esg-soc'); }
        if (gov) { html += esgScoreBadge(gov, 'Governance', 'esg-gov'); }
        if (mgmt) { html += esgScoreBadge(mgmt, 'Management Score', 'esg-mgmt'); }

        html += '</div></div></section>';
        return html;
    }

    function renderEsgPeers(sustainability) {
        var peers = sustainability && sustainability.peers;
        if (!Array.isArray(peers) || !peers.length) { return ''; }
        var current = {
            name: sustainability.companyName,
            esgRiskScore: sustainability.esgRiskScore,
            esgRiskCategory: sustainability.esgRiskCategory,
            companyExposureScore: sustainability.companyExposureScore,
            companyExposureCategory: sustainability.companyExposureCategory,
            overallManagementScore: sustainability.overallManagementScore,
            overallManagementCategory: sustainability.overallManagementCategory,
            neglectedRisk: sustainability.neglectedRisk,
            neglectedRiskPer: sustainability.neglectedRiskPer,
            subindustryExposureScore: sustainability.subindustryExposureScore,
            subindustryExposureCategory: sustainability.subindustryExposureCategory,
        };
        var rows = [current].concat(peers).map(function (peer, index) {
            return '<tr class="fn-row' + (index === 0 ? ' esg-current' : '') + '">' +
                '<td class="fn-label ownership-name">' + displayValue(peer.name) + '</td>' +
                '<td class="fn-cell fn-num">' + displayValue(peer.esgRiskScore) + '</td>' +
                '<td class="fn-cell ownership-text">' + displayValue(peer.esgRiskCategory) + '</td>' +
                '<td class="fn-cell fn-num">' + displayValue(peer.companyExposureScore) + '</td>' +
                '<td class="fn-cell ownership-text">' + displayValue(peer.companyExposureCategory) + '</td>' +
                '<td class="fn-cell fn-num">' + displayValue(peer.overallManagementScore) + '</td>' +
                '<td class="fn-cell ownership-text">' + displayValue(peer.overallManagementCategory) + '</td>' +
                '</tr>';
        }).join('');
        return '<section class="research-card fin-block"><div class="card-header">' +
            '<div><div class="card-kicker">BENCHMARKING</div>' +
            '<div class="card-title">ESG PEER COMPARISON</div></div>' +
            '<div class="card-meta">' + esc((peers.length + 1) + ' COMPANIES') + '</div></div>' +
            '<div class="fin-scroll"><table class="fin-table ownership-table">' +
            '<thead><tr>' +
            '<th class="fn-th fn-indicator">COMPANY</th>' +
            '<th class="fn-th fn-num">ESG RISK</th>' +
            '<th class="fn-th">RISK CATEGORY</th>' +
            '<th class="fn-th fn-num">EXPOSURE</th>' +
            '<th class="fn-th">EXPOSURE CATEGORY</th>' +
            '<th class="fn-th fn-num">MANAGEMENT</th>' +
            '<th class="fn-th">MANAGEMENT CATEGORY</th>' +
            '</tr></thead><tbody>' + rows + '</tbody></table></div></section>';
    }

    function renderEsg(esgRisk, sustainability) {
        var hasData = (esgRisk && typeof esgRisk === 'object') ||
                      (sustainability && typeof sustainability === 'object');
        if (!hasData) {
            return '<div class="esg-nodata">' +
                '<div class="esg-nodata-icon">◎</div>' +
                '<div class="esg-nodata-title">ESG DATA UNAVAILABLE</div>' +
                '<div class="esg-nodata-sub">Morningstar Sustainalytics did not return ESG scores for this security.</div>' +
                '</div>';
        }

        var html = renderEsgScoreCard(esgRisk, sustainability);

        // Morningstar-style details
        if (esgRisk && typeof esgRisk === 'object' && !esgRisk.source) {
            var detailFields = [
                ['RATING GLOBES', esgRisk.globes],
                ['SUB-INDUSTRY', esgRisk.subIndustry],
                ['AS OF DATE', esgRisk.asOfDate],
                ['CONTROVERSY LEVEL', esgRisk.controversyLevel],
                ['CONTROVERSY SEVERITY', esgRisk.controversyDescriptor],
                ['CONTROVERSY TOPICS', esgRisk.controversyTopics],
                ['CONTROVERSY AS OF', esgRisk.controversyAsOfDate],
            ].filter(function (f) { return f[1]; });
            if (detailFields.length) {
                html += renderFactGrid('CONTROVERSY & DETAIL', detailFields);
            }
            if (Array.isArray(esgRisk.notableIssues) && esgRisk.notableIssues.length) {
                html += '<section class="research-card"><div class="card-header">' +
                    '<div><div class="card-kicker">SUSTAINALYTICS</div>' +
                    '<div class="card-title">MATERIAL ESG ISSUES</div></div></div>' +
                    '<div class="esg-issues">' +
                    esgRisk.notableIssues.map(function (issue) {
                        return '<div class="esg-issue"><div class="fact-label">' +
                            displayValue(issue.scope) + '</div><div class="esg-issue-name">' +
                            displayValue(issue.issue) + '</div></div>';
                    }).join('') + '</div></section>';
            }
        }

        if (sustainability && typeof sustainability === 'object') {
            html += renderFactGrid('SUSTAINABILITY MANAGEMENT', [
                ['COMPANY', sustainability.companyName],
                ['MANAGEMENT SCORE', sustainability.overallManagementScore],
                ['MANAGEMENT CATEGORY', sustainability.overallManagementCategory],
                ['CONTROLLABLE RISK', sustainability.controllableRisk],
                ['CONTROLLED RISK', sustainability.controlledRisk],
                ['CONTROLLED RISK %', sustainability.controlledRiskPer],
                ['NEGLECTED RISK', sustainability.neglectedRisk],
                ['NEGLECTED RISK %', sustainability.neglectedRiskPer],
                ['UNCONTROLLABLE RISK', sustainability.uncontrollableRisk],
                ['AS OF DATE', sustainability.asOfDate],
            ]);
            html += renderEsgPeers(sustainability);
        }

        return html;
    }

    function renderClimateTable(table, title) {
        if (!table || !Array.isArray(table.rows) || !table.rows.length) { return ''; }
        var columns = table.columns || [];
        var head = columns.map(function (column, index) {
            return '<th class="fn-th ' + (index === 0 ? 'fn-indicator' : 'fn-num') + '">' + esc(column) + '</th>';
        }).join('');
        var rows = table.rows.map(function (row) {
            return '<tr class="fn-row">' + columns.map(function (_column, index) {
                var cell = row[index] || '';
                return '<td class="' + (index === 0 ? 'fn-label' : 'fn-cell') + '">' +
                    (cell ? esc(cell) : '—') + '</td>';
            }).join('') + '</tr>';
        }).join('');
        return '<section class="research-card fin-block">' +
            '<div class="qc-card-header">' +
            '<div><div class="qc-card-kicker">TRACENABLE</div>' +
            '<div class="qc-card-title">' + esc(title) + '</div></div>' +
            '<div class="qc-card-meta">' + esc(table.rows.length + ' ROWS') + '</div></div>' +
            '<div class="fin-scroll"><table class="fin-table">' +
            '<thead><tr>' + head + '</tr></thead><tbody>' + rows + '</tbody></table></div></section>';
    }

    function renderClimate(climateData) {
        if (!climateData || climateData.status !== 'FOUND') {
            return '<div class="esg-nodata">' +
                '<div class="esg-nodata-icon">◍</div>' +
                '<div class="esg-nodata-title">CLIMATE DATA UNAVAILABLE</div>' +
                '<div class="esg-nodata-sub">Tracenable did not return public climate rows for this company.</div>' +
                '</div>';
        }
        var tabs = climateData.tabs || {};
        var summary = climateData.summary || {};
        var summaryCards = metricCards('TRACENABLE COVERAGE', [
            { label: 'GHG emissions', value: summary.ghgEmissions, display: summary.ghgEmissions },
            { label: 'Targets', value: summary.climateTargets, display: summary.climateTargets },
            { label: 'EU taxonomy', value: summary.euTaxonomy, display: summary.euTaxonomy },
            { label: 'Energy', value: summary.energyManagement, display: summary.energyManagement },
            { label: 'Waste', value: summary.wasteManagement, display: summary.wasteManagement },
        ]);
        return summaryCards +
            renderClimateTable(tabs.ghgEmissions, 'GHG EMISSIONS') +
            renderClimateTable(tabs.climateTargets, 'CLIMATE TARGETS') +
            renderClimateTable(tabs.euTaxonomy, 'EU TAXONOMY') +
            renderClimateTable(tabs.energyManagement, 'ENERGY MANAGEMENT') +
            renderClimateTable(tabs.wasteManagement, 'WASTE MANAGEMENT');
    }

    // ── Ownership / people ────────────────────────────────────────────────

    function renderPeopleTable(people, title) {
        if (!people || !people.length) { return ''; }
        var rows = people.map(function (p) {
            var salary = p.salaryDisplay || '';
            var total  = p.totalCompensationDisplay || '';
            return '<tr class="fn-row">' +
                '<td class="fn-label ownership-name">' + esc(p.name) + '</td>' +
                '<td class="fn-cell ownership-text">' + displayValue(p.title) + '</td>' +
                '<td class="fn-cell">' + displayValue(p.age) + '</td>' +
                '<td class="fn-cell">' + displayValue(p.memberSince) + '</td>' +
                '<td class="fn-cell">' + displayValue(salary) +
                    (salary && p.salaryPeriod ? '<span class="cell-period">' + esc(p.salaryPeriod) + '</span>' : '') +
                '</td>' +
                '<td class="fn-cell">' + displayValue(total) +
                    (total && p.compensationPeriod ? '<span class="cell-period">' + esc(p.compensationPeriod) + '</span>' : '') +
                '</td></tr>';
        }).join('');
        return '<section class="research-card fin-block"><div class="card-header">' +
            '<div><div class="card-kicker">PEOPLE</div><div class="card-title">' + esc(title) + '</div></div>' +
            '<div class="card-meta">' + esc(people.length + ' MEMBERS') + '</div></div>' +
            '<div class="fin-scroll"><table class="fin-table ownership-table">' +
            '<thead><tr>' +
            '<th class="fn-th fn-indicator">NAME</th>' +
            '<th class="fn-th">TITLE</th>' +
            '<th class="fn-th fn-num">AGE</th>' +
            '<th class="fn-th fn-num">SINCE</th>' +
            '<th class="fn-th fn-num">SALARY</th>' +
            '<th class="fn-th fn-num">TOTAL COMP</th>' +
            '</tr></thead><tbody>' + rows + '</tbody></table></div></section>';
    }

    function renderInstitutions(institutions, title, direction) {
        if (!institutions || !institutions.length) { return ''; }
        var definitions = [
            ['name', 'INSTITUTION', 'label'],
            ['ticker', 'TICKER', 'text'],
            ['securityType', 'TYPE', 'text'],
            ['totalSharesHeld', 'SHARES HELD %', 'number'],
            ['totalAssets', 'TOTAL ASSETS %', 'number'],
            ['currentShares', 'CURRENT SHARES', 'number'],
            ['changeAmount', 'CHANGE', 'change'],
            ['changePercentage', 'CHANGE %', 'change'],
            ['trend', 'TREND', 'text'],
            ['starRating', 'STAR RATING', 'number'],
            ['domicileCountryId', 'DOMICILE', 'text'],
            ['date', 'DATE', 'number'],
        ];
        var columns = definitions.filter(function (definition) {
            return definition[0] === 'name' || institutions.some(function (institution) {
                return institution[definition[0]] !== null &&
                    institution[definition[0]] !== undefined &&
                    institution[definition[0]] !== '';
            });
        });
        var rows = institutions.map(function (i) {
            var cls = direction === 'buyer' ? 'fn-pos' : 'fn-neg';
            return '<tr class="fn-row">' + columns.map(function (column) {
                var type = column[2];
                if (type === 'label') {
                    return '<td class="fn-label ownership-name">' + displayValue(i[column[0]]) + '</td>';
                }
                var cellClass = type === 'text' ? ' ownership-text' : '';
                if (type === 'change') { cellClass += ' ' + cls; }
                return '<td class="fn-cell' + cellClass + '">' +
                    displayValue(i[column[0]]) + '</td>';
            }).join('') + '</tr>';
        }).join('');
        return '<section class="research-card fin-block"><div class="card-header">' +
            '<div><div class="card-kicker">INSTITUTIONAL FLOW</div><div class="card-title">' + esc(title) + '</div></div>' +
            '<div class="card-meta">' + esc(institutions.length + ' POSITIONS') + '</div></div>' +
            '<div class="fin-scroll"><table class="fin-table ownership-table">' +
            '<thead><tr>' + columns.map(function (column) {
                return '<th class="fn-th ' +
                    (column[2] === 'label' ? 'fn-indicator' :
                        column[2] === 'text' ? '' : 'fn-num') +
                    '">' + esc(column[1]) + '</th>';
            }).join('') + '</tr></thead><tbody>' + rows +
            '</tbody></table></div></section>';
    }

    function renderReportSection(title, header, text, cls) {
        if (!text) { return ''; }
        return '<section class="analyst-section ' + (cls || '') + '">' +
            '<div class="analyst-section-title">' + esc(title) + '</div>' +
            (header ? '<div class="analyst-section-header">' + esc(header) + '</div>' : '') +
            '<div class="analyst-copy">' + esc(text) + '</div></section>';
    }

    function renderAnalysisReport(report) {
        if (!report) { return ''; }
        var ratings = [
            ['ECONOMIC MOAT', report.economicMoatRating],
            ['VALUATION',     report.valuationRating],
            ['MANAGEMENT',    report.managementRating],
            ['RISK',          report.riskRating],
        ].filter(function (i) { return i[1]; });
        var meta = [report.author, report.authorTitle, report.publishDate,
                    report.isQuan ? 'QUANTITATIVE' : null].filter(Boolean).join(' · ');
        var html = '<div class="analyst-report"><div class="fin-title">MORNINGSTAR ANALYST REPORT</div>' +
            (report.headline ? '<div class="analyst-headline">' + esc(report.headline) + '</div>' : '') +
            (meta ? '<div class="analyst-meta">' + esc(meta) + '</div>' : '');
        if (ratings.length) {
            html += '<div class="analyst-ratings">' + ratings.map(function (i) {
                return '<div class="analyst-rating"><span>' + esc(i[0]) + '</span><strong>' + esc(i[1]) + '</strong></div>';
            }).join('') + '</div>';
        }
        html += renderReportSection('INVESTMENT THESIS', '', report.investmentThesis);
        html += renderReportSection('ECONOMIC MOAT', report.economicMoatHeader, report.economicMoat);
        html += renderReportSection('VALUATION', report.valuationHeader, report.valuation);
        html += renderReportSection('RISK', report.riskHeader, report.risk);
        html += renderReportSection('MANAGEMENT', report.managementHeader, report.management);
        if (report.bullsSay || report.bearsSay) {
            html += '<div class="analyst-cases">' +
                renderReportSection('BULLS SAY', '', report.bullsSay, 'analyst-bull') +
                renderReportSection('BEARS SAY', '', report.bearsSay, 'analyst-bear') +
                '</div>';
        }
        if (report.analystNote) {
            html += renderReportSection(
                report.analystNote.title || 'RECENT NOTE',
                [report.analystNote.author, report.analystNote.date].filter(Boolean).join(' · '),
                report.analystNote.text
            );
        }
        var hasContent = report.headline || ratings.length || report.investmentThesis ||
            report.economicMoat || report.valuation || report.risk || report.management ||
            report.bullsSay || report.bearsSay || report.analystNote;
        if (!hasContent) { html += '<div class="detail-empty">MORNINGSTAR ANALYST REPORT UNAVAILABLE</div>'; }
        return html + '</div>';
    }

    // ── Quantitative analysis ─────────────────────────────────────────────

    function periodSeries(series, period) {
        var limits = { '1M': 21, '3M': 63, '6M': 126, '1Y': 252, '3Y': 756, '5Y': 1260 };
        var limit = limits[period];
        return limit ? series.slice(-limit) : series.slice();
    }

    function sampledSeries(series, maximum) {
        if (series.length <= maximum) { return series; }
        var step = Math.ceil(series.length / maximum);
        return series.filter(function (_, index) {
            return index % step === 0 || index === series.length - 1;
        });
    }

    function svgPath(series, key, width, height, padding, bounds) {
        var parts = [];
        var count = Math.max(series.length - 1, 1);
        series.forEach(function (point, index) {
            var value = point[key];
            if (value === null || value === undefined || !isFinite(Number(value))) { return; }
            var x = padding + index / count * (width - padding * 2);
            var y = padding + (bounds.max - Number(value)) /
                Math.max(bounds.max - bounds.min, 0.000001) * (height - padding * 2);
            parts.push((parts.length ? 'L' : 'M') + x.toFixed(2) + ',' + y.toFixed(2));
        });
        return parts.join(' ');
    }

    function chartBounds(series, keys) {
        var values = [];
        series.forEach(function (point) {
            keys.forEach(function (key) {
                if (point[key] === null || point[key] === undefined || point[key] === '') { return; }
                var value = Number(point[key]);
                if (isFinite(value)) { values.push(value); }
            });
        });
        if (!values.length) { return { min: 0, max: 1 }; }
        var min = Math.min.apply(null, values);
        var max = Math.max.apply(null, values);
        var margin = Math.max((max - min) * 0.08, Math.abs(max) * 0.01, 0.01);
        return { min: min - margin, max: max + margin };
    }

    function gridLines(width, height, padding, bounds, percentAxis) {
        var lines = '';
        for (var index = 0; index < 5; index++) {
            var ratio = index / 4;
            var y = padding + ratio * (height - padding * 2);
            var value = bounds.max - ratio * (bounds.max - bounds.min);
            var label = percentAxis ? (value * 100).toFixed(1) + '%' : formatNumber(value, 2);
            lines += '<line x1="' + padding + '" y1="' + y + '" x2="' + (width - padding) +
                '" y2="' + y + '" class="chart-grid-line"></line>' +
                '<text x="' + (padding - 8) + '" y="' + (y + 3) +
                '" text-anchor="end" class="chart-axis-label">' + esc(label) + '</text>';
        }
        return lines;
    }

    function chartDateLabels(series, width, height, padding) {
        if (!series.length) { return ''; }
        var indices = [0, Math.floor((series.length - 1) / 2), series.length - 1];
        return indices.map(function (index) {
            var x = padding + index / Math.max(series.length - 1, 1) * (width - padding * 2);
            return '<text x="' + x + '" y="' + (height - 7) +
                '" text-anchor="' + (index === 0 ? 'start' : index === series.length - 1 ? 'end' : 'middle') +
                '" class="chart-axis-label">' + esc(series[index].date) + '</text>';
        }).join('');
    }

    // ── SVG chart interactivity (crosshair + tooltip) ─────────────────────

    function setupSvgTooltip(containerEl, series, chartW, chartH, padding, valueKeys, isPercent) {
        if (!containerEl || !series || !series.length) { return; }
        var svg = containerEl.querySelector('svg');
        if (!svg) { return; }
        var n = series.length;
        var ns = 'http://www.w3.org/2000/svg';

        function mkEl(tag, attrs) {
            var el = document.createElementNS(ns, tag);
            Object.keys(attrs).forEach(function (k) { el.setAttribute(k, attrs[k]); });
            return el;
        }

        // Crosshair
        var ch = mkEl('line', { class: 'qt-crosshair', x1: 0, x2: 0, y1: padding, y2: chartH - padding });
        ch.style.display = 'none';
        svg.appendChild(ch);

        // Tooltip group
        var tg = document.createElementNS(ns, 'g');
        tg.setAttribute('class', 'qt-tooltip-group');
        tg.style.display = 'none';
        var tbg  = mkEl('rect',  { class: 'qt-tooltip-bg', rx: 3, x: 0, y: 0, width: 10, height: 38 });
        var tdt  = mkEl('text',  { class: 'qt-tdate', x: 0, y: 0 });
        var tvl  = mkEl('text',  { class: 'qt-tval',  x: 0, y: 0 });
        tg.appendChild(tbg); tg.appendChild(tdt); tg.appendChild(tvl);
        svg.appendChild(tg);

        // Dot marker
        var dot = mkEl('circle', { class: 'qt-dot', cx: 0, cy: 0, r: 4 });
        dot.style.display = 'none';
        svg.appendChild(dot);

        // Hover area (transparent rect over chart area)
        var ha = mkEl('rect', {
            x: padding, y: padding,
            width: chartW - padding * 2,
            height: chartH - padding * 2,
            fill: 'transparent',
        });
        ha.style.cursor = 'crosshair';
        svg.appendChild(ha);

        // Chart bounds for value → y conversion
        var vals = [];
        series.forEach(function (pt) {
            valueKeys.forEach(function (k) {
                var v = pt[k];
                if (v !== null && v !== undefined && isFinite(Number(v))) { vals.push(Number(v)); }
            });
        });
        var minV = vals.length ? Math.min.apply(null, vals) : 0;
        var maxV = vals.length ? Math.max.apply(null, vals) : 1;
        var margin = Math.max((maxV - minV) * 0.08, Math.abs(maxV) * 0.01, 0.01);
        minV -= margin; maxV += margin;

        function valToY(v) {
            return padding + (maxV - v) / Math.max(maxV - minV, 0.000001) * (chartH - padding * 2);
        }

        ha.addEventListener('mousemove', function (e) {
            var rect = svg.getBoundingClientRect();
            var scale = chartW / rect.width;
            var mx = (e.clientX - rect.left) * scale;
            var idx = Math.max(0, Math.min(n - 1, Math.round((mx - padding) / Math.max(1, chartW - 2 * padding) * (n - 1))));
            var pt  = series[idx];
            if (!pt) { return; }

            var cx = padding + idx / Math.max(n - 1, 1) * (chartW - 2 * padding);
            ch.setAttribute('x1', cx.toFixed(2)); ch.setAttribute('x2', cx.toFixed(2));
            ch.style.display = '';

            // Primary value (first key)
            var mainKey = valueKeys[0];
            var mainVal = pt[mainKey];

            // Dot
            if (mainVal !== null && mainVal !== undefined) {
                var dy = valToY(Number(mainVal));
                dot.setAttribute('cx', cx.toFixed(2)); dot.setAttribute('cy', dy.toFixed(2));
                dot.style.display = '';
            }

            // Tooltip text
            var dateStr = pt.date || '';
            var valParts = valueKeys.map(function (k) {
                var v = pt[k];
                if (v === null || v === undefined) { return null; }
                var fmt = isPercent ? ((Number(v) * 100).toFixed(2) + '%') : Number(v).toLocaleString(undefined, { maximumFractionDigits: 2, minimumFractionDigits: 2 });
                return k === mainKey ? fmt : k.replace('rolling', '').replace('Vol30', 'Vol·30d') + ': ' + fmt;
            }).filter(Boolean);

            tdt.textContent = dateStr;
            tvl.textContent = valParts.join('  ·  ');

            var tipW = Math.max(100, tvl.textContent.length * 6.5 + 16);
            var tipX = cx + 10;
            var tipY = padding + 8;
            if (tipX + tipW > chartW - padding) { tipX = cx - tipW - 10; }

            tbg.setAttribute('x', (tipX - 4).toFixed(1)); tbg.setAttribute('y', tipY.toFixed(1));
            tbg.setAttribute('width', tipW.toFixed(1)); tbg.setAttribute('height', '36');
            tdt.setAttribute('x', tipX.toFixed(1)); tdt.setAttribute('y', (tipY + 12).toFixed(1));
            tvl.setAttribute('x', tipX.toFixed(1)); tvl.setAttribute('y', (tipY + 26).toFixed(1));
            tg.style.display = '';
        });

        ha.addEventListener('mouseleave', function () {
            ch.style.display = 'none';
            tg.style.display = 'none';
            dot.style.display = 'none';
        });
    }

    function renderPriceChart(series, currency) {
        if (!series.length) { return '<div class="detail-empty">NO PRICE HISTORY</div>'; }
        var data = sampledSeries(series, 700);
        var width = 1120;
        var height = 380;
        var padding = 58;
        var bounds = chartBounds(data, ['close', 'ma20', 'ma50', 'ma200']);
        var closePath = svgPath(data, 'close', width, height, padding, bounds);
        var areaPath = closePath ?
            closePath + ' L' + (width - padding) + ',' + (height - padding) +
            ' L' + padding + ',' + (height - padding) + ' Z' : '';
        var zeroY = padding + (bounds.max) / Math.max(bounds.max - bounds.min, 0.000001) * (height - padding * 2);
        return '<div class="chart-legend">' +
            '<span><i class="legend-line legend-price"></i>PRICE</span>' +
            '<span><i class="legend-line legend-ma20"></i>MA 20</span>' +
            '<span><i class="legend-line legend-ma50"></i>MA 50</span>' +
            '<span><i class="legend-line legend-ma200"></i>MA 200</span>' +
            '<span class="chart-currency">' + esc(currency || '') + '</span></div>' +
            '<svg class="quant-chart" viewBox="0 0 ' + width + ' ' + height +
            '" role="img" aria-label="Price history">' +
            '<defs>' +
            '<linearGradient id="priceArea" x1="0" y1="0" x2="0" y2="1">' +
            '<stop offset="0%" stop-color="var(--color-accent)" stop-opacity=".18"></stop>' +
            '<stop offset="100%" stop-color="var(--color-accent)" stop-opacity="0"></stop>' +
            '</linearGradient>' +
            '<clipPath id="chartClip"><rect x="' + padding + '" y="' + padding + '" width="' + (width - padding * 2) + '" height="' + (height - padding * 2) + '"/></clipPath>' +
            '</defs>' +
            gridLines(width, height, padding, bounds, false) +
            '<g clip-path="url(#chartClip)">' +
            '<path d="' + areaPath + '" class="chart-price-area"></path>' +
            '<path d="' + svgPath(data, 'ma200', width, height, padding, bounds) + '" class="chart-line chart-ma200"></path>' +
            '<path d="' + svgPath(data, 'ma50', width, height, padding, bounds) + '" class="chart-line chart-ma50"></path>' +
            '<path d="' + svgPath(data, 'ma20', width, height, padding, bounds) + '" class="chart-line chart-ma20"></path>' +
            '<path d="' + closePath + '" class="chart-line chart-price"></path>' +
            '</g>' +
            chartDateLabels(data, width, height, padding) + '</svg>';
    }

    function renderRiskChart(series, key, title, className, percentAxis) {
        if (!series.length) { return ''; }
        var data = sampledSeries(series, 600);
        var width = 540;
        var height = 200;
        var padding = 48;
        var bounds = chartBounds(data, [key]);
        var path = svgPath(data, key, width, height, padding, bounds);
        var area = path ? path + ' L' + (width - padding) + ',' + (height - padding) +
            ' L' + padding + ',' + (height - padding) + ' Z' : '';
        return '<section class="research-card chart-card qc-risk"><div class="qc-card-header">' +
            '<div class="qc-card-kicker">TIME SERIES</div>' +
            '<div class="qc-card-title">' + esc(title) + '</div>' +
            '</div>' +
            '<svg class="quant-chart compact-chart" viewBox="0 0 ' + width + ' ' + height + '">' +
            '<defs><clipPath id="riskClip' + className + '"><rect x="' + padding + '" y="' + padding + '" width="' + (width - padding * 2) + '" height="' + (height - padding * 2) + '"/></clipPath></defs>' +
            gridLines(width, height, padding, bounds, percentAxis) +
            '<g clip-path="url(#riskClip' + className + ')">' +
            '<path d="' + area + '" class="chart-risk-area ' + className + '-area"></path>' +
            '<path d="' + path + '" class="chart-line ' + className + '"></path>' +
            '</g>' +
            chartDateLabels(data, width, height, padding) + '</svg></section>';
    }

    function renderRelativeChart(series, ticker, benchmarkTicker) {
        var available = series.filter(function (point) {
            return point.benchmarkBase100 !== null && point.benchmarkBase100 !== undefined;
        });
        if (available.length < 2) { return ''; }
        var data = sampledSeries(available, 600);
        var width = 1120;
        var height = 260;
        var padding = 54;
        var bounds = chartBounds(data, ['base100', 'benchmarkBase100']);
        return '<section class="research-card chart-card"><div class="qc-card-header">' +
            '<div><div class="qc-card-kicker">BENCHMARK COMPARISON</div><div class="qc-card-title">RELATIVE PERFORMANCE · BASE 100</div></div>' +
            '<div class="qc-card-meta">' + esc((ticker || 'ASSET') + ' vs ' + (benchmarkTicker || 'BENCHMARK')) + '</div></div>' +
            '<div class="chart-legend"><span><i class="legend-line legend-price"></i>' +
            esc(ticker || 'ASSET') + '</span><span><i class="legend-line legend-benchmark"></i>' +
            esc(benchmarkTicker || 'BENCHMARK') + '</span></div>' +
            '<svg class="quant-chart compact-chart relative-chart" viewBox="0 0 ' + width + ' ' + height + '">' +
            '<defs><clipPath id="relClip"><rect x="' + padding + '" y="' + padding + '" width="' + (width - padding * 2) + '" height="' + (height - padding * 2) + '"/></clipPath></defs>' +
            gridLines(width, height, padding, bounds, false) +
            '<g clip-path="url(#relClip)">' +
            '<path d="' + svgPath(data, 'base100', width, height, padding, bounds) + '" class="chart-line chart-price"></path>' +
            '<path d="' + svgPath(data, 'benchmarkBase100', width, height, padding, bounds) + '" class="chart-line chart-benchmark"></path>' +
            '</g>' +
            chartDateLabels(data, width, height, padding) + '</svg></section>';
    }

    // ── Quantitative KPI bar ──────────────────────────────────────────────

    function trendBadge(trend) {
        var cls = trend === 'Bullish' ? 'qkpi-badge-bull' : trend === 'Bearish' ? 'qkpi-badge-bear' : 'qkpi-badge-neutral';
        var icon = trend === 'Bullish' ? '▲' : trend === 'Bearish' ? '▼' : '◆';
        return '<span class="qkpi-badge ' + cls + '">' + icon + ' ' + esc(trend || 'NEUTRAL') + '</span>';
    }

    function qkpiCell(label, display, tone, note) {
        return '<div class="qkpi-cell">' +
            '<div class="qkpi-value ' + (tone || '') + '">' + esc(display || '—') + '</div>' +
            '<div class="qkpi-label">' + esc(label) + '</div>' +
            (note ? '<div class="qkpi-note">' + esc(note) + '</div>' : '') +
            '</div>';
    }

    function renderQuantKpiBar(quantitative) {
        var performance = quantitative.performance || {};
        var risk = quantitative.risk || {};
        var technical = quantitative.technical || {};
        var trend = technical.trend || 'Neutral';
        var oneDay = performance.oneDay;
        var html = '<div class="qkpi-bar">';

        // Left: trend badge + price block
        html += '<div class="qkpi-lead">';
        html += trendBadge(trend);
        html += '<div class="qkpi-obs">' + esc(quantitative.observations + ' OBSERVATIONS') + '</div>';
        html += '</div>';

        // Return pills
        html += '<div class="qkpi-returns">';
        var rets = [
            ['1D', oneDay],
            ['1M', performance.oneMonth],
            ['3M', performance.threeMonths],
            ['6M', performance.sixMonths],
            ['YTD', performance.ytd],
            ['1Y', performance.oneYear],
            ['3Y', performance.threeYears],
        ];
        rets.forEach(function (item) {
            var v = item[1];
            var fmt = v !== null && v !== undefined ? ((v * 100) >= 0 ? '+' : '') + (v * 100).toFixed(1) + '%' : '—';
            var cls = v === null || v === undefined ? '' : v > 0 ? 'qkpi-ret-pos' : v < 0 ? 'qkpi-ret-neg' : '';
            html += '<div class="qkpi-ret ' + cls + '"><span class="qkpi-ret-val">' + esc(fmt) + '</span><span class="qkpi-ret-lbl">' + esc(item[0]) + '</span></div>';
        });
        html += '</div>';

        // Key risk indicators
        html += '<div class="qkpi-risk-strip">';
        html += qkpiCell('SHARPE', formatNumber(risk.sharpeRatio), valueTone(risk.sharpeRatio) === 'metric-positive' ? 'qkpi-pos' : valueTone(risk.sharpeRatio) === 'metric-negative' ? 'qkpi-neg' : '');
        html += qkpiCell('MAX DD', formatPercent(risk.maxDrawdown), 'qkpi-neg');
        html += qkpiCell('VOL (ANN.)', formatPercent(risk.annualizedVolatility), '');
        html += qkpiCell('RSI 14', formatNumber(technical.rsi14, 1), '');
        html += '</div>';
        html += '</div>';
        return html;
    }

    // ── Quantitative metric section cards ─────────────────────────────────

    function qMetricRow(label, display, tone, note, sub) {
        return '<div class="qm-row ' + (tone || '') + '">' +
            '<div class="qm-label">' + esc(label) + (sub ? '<span class="qm-sub">' + esc(sub) + '</span>' : '') + '</div>' +
            '<div class="qm-value">' + esc(display || '—') + '</div>' +
            (note ? '<div class="qm-note">' + esc(note) + '</div>' : '') +
            '</div>';
    }

    function qSection(kicker, title, rows) {
        return '<div class="qs-card">' +
            '<div class="qs-head"><div class="qs-kicker">' + esc(kicker) + '</div>' +
            '<div class="qs-title">' + esc(title) + '</div></div>' +
            '<div class="qs-body">' + rows + '</div>' +
            '</div>';
    }

    function renderQuantMetrics(quantitative) {
        var performance = quantitative.performance || {};
        var risk = quantitative.risk || {};
        var technical = quantitative.technical || {};
        var liquidity = quantitative.liquidity || {};
        var relative = quantitative.relative || {};

        // ── Performance section
        var perfRows =
            qMetricRow('1 Day', formatPercent(performance.oneDay), valueTone(performance.oneDay) === 'metric-positive' ? 'qm-pos' : valueTone(performance.oneDay) === 'metric-negative' ? 'qm-neg' : '') +
            qMetricRow('1 Month', formatPercent(performance.oneMonth), valueTone(performance.oneMonth) === 'metric-positive' ? 'qm-pos' : valueTone(performance.oneMonth) === 'metric-negative' ? 'qm-neg' : '') +
            qMetricRow('3 Months', formatPercent(performance.threeMonths), valueTone(performance.threeMonths) === 'metric-positive' ? 'qm-pos' : valueTone(performance.threeMonths) === 'metric-negative' ? 'qm-neg' : '') +
            qMetricRow('6 Months', formatPercent(performance.sixMonths), valueTone(performance.sixMonths) === 'metric-positive' ? 'qm-pos' : valueTone(performance.sixMonths) === 'metric-negative' ? 'qm-neg' : '') +
            qMetricRow('YTD', formatPercent(performance.ytd), valueTone(performance.ytd) === 'metric-positive' ? 'qm-pos' : valueTone(performance.ytd) === 'metric-negative' ? 'qm-neg' : '') +
            qMetricRow('1 Year', formatPercent(performance.oneYear), valueTone(performance.oneYear) === 'metric-positive' ? 'qm-pos' : valueTone(performance.oneYear) === 'metric-negative' ? 'qm-neg' : '') +
            qMetricRow('3 Years', formatPercent(performance.threeYears), valueTone(performance.threeYears) === 'metric-positive' ? 'qm-pos' : valueTone(performance.threeYears) === 'metric-negative' ? 'qm-neg' : '') +
            qMetricRow('5 Years', formatPercent(performance.fiveYears), valueTone(performance.fiveYears) === 'metric-positive' ? 'qm-pos' : valueTone(performance.fiveYears) === 'metric-negative' ? 'qm-neg' : '') +
            qMetricRow('CAGR (Full Period)', formatPercent(performance.cagr), valueTone(performance.cagr) === 'metric-positive' ? 'qm-pos' : valueTone(performance.cagr) === 'metric-negative' ? 'qm-neg' : '', quantitative.startDate + ' → ' + quantitative.asOfDate);

        // ── Risk section
        var riskRows =
            qMetricRow('Annualized Volatility', formatPercent(risk.annualizedVolatility)) +
            qMetricRow('Downside Volatility', formatPercent(risk.downsideVolatility)) +
            qMetricRow('Sharpe Ratio', formatNumber(risk.sharpeRatio), valueTone(risk.sharpeRatio) === 'metric-positive' ? 'qm-pos' : valueTone(risk.sharpeRatio) === 'metric-negative' ? 'qm-neg' : '') +
            qMetricRow('Sortino Ratio', formatNumber(risk.sortinoRatio), valueTone(risk.sortinoRatio) === 'metric-positive' ? 'qm-pos' : valueTone(risk.sortinoRatio) === 'metric-negative' ? 'qm-neg' : '') +
            qMetricRow('Calmar Ratio', formatNumber(risk.calmarRatio), valueTone(risk.calmarRatio) === 'metric-positive' ? 'qm-pos' : valueTone(risk.calmarRatio) === 'metric-negative' ? 'qm-neg' : '') +
            '<div class="qm-divider"></div>' +
            qMetricRow('Max Drawdown', formatPercent(risk.maxDrawdown), 'qm-neg', risk.maxDrawdownStart && risk.maxDrawdownTrough ? risk.maxDrawdownStart + ' → ' + risk.maxDrawdownTrough : '') +
            qMetricRow('Current Drawdown', formatPercent(risk.currentDrawdown), risk.currentDrawdown < 0 ? 'qm-neg' : '') +
            qMetricRow('Recovery Date', risk.maxDrawdownRecovery || '—', risk.maxDrawdownRecovery ? 'qm-pos' : 'qm-dim') +
            '<div class="qm-divider"></div>' +
            qMetricRow('VaR 95% (1D)', formatPercent(risk.var95), 'qm-neg') +
            qMetricRow('CVaR 95% (1D)', formatPercent(risk.cvar95), 'qm-neg') +
            qMetricRow('VaR 99% (1D)', formatPercent(risk.var99), 'qm-neg') +
            qMetricRow('CVaR 99% (1D)', formatPercent(risk.cvar99), 'qm-neg') +
            '<div class="qm-divider"></div>' +
            qMetricRow('Best Day', formatPercent(risk.bestDay), 'qm-pos') +
            qMetricRow('Worst Day', formatPercent(risk.worstDay), 'qm-neg') +
            qMetricRow('Positive Days %', formatPercent(risk.positiveDays)) +
            qMetricRow('Skewness', formatNumber(risk.skewness)) +
            qMetricRow('Excess Kurtosis', formatNumber(risk.excessKurtosis)) +
            qMetricRow('1D Autocorrelation', formatNumber(risk.autocorrelation1D));

        // ── Technical section
        var trend = technical.trend || 'Neutral';
        var trendClass = trend === 'Bullish' ? 'qm-pos' : trend === 'Bearish' ? 'qm-neg' : '';
        var techRows =
            qMetricRow('Trend Regime', trend, trendClass) +
            qMetricRow('RSI (14)', formatNumber(technical.rsi14, 1), technical.rsi14 >= 70 ? 'qm-neg' : technical.rsi14 <= 30 ? 'qm-pos' : '') +
            qMetricRow('ATR (14)', formatNumber(technical.atr14), '', formatPercent(technical.atr14Percent) + ' of price') +
            '<div class="qm-divider"></div>' +
            qMetricRow('MA 20', formatNumber(technical.ma20), '', '', 'vs ' + formatPercent(technical.distanceToMa20)) +
            qMetricRow('MA 50', formatNumber(technical.ma50), '', '', 'vs ' + formatPercent(technical.distanceToMa50)) +
            qMetricRow('MA 200', formatNumber(technical.ma200), '', '', 'vs ' + formatPercent(technical.distanceToMa200)) +
            '<div class="qm-divider"></div>' +
            qMetricRow('52W High', formatNumber(technical.high52Week), '', '', formatPercent(technical.distanceToHigh52Week) + ' away') +
            qMetricRow('52W Low', formatNumber(technical.low52Week), '', '', formatPercent(technical.distanceToLow52Week) + ' away') +
            '<div class="qm-divider"></div>' +
            qMetricRow('Latest Volume', formatNumber(liquidity.latestVolume, 0)) +
            qMetricRow('Avg Volume (20D)', formatNumber(liquidity.averageVolume20, 0)) +
            qMetricRow('Relative Volume', liquidity.relativeVolume != null ? formatNumber(liquidity.relativeVolume) + 'x' : '—', liquidity.relativeVolume > 2 ? 'qm-pos' : '');

        // ── Benchmark / relative section
        var relRows =
            qMetricRow('Beta', formatNumber(relative.beta)) +
            qMetricRow('Alpha (Annual)', formatPercent(relative.alpha), valueTone(relative.alpha) === 'metric-positive' ? 'qm-pos' : valueTone(relative.alpha) === 'metric-negative' ? 'qm-neg' : '') +
            qMetricRow('Correlation', formatNumber(relative.correlation)) +
            qMetricRow('Tracking Error', formatPercent(relative.trackingError)) +
            qMetricRow('Information Ratio', formatNumber(relative.informationRatio), valueTone(relative.informationRatio) === 'metric-positive' ? 'qm-pos' : valueTone(relative.informationRatio) === 'metric-negative' ? 'qm-neg' : '') +
            '<div class="qm-divider"></div>' +
            qMetricRow('Asset Return (1Y)', formatPercent(relative.assetReturn1Y), valueTone(relative.assetReturn1Y) === 'metric-positive' ? 'qm-pos' : valueTone(relative.assetReturn1Y) === 'metric-negative' ? 'qm-neg' : '') +
            qMetricRow('Benchmark Return (1Y)', formatPercent(relative.benchmarkReturn1Y), valueTone(relative.benchmarkReturn1Y) === 'metric-positive' ? 'qm-pos' : valueTone(relative.benchmarkReturn1Y) === 'metric-negative' ? 'qm-neg' : '') +
            qMetricRow('Outperformance (1Y)', formatPercent(relative.outperformance1Y), valueTone(relative.outperformance1Y) === 'metric-positive' ? 'qm-pos' : valueTone(relative.outperformance1Y) === 'metric-negative' ? 'qm-neg' : '') +
            '<div class="qm-divider"></div>' +
            '<div class="qm-bench-tag">vs ' + esc(relative.benchmarkTicker || '—') + ' · RF ' + formatPercent(quantitative.riskFreeRate) + '</div>';

        return '<div class="qs-grid">' +
            qSection('RETURNS', 'PERFORMANCE', perfRows) +
            qSection('TAIL RISK & RATIOS', 'RISK METRICS', riskRows) +
            qSection('TREND & MOMENTUM', 'TECHNICAL', techRows) +
            qSection('BENCHMARK SENSITIVITY', 'RELATIVE RISK', relRows) +
            '</div>';
    }

    function renderQuantitative(quantitative) {
        if (!quantitative || !Array.isArray(quantitative.series) || !quantitative.series.length) {
            return panelShell(
                'MORNINGSTAR',
                'QUANTITATIVE ANALYSIS',
                'Price history and risk metrics require a Morningstar market series.',
                '<div class="detail-empty">NO QUANTITATIVE DATA AVAILABLE</div>'
            );
        }

        var periodBtns = '<div class="quant-periods">' +
            ['1M', '3M', '6M', '1Y', '3Y', '5Y', 'MAX'].map(function (period) {
                return '<button class="quant-period-btn' + (period === '1Y' ? ' active' : '') +
                    '" data-quant-period="' + period + '">' + period + '</button>';
            }).join('') + '</div>';

        var chartSection =
            '<section class="research-card chart-card primary-chart-card">' +
            '<div class="qc-card-header">' +
            '<div><div class="qc-card-kicker">MARKET PRICE</div><div class="qc-card-title">PRICE & MOVING AVERAGES</div></div>' +
            '<div class="qc-card-meta">' + esc(quantitative.ticker) + ' · ' + esc(quantitative.observations + ' obs') + '</div>' +
            '</div>' +
            periodBtns +
            '<div id="quantPriceChart"></div></section>' +
            '<div class="chart-pair">' +
            '<div id="quantDrawdownChart"></div>' +
            '<div id="quantVolatilityChart"></div>' +
            '</div>' +
            '<div id="quantRelativeChart"></div>';

        var content =
            renderQuantKpiBar(quantitative) +
            chartSection +
            renderQuantMetrics(quantitative);

        return panelShell(
            'MARKET INTELLIGENCE',
            'QUANTITATIVE ANALYSIS',
            'Returns, tail risk, drawdowns, trend and liquidity from Morningstar price history.',
            content,
            quantitative.asOfDate
        );
    }

    function updateQuantCharts(quantitative, period) {
        var series  = periodSeries(quantitative.series || [], period);
        var sampled = sampledSeries(series, 700);
        var price    = document.getElementById('quantPriceChart');
        var drawdown = document.getElementById('quantDrawdownChart');
        var vol      = document.getElementById('quantVolatilityChart');
        var relative = document.getElementById('quantRelativeChart');

        if (price) {
            price.innerHTML = renderPriceChart(series, quantitative.currency);
            setupSvgTooltip(price, sampled, 1120, 380, 58, ['close', 'ma20', 'ma50', 'ma200'], false);
        }
        if (drawdown) {
            drawdown.innerHTML = renderRiskChart(series, 'drawdown', 'DRAWDOWN', 'chart-drawdown', true);
            setupSvgTooltip(drawdown, sampledSeries(series, 600), 540, 200, 48, ['drawdown'], true);
        }
        if (vol) {
            vol.innerHTML = renderRiskChart(series, 'rollingVol30', 'ROLLING 30D VOLATILITY', 'chart-volatility', true);
            setupSvgTooltip(vol, sampledSeries(series, 600), 540, 200, 48, ['rollingVol30'], true);
        }
        if (relative) {
            relative.innerHTML = renderRelativeChart(series, quantitative.ticker, quantitative.benchmarkTicker);
            var relSampled = sampledSeries(series.filter(function (p) { return p.benchmarkBase100 != null; }), 600);
            if (relSampled.length >= 2) {
                setupSvgTooltip(relative, relSampled, 1120, 260, 54, ['base100', 'benchmarkBase100'], false);
            }
        }
    }

    function setupQuantitative(quantitative) {
        if (!quantitative || !quantitative.series) { return; }
        updateQuantCharts(quantitative, '1Y');
        document.querySelectorAll('[data-quant-period]').forEach(function (button) {
            button.addEventListener('click', function () {
                document.querySelectorAll('[data-quant-period]').forEach(function (item) {
                    item.classList.remove('active');
                });
                this.classList.add('active');
                updateQuantCharts(quantitative, this.dataset.quantPeriod);
            });
        });
    }

    // ── Star rating ───────────────────────────────────────────────────────

    function renderStars(rating) {
        var n = parseInt(rating, 10) || 0;
        if (!n) { return ''; }
        var s = '';
        for (var i = 1; i <= 5; i++) { s += i <= n ? '★' : '☆'; }
        return s;
    }

    // ── Main render ───────────────────────────────────────────────────────

    function render(asset) {
        var ov      = asset.overview       || {};
        var profile = asset.companyProfile || {};
        var buyersCount = (asset.institutionBuyers || []).length;
        var sellersCount = (asset.institutionSellers || []).length;
        var institutionsLabel = document.querySelector(
            '[data-tab="institutions"] .nav-label'
        );
        if (institutionsLabel) {
            institutionsLabel.textContent = 'BUY / SELL ' +
                buyersCount + ' / ' + sellersCount;
        }

        // Header
        var nameEl      = document.getElementById('dName');
        var idsEl       = document.getElementById('dIds');
        var sectorEl    = document.getElementById('dSector');
        var starsEl     = document.getElementById('dStars');
        var priceEl     = document.getElementById('dPrice');
        var priceLblEl  = document.getElementById('dPriceLabel');
        var fvEl        = document.getElementById('dFairValue');

        if (nameEl)    { nameEl.textContent   = ov.securityName || asset.name || secId; }
        if (idsEl)     { idsEl.textContent    = [asset.provider, asset.assetType, ov.ticker, ov.exchange, (asset.isin && asset.isin.length === 12) ? asset.isin : null].filter(Boolean).join(' · '); }
        if (sectorEl)  { sectorEl.textContent = [ov.sector, ov.industry].filter(Boolean).join(' / '); }
        if (starsEl)   { starsEl.textContent  = renderStars(ov.starRating); }

        if (ov.lastClose != null && priceEl) {
            priceEl.textContent = [ov.lastClose, ov.currency].filter(Boolean).join(' ');
            if (priceLblEl) {
                priceLblEl.textContent = [
                    ov.lastCloseDate,
                    ov.uncertainty && ov.uncertainty !== '_PO_' ? ov.uncertainty : null,
                ].filter(Boolean).join(' · ');
            }
        }
        if (ov.fairValue && ov.fairValue !== '_PO_' && fvEl) {
            fvEl.textContent = 'FAIR VALUE ' + ov.fairValue;
        }

        // ── OVERVIEW: description + facts + key metrics ───────────────────
        var overviewPanel = document.querySelector('[data-panel="overview"]');
        if (overviewPanel) {
            var overviewContent =
                renderDescription(profile.description) +
                renderFacts(profile) +
                renderFinTable(asset.keyMetrics, 'KEY METRICS');
            overviewPanel.innerHTML = panelShell(
                'COMPANY RESEARCH',
                'OVERVIEW',
                'Business profile, market positioning and the core operating metrics used to frame the investment case.',
                overviewContent || '<div class="detail-empty">NO DATA AVAILABLE</div>',
                [ov.ticker, ov.exchange, ov.currency].filter(Boolean).join(' · ')
            );
        }

        var quantitativePanel = document.querySelector('[data-panel="quantitative"]');
        if (quantitativePanel) {
            quantitativePanel.innerHTML = renderQuantitative(asset.quantitative);
        }

        // ── Financial tabs ────────────────────────────────────────────────
        [
            ['income',   asset.incomeStatement, 'INCOME STATEMENT',    'Revenue, margins and earnings progression across reported fiscal periods.'],
            ['balance',  asset.balanceSheet,    'BALANCE SHEET',       'Assets, liabilities, capital structure and balance-sheet resilience.'],
            ['cashflow', asset.cashFlow,        'CASH FLOW STATEMENT', 'Operating cash generation, investment requirements and financing flows.'],
            ['valuation',asset.valuation,       'VALUATION',           'Market multiples, historical valuation context and pricing indicators.'],
            ['dividends',asset.dividends,       'DIVIDENDS',           'Distribution history and shareholder income profile.'],
        ].forEach(function (row) {
            var panel = document.querySelector('[data-panel="' + row[0] + '"]');
            if (panel) {
                panel.innerHTML = panelShell(
                    'FINANCIAL STATEMENTS',
                    row[2],
                    row[3],
                    renderFinTable(row[1], row[2]) ||
                        '<div class="detail-empty">NO DATA AVAILABLE</div>',
                    'CLICK A ROW TO VIEW HISTORY'
                );
                setupFinTableEvents(panel);
            }
        });

        // ── Growth tab ────────────────────────────────────────────────────
        var growthPanel = document.querySelector('[data-panel="growth"]');
        if (growthPanel) {
            var growthContent =
                renderFinTable(asset.profitability,   'PROFITABILITY')   +
                renderFinTable(asset.operatingGrowth, 'OPERATING GROWTH')+
                renderFinTable(asset.financialHealth, 'FINANCIAL HEALTH')+
                renderFinTable(asset.freeCashFlow,    'FREE CASH FLOW');
            growthPanel.innerHTML = panelShell(
                'OPERATING QUALITY',
                'GROWTH AND RETURNS',
                'Profitability, operating momentum, financial health and free-cash-flow conversion.',
                growthContent || '<div class="detail-empty">NO DATA AVAILABLE</div>',
                'CLICK A ROW TO VIEW HISTORY'
            );
            setupFinTableEvents(growthPanel);
        }

        // ── ESG tab ───────────────────────────────────────────────────────
        var esgPanel = document.querySelector('[data-panel="esg"]');
        if (esgPanel) {
            esgPanel.innerHTML = panelShell(
                'MORNINGSTAR SUSTAINALYTICS',
                'ESG AND CONTROVERSIES',
                'Material environmental, social and governance exposure, risk management and peer positioning.',
                renderEsg(asset.esgRisk, asset.sustainability)
            );
        }

        var climatePanel = document.querySelector('[data-panel="climate"]');
        if (climatePanel) {
            climatePanel.innerHTML = panelShell(
                'TRACENABLE',
                'CLIMATE DATA',
                'GHG emissions, climate targets, EU taxonomy, energy management and waste management disclosed by Tracenable.',
                renderClimate(asset.climateData)
            );
        }

        // ── Management tab ────────────────────────────────────────────────
        var managementPanel = document.querySelector('[data-panel="management"]');
        if (managementPanel) {
            var managementContent =
                renderPeopleTable(asset.keyExecutives,    'KEY EXECUTIVES') +
                renderPeopleTable(asset.boardOfDirectors, 'BOARD OF DIRECTORS');
            managementPanel.innerHTML = panelShell(
                'LEADERSHIP',
                'MANAGEMENT AND GOVERNANCE',
                'Executive leadership, board composition, tenure and disclosed compensation.',
                managementContent ||
                    '<div class="detail-empty">NO MANAGEMENT DATA AVAILABLE</div>'
            );
        }

        // ── Institutions tab ──────────────────────────────────────────────
        var institutionsPanel = document.querySelector('[data-panel="institutions"]');
        if (institutionsPanel) {
            var ownershipContent =
                renderInstitutions(asset.institutionBuyers,   'TOP INSTITUTIONAL BUYERS',   'buyer') +
                renderInstitutions(asset.institutionSellers,  'TOP INSTITUTIONAL SELLERS',  'seller');
            institutionsPanel.innerHTML = panelShell(
                'OWNERSHIP FLOW',
                'INSTITUTIONAL BUYERS AND SELLERS',
                'Largest disclosed position increases and reductions reported by institutional investors.',
                ownershipContent ||
                    '<div class="detail-empty">NO INSTITUTIONAL DATA AVAILABLE</div>',
                buyersCount + ' BUYERS · ' + sellersCount + ' SELLERS'
            );
        }

        // ── Analysts tab ──────────────────────────────────────────────────
        var analystsPanel = document.querySelector('[data-panel="analysts"]');
        if (analystsPanel) {
            analystsPanel.innerHTML = panelShell(
                'MORNINGSTAR EQUITY RESEARCH',
                'ANALYST VIEW',
                'Investment thesis, economic moat, valuation, risk and bull/bear arguments.',
                renderAnalysisReport(asset.analysisReport) ||
                    '<div class="detail-empty">NO MORNINGSTAR ANALYST REPORT AVAILABLE</div>'
            );
        }

        // ── Show/hide tabs based on data availability ─────────────────────
        [
            ['income',       asset.incomeStatement],
            ['quantitative', true],
            ['balance',      asset.balanceSheet],
            ['cashflow',     asset.cashFlow],
            ['valuation',    asset.valuation],
            ['growth',       asset.profitability || asset.operatingGrowth || asset.financialHealth || asset.freeCashFlow],
            ['dividends',    asset.dividends],
            ['esg',          true],
            ['climate',      true],
            ['management',   (asset.keyExecutives && asset.keyExecutives.length) || (asset.boardOfDirectors && asset.boardOfDirectors.length)],
            ['institutions', true],
            ['analysts',     asset.analysisReport],
        ].forEach(function (row) {
            var btn = document.querySelector('[data-tab="' + row[0] + '"]');
            if (btn) { btn.style.display = row[1] ? '' : 'none'; }
        });

        hideEl(loadingEl);
        showEl(contentEl);
        setupQuantitative(asset.quantitative);
        setupTabs();
        setupSidebar();
    }

    // ── Fetch ─────────────────────────────────────────────────────────────

    fetch(apiUrl, { headers: { 'Accept': 'application/json' } })
        .then(function (r) {
            if (!r.ok) { throw new Error('HTTP ' + r.status); }
            return r.json();
        })
        .then(function (data) {
            if (data.error) { throw new Error(data.error); }
            render(data);
        })
        .catch(function (err) {
            hideEl(loadingEl);
            if (errorEl) {
                errorEl.textContent = err.message || 'Failed to load company data.';
                showEl(errorEl);
            }
        });
}());
