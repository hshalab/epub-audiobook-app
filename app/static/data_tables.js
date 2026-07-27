(function () {
    const ENTRY_OPTIONS = [25, 50, 100, Infinity];
    const STORAGE_PREFIX = 'data-table-state:';
    const ACTION_TEXT = /^(actions?|thao\s*tác)$/i;

    function text(cell) { return cell ? cell.textContent.trim().replace(/\s+/g, ' ') : ''; }
    function keyFor(table) { return table.dataset.tableKey || table._fallbackTableKey || null; }
    function storageKey(table) { return STORAGE_PREFIX + location.pathname + ':' + keyFor(table); }
    function readState(table) {
        if (!keyFor(table)) return { search: '', filters: {} };
        try {
            const value = JSON.parse(localStorage.getItem(storageKey(table)) || '{}');
            return value && typeof value === 'object' ? {
                search: typeof value.search === 'string' ? value.search : '',
                filters: value.filters && typeof value.filters === 'object' ? value.filters : {}
            } : { search: '', filters: {} };
        } catch (_) { return { search: '', filters: {} }; }
    }
    function saveState(table, state) {
        if (!keyFor(table)) return;
        try { localStorage.setItem(storageKey(table), JSON.stringify({ search: state.search, filters: state.filters })); } catch (_) { /* storage may be unavailable */ }
    }
    function emptyRow(row) { return row.querySelector('[colspan]') || row.cells.length === 0; }
    function actionColumn(table, index, header) {
        const heading = text(header);
        if (!heading || ACTION_TEXT.test(heading)) return true;
        const rows = table._dataRows.slice(0, 20);
        return rows.length > 0 && rows.every(row => {
            const cell = row.cells[index];
            return cell && !cell.querySelector('input[type="checkbox"]') &&
                cell.querySelector('form, button, a') &&
                !cell.querySelector(':scope > *:not(form):not(button):not(a)') &&
                !cell.textContent.replace(/\s+/g, '').replace(/[\u00a0]/g, '').replace(/\S+/g, '');
        });
    }
    function sortable(table, index, header) { return !actionColumn(table, index, header) && !header.querySelector('input,select,button'); }
    function filterColumns(table) {
        return Array.from(table.tHead ? table.tHead.rows[0].cells : []).map((header, index) => {
            if (!text(header) || actionColumn(table, index, header)) return null;
            const values = table._dataRows.slice(0, 20).map(row => text(row.cells[index]));
            const distinct = [...new Set(values)];
            const average = values.reduce((sum, value) => sum + value.length, 0) / (values.length || 1);
            if (distinct.length < 2 || distinct.length > 10 || average > 80 || table._dataRows.some(row => row.cells[index] && row.cells[index].querySelector('input[type="checkbox"]'))) return null;
            if (table._dataRows.length && table._dataRows.slice(0, 20).every(row => row.cells[index] && row.cells[index].querySelectorAll('a').length > 0)) return null;
            return { index, values: distinct };
        }).filter(Boolean);
    }
    function option(select, value, label) { const item = document.createElement('option'); item.value = value; item.textContent = label || value; select.appendChild(item); }
    function makeToolbar(table, state) {
        const toolbar = document.createElement('div'); toolbar.className = 'data-table-toolbar';
        const search = document.createElement('input'); search.type = 'search'; search.placeholder = 'Search…'; search.setAttribute('aria-label', 'Search table'); search.value = state.search;
        const filters = document.createElement('div'); filters.className = 'data-table-filters';
        filterColumns(table).forEach(column => { const select = document.createElement('select'); select.dataset.column = column.index; select.setAttribute('aria-label', text(table.tHead.rows[0].cells[column.index])); option(select, '', 'All'); column.values.sort((a, b) => a.localeCompare(b)).forEach(value => option(select, value)); select.value = state.filters[column.index] || ''; filters.appendChild(select); });
        const entries = document.createElement('select'); entries.setAttribute('aria-label', 'Rows per page'); ENTRY_OPTIONS.forEach(value => option(entries, String(value), value === Infinity ? 'Tất cả' : String(value))); entries.value = '25';
        const count = document.createElement('span'); count.className = 'data-table-count';
        const pagination = document.createElement('div'); pagination.className = 'data-table-pagination pagination';
        toolbar.append(search, filters, entries, count, pagination); table.parentElement.insertBefore(toolbar, table.closest('.table-wrap') || table);
        table._controls = { search, filters, entries, count, pagination }; return toolbar;
    }
    function render(table) {
        const { search, filters, entries, count, pagination } = table._controls;
        const query = search.value.trim().toLowerCase();
        const selected = Object.fromEntries(Array.from(filters.children).map(select => [select.dataset.column, select.value]));
        const matches = table._dataRows.filter(row => (!query || text(row).toLowerCase().includes(query)) && Object.entries(selected).every(([i, value]) => !value || text(row.cells[i]).toLowerCase() === value.toLowerCase()));
        const perPage = Number(entries.value), pages = perPage === Infinity ? 1 : Math.max(1, Math.ceil(matches.length / perPage));
        table._page = Math.min(table._page || 1, pages); const start = perPage === Infinity ? 0 : (table._page - 1) * perPage;
        const visible = new Set(matches.slice(start, perPage === Infinity ? undefined : start + perPage)); table._dataRows.forEach(row => { row.hidden = !visible.has(row); });
        count.textContent = `${matches.length} result${matches.length === 1 ? '' : 's'}`; pagination.replaceChildren();
        for (let page = 1; page <= pages; page++) { const button = document.createElement('button'); button.type = 'button'; button.className = 'pagination-link' + (page === table._page ? ' current' : ''); button.textContent = page; button.disabled = page === table._page; button.addEventListener('click', () => { table._page = page; render(table); }); pagination.appendChild(button); }
        if (keyFor(table)) saveState(table, { search: search.value, filters: selected });
    }
    function setup(table) {
        if (!table._dataRows) table._dataRows = Array.from(table.tBodies[0]?.rows || []).filter(row => !emptyRow(row));
        if (!table._controls) { const state = readState(table); makeToolbar(table, state); table._controls.search.addEventListener('input', () => { table._page = 1; render(table); }); table._controls.filters.addEventListener('change', () => { table._page = 1; render(table); }); table._controls.entries.addEventListener('change', () => { table._page = 1; render(table); }); Array.from(table.tHead?.rows[0]?.cells || []).forEach((header, index) => { if (sortable(table, index, header)) header.addEventListener('click', () => { const direction = table._sort?.index === index && table._sort.direction === 1 ? -1 : 1; table._sort = { index, direction }; table._dataRows.sort((a, b) => { const av = text(a.cells[index]), bv = text(b.cells[index]); const an = Number(av), bn = Number(bv); const ad = Date.parse(av), bd = Date.parse(bv); const result = Number.isFinite(an) && Number.isFinite(bn) ? an - bn : !Number.isNaN(ad) && !Number.isNaN(bd) ? ad - bd : av.localeCompare(bv, undefined, { sensitivity: 'base' }); return result * direction; }); header.dataset.sort = direction === 1 ? 'asc' : 'desc'; table._dataRows.forEach(row => table.tBodies[0].appendChild(row)); render(table); }); }); }
        render(table);
    }
    function initDataTables(root = document) { Array.from(root.querySelectorAll('table.data-table')).forEach((table, index) => { table._fallbackTableKey = table._fallbackTableKey || String(index); setup(table); }); }
    function refreshDataTable(table) { if (table) { table._dataRows = null; if (table._controls) { const toolbar = table._controls.search.closest('.data-table-toolbar'); if (toolbar) toolbar.remove(); table._controls = null; } setup(table); } }
    window.DataTables = { init: initDataTables, refresh: refreshDataTable };
    document.addEventListener('DOMContentLoaded', () => DataTables.init());
})();
