(function () {
    const ENTRY_OPTIONS = [25, 50, 100, Infinity];
    const STORAGE_PREFIX = 'data-table-state:';
    const ACTION_TEXT = /^(actions?|thao\s*tac)$/i;

    function text(cell) { return cell ? cell.textContent.trim().replace(/\s+/g, ' ') : ''; }
    function keyFor(table) { return table.dataset.tableKey || table._fallbackTableKey || null; }
    function storageKey(table) { return STORAGE_PREFIX + location.pathname + ':' + keyFor(table); }
    function hasMeaningfulText(cell) {
        if (!cell) return false;
        var txt = cell.textContent;
        return txt.replace(/\s+/g, '').length > 0;
    }
    function readState(table) {
        if (!keyFor(table)) return { search: '', filters: {} };
        try {
            var raw = JSON.parse(localStorage.getItem(storageKey(table)) || '{}');
            var valid = raw && typeof raw === 'object';
            var search = valid && typeof raw.search === 'string' ? raw.search : '';
            var filters = {};
            if (valid && raw.filters && typeof raw.filters === 'object' && !Array.isArray(raw.filters)) {
                for (var k in raw.filters) {
                    if (raw.filters.hasOwnProperty(k) && typeof raw.filters[k] === 'string' && raw.filters[k]) {
                        filters[k] = raw.filters[k];
                    }
                }
            }
            return { search: search, filters: filters };
        } catch (_) { return { search: '', filters: {} }; }
    }
    function saveState(table, state) {
        if (!keyFor(table)) return;
        try { localStorage.setItem(storageKey(table), JSON.stringify({ search: state.search, filters: state.filters })); } catch (_) {}
    }
    function emptyRow(row) { return row.querySelector('[colspan]') || row.cells.length === 0; }
    function isActionColumn(table, index, header) {
        var heading = text(header);
        if (!heading || ACTION_TEXT.test(heading)) return true;
        var rows = table._dataRows.slice(0, 20);
        if (!rows.length) return false;
        return rows.every(function (row) {
            var cell = row.cells[index];
            if (!cell) return true;
            if (cell.querySelector('input[type="checkbox"]')) return false;
            if (cell.querySelector(':scope > *:not(form):not(button):not(a)')) return false;
            return cell.querySelector('form, button, a') && !hasMeaningfulText(cell);
        });
    }
    function sortable(table, index, header) { return !isActionColumn(table, index, header) && !header.querySelector('input,select,button'); }
    function isLinkColumn(rows, index) {
        if (!rows.length) return false;
        return rows.slice(0, 20).every(function (row) {
            var cell = row.cells[index];
            return cell && cell.querySelectorAll('a').length > 0 && !hasMeaningfulText(cell);
        });
    }
    function filterColumns(table) {
        var rows = table._dataRows;
        return Array.from(table.tHead ? table.tHead.rows[0].cells : []).map(function (header, index) {
            if (isActionColumn(table, index, header) || isLinkColumn(rows, index)) return null;
            var raw = rows.slice(0, 20).map(function (row) { return text(row.cells[index]); });
            var distinct = [];
            raw.forEach(function (v) { if (distinct.indexOf(v) === -1) distinct.push(v); });
            var totalLen = 0; raw.forEach(function (v) { totalLen += v.length; });
            var average = raw.length ? totalLen / raw.length : 0;
            if (distinct.length < 2 || distinct.length > 10 || average > 80) return null;
            if (rows.length && rows.slice(0, 20).every(function (row) { var c = row.cells[index]; return c && c.querySelector('input[type="checkbox"]'); })) return null;
            if (isLinkColumn(rows, index)) return null;
            return { index: index, values: distinct };
        }).filter(Boolean);
    }
    function addOption(select, value, label) { var opt = document.createElement('option'); opt.value = value; opt.textContent = label || value; select.appendChild(opt); }
    function insertBefore(node, ref) {
        if (ref && ref.parentElement) { ref.parentElement.insertBefore(node, ref); return; }
        if (ref && ref.parentNode) { ref.parentNode.insertBefore(node, ref); return; }
        document.body.appendChild(node);
    }
    function makeToolbar(table, state) {
        var toolbar = document.createElement('div'); toolbar.className = 'data-table-toolbar';
        var search = document.createElement('input'); search.type = 'search'; search.placeholder = 'Search...'; search.setAttribute('aria-label', 'Search table'); search.value = state.search;
        var filters = document.createElement('div'); filters.className = 'data-table-filters';
        filterColumns(table).forEach(function (col) {
            var sel = document.createElement('select'); sel.dataset.column = col.index;
            sel.setAttribute('aria-label', text(table.tHead.rows[0].cells[col.index]));
            addOption(sel, '', 'All');
            col.values.slice().sort(function (a, b) { return a.localeCompare(b); }).forEach(function (v) { addOption(sel, v); });
            sel.value = state.filters[col.index] || '';
            filters.appendChild(sel);
        });
        var entries = document.createElement('select'); entries.setAttribute('aria-label', 'Rows per page');
        ENTRY_OPTIONS.forEach(function (v) { addOption(entries, String(v), v === Infinity ? 'All' : String(v)); });
        entries.value = '25';
        var count = document.createElement('span'); count.className = 'data-table-count';
        var pagination = document.createElement('div'); pagination.className = 'data-table-pagination pagination';
        toolbar.append(search, filters, entries, count, pagination);
        var wrapper = table.closest('.table-wrap');
        if (wrapper && wrapper.parentElement) { wrapper.parentElement.insertBefore(toolbar, wrapper); }
        else { table.parentElement.insertBefore(toolbar, table); }
        table._controls = { search: search, filters: filters, entries: entries, count: count, pagination: pagination };
        return toolbar;
    }
    function render(table) {
        var c = table._controls;
        var query = c.search.value.trim().toLowerCase();
        var selected = {};
        Array.from(c.filters.children).forEach(function (sel) { selected[sel.dataset.column] = sel.value; });
        var matches = table._dataRows.filter(function (row) {
            if (query && !text(row).toLowerCase().includes(query)) return false;
            for (var key in selected) {
                if (selected.hasOwnProperty(key) && selected[key]) {
                    if (text(row.cells[key]).toLowerCase() !== selected[key].toLowerCase()) return false;
                }
            }
            return true;
        });
        var perPage = Number(c.entries.value);
        var pages = perPage === Infinity ? 1 : Math.max(1, Math.ceil(matches.length / perPage));
        table._page = Math.min(table._page || 1, pages);
        var start = perPage === Infinity ? 0 : (table._page - 1) * perPage;
        var visible = new Set(matches.slice(start, perPage === Infinity ? undefined : start + perPage));
        table._dataRows.forEach(function (row) { row.hidden = !visible.has(row); });
        c.count.textContent = matches.length + ' result' + (matches.length === 1 ? '' : 's');
        c.pagination.replaceChildren();
        for (var p = 1; p <= pages; p++) {
            (function (page) {
                var btn = document.createElement('button'); btn.type = 'button';
                btn.className = 'pagination-link' + (page === table._page ? ' current' : '');
                btn.textContent = page; btn.disabled = page === table._page;
                btn.addEventListener('click', function () { table._page = page; render(table); });
                c.pagination.appendChild(btn);
            })(p);
        }
        if (keyFor(table)) saveState(table, { search: c.search.value, filters: selected });
    }
    function setup(table) {
        if (!table._dataRows) table._dataRows = Array.from(table.tBodies[0] ? table.tBodies[0].rows : []).filter(function (r) { return !emptyRow(r); });
        if (!table._controls) {
            var state = readState(table);
            makeToolbar(table, state);
            table._controls.search.addEventListener('input', function () { table._page = 1; render(table); });
            table._controls.filters.addEventListener('change', function () { table._page = 1; render(table); });
            table._controls.entries.addEventListener('change', function () { table._page = 1; render(table); });
            Array.from(table.tHead ? table.tHead.rows[0].cells : []).forEach(function (header, index) {
                if (sortable(table, index, header)) {
                    header.style.cursor = 'pointer';
                    header.addEventListener('click', function () {
                        var dir = (table._sort && table._sort.index === index && table._sort.direction === 1) ? -1 : 1;
                        table._sort = { index: index, direction: dir };
                        table._dataRows.sort(function (a, b) {
                            var av = text(a.cells[index]), bv = text(b.cells[index]);
                            var an = Number(av), bn = Number(bv);
                            if (Number.isFinite(an) && Number.isFinite(bn)) return (an - bn) * dir;
                            var ad = Date.parse(av), bd = Date.parse(bv);
                            if (!Number.isNaN(ad) && !Number.isNaN(bd)) return (ad - bd) * dir;
                            return av.localeCompare(bv, undefined, { sensitivity: 'base' }) * dir;
                        });
                        header.dataset.sort = dir === 1 ? 'asc' : 'desc';
                        table._dataRows.forEach(function (row) { table.tBodies[0].appendChild(row); });
                        render(table);
                    });
                }
            });
        }
        render(table);
    }
    function initDataTables(root) {
        if (root === undefined) root = document;
        Array.from(root.querySelectorAll('table.data-table')).forEach(function (table, index) {
            if (!table._fallbackTableKey) table._fallbackTableKey = String(index);
            setup(table);
        });
    }
    function refreshDataTable(table) {
        if (!table) return;
        table._dataRows = null;
        if (table._controls) {
            var tb = table._controls.search.closest('.data-table-toolbar');
            if (tb) tb.remove();
            table._controls = null;
        }
        setup(table);
    }
    window.DataTables = { init: initDataTables, refresh: refreshDataTable };
    document.addEventListener('DOMContentLoaded', function () { DataTables.init(); });
})();
