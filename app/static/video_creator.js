// Video Library
(function() {
    const API = '/video/api/videos';
    let currentPage = 1;
    let perPage = 20;
    let search = '';
    let statusFilter = '';
    let sort = 'created_at';
    let order = 'desc';
    let selectedIds = new Set();

    async function loadVideos() {
        const params = new URLSearchParams({
            page: currentPage,
            per_page: perPage,
            search,
            upload_status: statusFilter,
            sort,
            order,
        });
        const res = await fetch(`${API}?${params}`);
        const data = await res.json();
        renderTable(data);
        renderPagination(data);
        updateBulkButtons();
    }

    function renderTable(data) {
        const tbody = document.getElementById('video-table-body');
        tbody.innerHTML = '';
        if (!data.videos.length) {
            tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--text-muted)">No videos found</td></tr>';
            return;
        }
        data.videos.forEach((v, i) => {
            const tr = document.createElement('tr');
            const statusBadge = getStatusBadge(v.upload_status);
            const sizeMB = (v.file_size_bytes / (1024 * 1024)).toFixed(1);
            const date = new Date(v.created_at).toLocaleDateString();
            tr.innerHTML = `
                <td><input type="checkbox" class="video-check" data-id="${v.id}" ${selectedIds.has(v.id) ? 'checked' : ''}></td>
                <td>${(data.page - 1) * data.per_page + i + 1}</td>
                <td title="${escHtml(v.filename)}">${escHtml(v.filename)}</td>
                <td>${escHtml(v.title || '-')}</td>
                <td>${statusBadge}</td>
                <td>${sizeMB} MB</td>
                <td>${date}</td>
                <td>
                    <button type="button" class="btn-outline btn-sm" onclick="editVideo(${v.id})">Edit</button>
                    <a href="/video/videos/${encodeURIComponent(v.filename)}" download class="btn-outline btn-sm">Download</a>
                    ${v.upload_status === 'local_only' || v.upload_status === 'failed' ?
                        `<button type="button" class="btn-outline btn-sm" onclick="uploadSingle(${v.id})">Upload</button>` : ''}
                    <button type="button" class="btn-danger btn-sm" onclick="deleteVideo(${v.id})">Delete</button>
                </td>
            `;
            tbody.appendChild(tr);
        });

        tbody.querySelectorAll('.video-check').forEach(cb => {
            cb.addEventListener('change', function() {
                const id = parseInt(this.dataset.id);
                if (this.checked) selectedIds.add(id);
                else selectedIds.delete(id);
                updateBulkButtons();
            });
        });
    }

    function getStatusBadge(status) {
        const badges = {
            local_only: '<span class="badge badge-pending">Local</span>',
            queued: '<span class="badge badge-info">Queued</span>',
            uploading: '<span class="badge badge-processing">Uploading</span>',
            uploaded: '<span class="badge badge-done">Uploaded ✓</span>',
            failed: '<span class="badge badge-failed">Failed</span>',
        };
        return badges[status] || status;
    }

    function renderPagination(data) {
        const info = document.getElementById('pagination-info');
        info.textContent = `Showing ${(data.page - 1) * data.per_page + 1}-${Math.min(data.page * data.per_page, data.total)} of ${data.total}`;

        const container = document.getElementById('pagination-top');
        container.innerHTML = '';
        if (data.total_pages <= 1) return;

        const pages = [];
        for (let i = 1; i <= data.total_pages; i++) {
            if (i === 1 || i === data.total_pages || (i >= data.page - 2 && i <= data.page + 2)) {
                pages.push(i);
            } else if (pages[pages.length - 1] !== '...') {
                pages.push('...');
            }
        }

        pages.forEach(p => {
            const btn = document.createElement('button');
            btn.className = `btn-sm ${p === data.page ? 'btn-primary' : 'btn-outline'}`;
            btn.textContent = p;
            if (p !== '...') {
                btn.onclick = () => { currentPage = p; loadVideos(); };
            }
            container.appendChild(btn);
        });
    }

    function updateBulkButtons() {
        const hasSelected = selectedIds.size > 0;
        document.getElementById('btn-bulk-upload').disabled = !hasSelected;
        document.getElementById('btn-bulk-delete').disabled = !hasSelected;
    }

    function escHtml(s) {
        const d = document.createElement('div');
        d.textContent = s || '';
        return d.innerHTML;
    }

    // Filters
    let searchTimer;
    document.getElementById('filter-search').addEventListener('input', function() {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => { search = this.value; currentPage = 1; loadVideos(); }, 300);
    });
    document.getElementById('filter-status').addEventListener('change', function() {
        statusFilter = this.value; currentPage = 1; loadVideos();
    });
    document.getElementById('filter-sort').addEventListener('change', function() {
        const [s, o] = this.value.split(':');
        sort = s; order = o || 'desc'; currentPage = 1; loadVideos();
    });
    document.getElementById('filter-per-page').addEventListener('change', function() {
        perPage = parseInt(this.value); currentPage = 1; loadVideos();
    });

    // Select all
    document.getElementById('select-all-videos').addEventListener('change', function() {
        document.querySelectorAll('.video-check').forEach(cb => {
            cb.checked = this.checked;
            const id = parseInt(cb.dataset.id);
            if (this.checked) selectedIds.add(id);
            else selectedIds.delete(id);
        });
        updateBulkButtons();
    });

    // Bulk actions
    document.getElementById('btn-bulk-upload').addEventListener('click', async function() {
        const res = await fetch(`${API}/bulk-upload`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ids: [...selectedIds]}),
        });
        const data = await res.json();
        alert(`Queued ${data.queued} videos for upload`);
        selectedIds.clear();
        loadVideos();
    });

    document.getElementById('btn-bulk-delete').addEventListener('click', async function() {
        if (!confirm(`Delete ${selectedIds.size} videos?`)) return;
        const res = await fetch(`${API}/bulk-delete`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ids: [...selectedIds]}),
        });
        const data = await res.json();
        alert(`Deleted ${data.deleted} videos`);
        selectedIds.clear();
        loadVideos();
    });

    // Single video actions
    window.editVideo = async function(id) {
        const res = await fetch(`${API}/${id}`);
        const video = await res.json();
        document.getElementById('edit-id').value = id;
        document.getElementById('edit-title').value = video.title || '';
        document.getElementById('edit-description').value = video.description || '';
        document.getElementById('edit-tags').value = video.tags || '';
        document.getElementById('edit-privacy').value = video.privacy || 'private';
        document.getElementById('edit-modal').style.display = 'block';
    };

    window.closeEditModal = function() {
        document.getElementById('edit-modal').style.display = 'none';
    };

    window.saveVideo = async function() {
        const id = document.getElementById('edit-id').value;
        await fetch(`${API}/${id}`, {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                title: document.getElementById('edit-title').value,
                description: document.getElementById('edit-description').value,
                tags: document.getElementById('edit-tags').value,
                privacy: document.getElementById('edit-privacy').value,
            }),
        });
        closeEditModal();
        loadVideos();
    };

    window.uploadSingle = async function(id) {
        await fetch(`${API}/${id}/requeue`, {method: 'POST'});
        loadVideos();
    };

    window.deleteVideo = async function(id) {
        if (!confirm('Delete this video?')) return;
        await fetch(`${API}/${id}`, {method: 'DELETE'});
        loadVideos();
    };

    // Initial load
    loadVideos();
})();
(function() {
    const API = {
        uploadBatch: '/video/upload-batch',
        generateBatch: '/video/generate-batch',
        backgrounds: '/video/backgrounds',
        uploadBg: '/video/upload-background',
        previewBg: '/video/backgrounds/preview',
        musicList: '/music/list',
        progress: '/video/progress/',
    };

    let batchId = null;
    let batchFiles = [];
    let backgrounds = [];

    // Overlay config: one shared default applied to every file, plus a
    // sparse map of explicit per-file overrides. activeEditIndex is null
    // when editing the shared default, or a file index once the user has
    // clicked "Chỉnh" on that row.
    let defaultOverlayConfig = getDefaultOverlayConfig();
    let overlayConfigs = {};      // { [index]: configObject } -- overrides only
    let activeEditIndex = null;

    function getDefaultOverlayConfig() {
        return {
            text: "", position: "top", alignment: "center", font_size: 52,
            text_color: "#FFFFFF", margin: 20, offset_x: 0, offset_y: 0,
            shadow: { enabled: false, color: "#000000", offset: 3 },
            box: { enabled: false, color: "#000000", opacity: 60, padding_x: 16, padding_y: 8, radius: 8 },
            marquee: { enabled: false, height: 60, font_size: 36, text_color: "#FFFFFF", bg_color: "#000000", bg_opacity: 80, speed_px_per_sec: 50 }
        };
    }

    function initOverlayConfigs(files) {
        // New batch: drop per-file overrides from the previous batch, keep
        // the shared default (it's a deliberate user setting, not tied to
        // any one batch).
        overlayConfigs = {};
        activeEditIndex = null;
        updateOverrideDots();
    }

    function hasOverride(index) {
        return Object.prototype.hasOwnProperty.call(overlayConfigs, index);
    }

    // The config object the Studio form currently edits: a file's override
    // once one exists for it, otherwise the shared default.
    function currentOverlayTarget() {
        if (activeEditIndex !== null && overlayConfigs[activeEditIndex]) {
            return overlayConfigs[activeEditIndex];
        }
        return defaultOverlayConfig;
    }

    function updateOverrideDots() {
        document.querySelectorAll('.btn-edit-overlay').forEach(btn => {
            const idx = parseInt(btn.dataset.index);
            const cell = btn.closest('td');
            const dot = cell.querySelector('.override-dot');
            const resetBtn = cell.querySelector('.btn-reset-overlay');
            const has = hasOverride(idx);
            if (dot) dot.style.display = has ? 'inline-block' : 'none';
            if (resetBtn) resetBtn.style.display = has ? '' : 'none';
        });
    }

    function loadOverlayConfigToForm(cfg) {
        // Basic
        document.getElementById('ov-text').value = cfg.text;
        document.getElementById('ov-position').value = cfg.position;
        document.getElementById('ov-alignment').value = cfg.alignment;
        document.getElementById('ov-font-size').value = cfg.font_size;
        document.getElementById('ov-text-color').value = cfg.text_color;
        document.getElementById('ov-margin').value = cfg.margin;
        document.getElementById('ov-offset-x').value = cfg.offset_x;
        document.getElementById('ov-offset-y').value = cfg.offset_y;
        document.getElementById('ov-offset-label').textContent = `${cfg.offset_x}, ${cfg.offset_y}`;

        // Shadow
        document.getElementById('ov-shadow-enabled').checked = cfg.shadow.enabled;
        document.getElementById('ov-shadow-color').value = cfg.shadow.color;
        document.getElementById('ov-shadow-offset').value = cfg.shadow.offset;

        // Box
        document.getElementById('ov-box-enabled').checked = cfg.box.enabled;
        document.getElementById('ov-box-color').value = cfg.box.color;
        document.getElementById('ov-box-opacity').value = cfg.box.opacity;
        document.getElementById('ov-box-opacity-label').textContent = cfg.box.opacity + '%';
        document.getElementById('ov-box-px').value = cfg.box.padding_x;
        document.getElementById('ov-box-py').value = cfg.box.padding_y;
        document.getElementById('ov-box-radius').value = cfg.box.radius;

        // Marquee
        document.getElementById('ov-marquee-enabled').checked = cfg.marquee.enabled;
        document.getElementById('ov-marquee-height').value = cfg.marquee.height;
        document.getElementById('ov-marquee-font-size').value = cfg.marquee.font_size;
        document.getElementById('ov-marquee-text-color').value = cfg.marquee.text_color;
        document.getElementById('ov-marquee-bg-color').value = cfg.marquee.bg_color;
        document.getElementById('ov-marquee-opacity').value = cfg.marquee.bg_opacity;
        document.getElementById('ov-marquee-opacity-label').textContent = cfg.marquee.bg_opacity + '%';
        document.getElementById('ov-marquee-speed').value = cfg.marquee.speed_px_per_sec;
    }

    // Save-on-every-change: writes the form's current values into whichever
    // config object is passed in. Called from every overlay field's input
    // handler (wired in the drag-preview IIFE below via the
    // window.__studioOnOverlayFieldChanged bridge) so nothing is lost
    // whether or not the user switches rows or clicks "Chỉnh" before
    // generating.
    function saveFormToOverlayConfig(cfg) {
        cfg.text = document.getElementById('ov-text').value;
        cfg.position = document.getElementById('ov-position').value;
        cfg.alignment = document.getElementById('ov-alignment').value;
        cfg.font_size = parseInt(document.getElementById('ov-font-size').value) || 52;
        cfg.text_color = document.getElementById('ov-text-color').value;
        cfg.margin = parseInt(document.getElementById('ov-margin').value) || 20;
        cfg.offset_x = parseInt(document.getElementById('ov-offset-x').value) || 0;
        cfg.offset_y = parseInt(document.getElementById('ov-offset-y').value) || 0;

        cfg.shadow.enabled = document.getElementById('ov-shadow-enabled').checked;
        cfg.shadow.color = document.getElementById('ov-shadow-color').value;
        cfg.shadow.offset = parseInt(document.getElementById('ov-shadow-offset').value) || 3;

        cfg.box.enabled = document.getElementById('ov-box-enabled').checked;
        cfg.box.color = document.getElementById('ov-box-color').value;
        cfg.box.opacity = parseInt(document.getElementById('ov-box-opacity').value) || 60;
        cfg.box.padding_x = parseInt(document.getElementById('ov-box-px').value) || 16;
        cfg.box.padding_y = parseInt(document.getElementById('ov-box-py').value) || 8;
        cfg.box.radius = parseInt(document.getElementById('ov-box-radius').value) || 8;

        cfg.marquee.enabled = document.getElementById('ov-marquee-enabled').checked;
        cfg.marquee.height = parseInt(document.getElementById('ov-marquee-height').value) || 60;
        cfg.marquee.font_size = parseInt(document.getElementById('ov-marquee-font-size').value) || 36;
        cfg.marquee.text_color = document.getElementById('ov-marquee-text-color').value;
        cfg.marquee.bg_color = document.getElementById('ov-marquee-bg-color').value;
        cfg.marquee.bg_opacity = parseInt(document.getElementById('ov-marquee-opacity').value) || 80;
        cfg.marquee.speed_px_per_sec = parseInt(document.getElementById('ov-marquee-speed').value) || 50;
    }

    function updateStudioHeaderBadge() {
        const badge = document.getElementById('overlay-active-badge');
        const backBtn = document.getElementById('ov-edit-default');
        if (!badge) return;
        if (activeEditIndex === null) {
            badge.textContent = 'Đang chỉnh: Mặc định (tất cả files)';
            if (backBtn) backBtn.style.display = 'none';
        } else {
            const file = batchFiles.find(f => f.index === activeEditIndex);
            badge.textContent = file ? 'Đang chỉnh: ' + file.name : '';
            if (backBtn) backBtn.style.display = file ? 'inline-block' : 'none';
        }
        badge.style.display = 'inline-block';
    }

    // index === null switches to editing the shared default.
    function switchOverlayEditTarget(index) {
        activeEditIndex = index;
        loadOverlayConfigToForm(currentOverlayTarget());
        updateStudioHeaderBadge();
        if (window.__studioRefreshPreview) window.__studioRefreshPreview();
    }

    // Bridge for the drag-preview IIFE (a separate closure further down the
    // file) to save into whichever config is currently active, and to know
    // which file (if any) is being edited, without reaching into this
    // IIFE's private variables directly.
    window.__studioOnOverlayFieldChanged = function() {
        saveFormToOverlayConfig(currentOverlayTarget());
    };
    window.__studioGetActiveEditIndex = function() { return activeEditIndex; };

    // "Apply to all": take whatever is on the form right now, make it the
    // new shared default, and drop every per-file override so every file
    // uses exactly these settings.
    document.getElementById('ov-apply-all').addEventListener('click', function() {
        if (!batchFiles.length) return;
        saveFormToOverlayConfig(currentOverlayTarget());
        defaultOverlayConfig = JSON.parse(JSON.stringify(currentOverlayTarget()));
        overlayConfigs = {};
        document.querySelectorAll('#file-table-body tr').forEach(tr => tr.classList.remove('row-active'));
        updateOverrideDots();
        switchOverlayEditTarget(null);
        alert(`Đã áp dụng overlay hiện tại cho tất cả ${batchFiles.length} files`);
    });

    // "Clear all": reset the shared default to factory defaults and drop
    // every per-file override.
    document.getElementById('ov-clear-all').addEventListener('click', function() {
        if (!batchFiles.length) return;
        defaultOverlayConfig = getDefaultOverlayConfig();
        overlayConfigs = {};
        document.querySelectorAll('#file-table-body tr').forEach(tr => tr.classList.remove('row-active'));
        updateOverrideDots();
        switchOverlayEditTarget(null);
        alert('Đã xóa overlay tất cả files');
    });

    document.getElementById('ov-edit-default').addEventListener('click', () => {
        document.querySelectorAll('#file-table-body tr').forEach(tr => tr.classList.remove('row-active'));
        switchOverlayEditTarget(null);
    });

    // Dropzone for audio files
    (function() {
        const dz = document.getElementById('dropzone-audio');
        const input = document.getElementById('audio-files');
        const preview = document.getElementById('audio-preview');
        if (!dz || !input) return;
        ['dragenter','dragover'].forEach(e => dz.addEventListener(e, ev => { ev.preventDefault(); dz.classList.add('dragover'); }));
        ['dragleave','drop'].forEach(e => dz.addEventListener(e, ev => { ev.preventDefault(); dz.classList.remove('dragover'); }));
        dz.addEventListener('drop', ev => {
            const files = ev.dataTransfer.files;
            if (files.length) { input.files = files; input.dispatchEvent(new Event('change')); }
        });
        input.addEventListener('change', function() {
            if (!this.files.length) return;
            const names = Array.from(this.files).map(f => f.name).join(', ');
            preview.innerHTML = '<span style="font-size:var(--font-size-sm);color:var(--text-secondary)">' + names + '</span>';
            preview.style.display = 'inline-block';
        });
    })();

    async function loadBackgrounds() {
        try {
            const res = await fetch(API.backgrounds);
            const data = await res.json();
            backgrounds = data.backgrounds || [];
            refreshBgSelects();
            refreshAllPreviews();
        } catch(e) { console.error('Failed to load backgrounds', e); }
    }

    async function loadMusicList() {
        try {
            const res = await fetch(API.musicList);
            const data = await res.json();
            const sel = document.getElementById('cfg-music');
            (data.music || []).forEach(m => {
                const opt = document.createElement('option');
                opt.value = m.id;
                let label = m.name;
                if (m.duration_sec) {
                    const mins = Math.floor(m.duration_sec / 60);
                    const secs = String(Math.round(m.duration_sec % 60)).padStart(2, '0');
                    label += ` (${mins}:${secs})`;
                }
                opt.textContent = label;
                sel.appendChild(opt);
            });
        } catch(e) { console.error('Failed to load music list', e); }
    }

    function refreshBgSelects() {
        document.querySelectorAll('.bg-select').forEach(sel => {
            const current = sel.value;
            sel.innerHTML = '<option value="">-- Default --</option>';
            backgrounds.forEach(bg => {
                const opt = document.createElement('option');
                opt.value = bg.path;
                opt.textContent = bg.is_default ? '(Default) ' + bg.name.split('/').pop() : bg.name;
                sel.appendChild(opt);
            });
            if (current) sel.value = current;
        });
    }

    function updateSelectedCount() {
        const checks = document.querySelectorAll('.row-check');
        const checked = [...checks].filter(c => c.checked).length;
        document.getElementById('selected-count').textContent = checked + ' selected';
    }

    function renderTable(files) {
        const tbody = document.getElementById('file-table-body');
        tbody.innerHTML = '';
        files.forEach((f, i) => {
            const tr = document.createElement('tr');
            tr.dataset.index = f.index;
            tr.innerHTML = `
                <td class="col-check"><input type="checkbox" class="row-check" data-index="${f.index}" checked></td>
                <td>${i + 1}</td>
                <td>${escHtml(f.name)}</td>
                <td>${f.size_mb} MB</td>
                <td>
                    <div class="bg-controls">
                        <select class="bg-select" data-index="${f.index}">
                            <option value="">-- Default --</option>
                        </select>
                        <input type="file" class="bg-upload-inline" data-index="${f.index}" accept=".jpg,.jpeg,.png,.webp">
                    </div>
                </td>
                <td class="col-preview">
                    <img class="bg-preview-img empty" data-index="${f.index}"
                         alt="background preview" loading="lazy">
                    <div class="bg-preview-name" data-index="${f.index}">Default</div>
                </td>
                <td class="status-pending" data-status="${f.index}">Ready</td>
                <td class="col-time" data-time="${f.index}">—</td>
                <td class="col-edit">
                    <button type="button" class="btn-edit-overlay btn-sm btn-outline" data-index="${f.index}" title="Chỉnh overlay riêng cho file này">Chỉnh</button>
                    <button type="button" class="btn-reset-overlay btn-sm btn-outline" data-index="${f.index}" style="display:none" title="Bỏ overlay riêng, dùng mặc định">Mặc định</button>
                    <span class="status-dot status-dot-blue override-dot" style="display:none" title="File này có overlay tuỳ chỉnh riêng"></span>
                </td>
            `;
            tbody.appendChild(tr);
        });
        initOverlayConfigs(files);
        switchOverlayEditTarget(null);
        refreshBgSelects();
        refreshAllPreviews();
        updateSelectedCount();
        refreshStudioFileLists(files);

        tbody.querySelectorAll('.row-check').forEach(cb => {
            cb.addEventListener('change', updateSelectedCount);
        });

        document.getElementById('select-all').addEventListener('change', function() {
            tbody.querySelectorAll('.row-check').forEach(c => { c.checked = this.checked; });
            updateSelectedCount();
        });

        tbody.querySelectorAll('.bg-select').forEach(sel => {
            sel.addEventListener('change', function() {
                updatePreview(this.dataset.index);
                if (window.__studioOnRowBgChanged) window.__studioOnRowBgChanged(this.dataset.index);
            });
        });

        tbody.querySelectorAll('.btn-edit-overlay').forEach(btn => {
            btn.addEventListener('click', () => {
                const idx = parseInt(btn.dataset.index);
                if (!hasOverride(idx)) {
                    overlayConfigs[idx] = JSON.parse(JSON.stringify(defaultOverlayConfig));
                    updateOverrideDots();
                }
                tbody.querySelectorAll('tr').forEach(tr => tr.classList.remove('row-active'));
                btn.closest('tr').classList.add('row-active');
                switchOverlayEditTarget(idx);
            });
        });

        tbody.querySelectorAll('.btn-reset-overlay').forEach(btn => {
            btn.addEventListener('click', () => {
                const idx = parseInt(btn.dataset.index);
                delete overlayConfigs[idx];
                updateOverrideDots();
                if (activeEditIndex === idx) {
                    btn.closest('tr').classList.remove('row-active');
                    switchOverlayEditTarget(null);
                }
            });
        });

        tbody.querySelectorAll('.bg-upload-inline').forEach(input => {
            input.addEventListener('change', async function() {
                const idx = this.dataset.index;
                const file = this.files[0];
                if (!file) return;
                const fd = new FormData();
                fd.append('file', file);
                const statusEl = document.querySelector(`[data-status="${idx}"]`);
                statusEl.textContent = 'Uploading bg...';
                statusEl.className = 'status-pending';
                try {
                    const res = await fetch(API.uploadBg, { method: 'POST', body: fd });
                    const data = await res.json();
                    backgrounds.push({ name: data.name, path: data.path, is_default: false });
                    refreshBgSelects();
                    const sel = document.querySelector(`.bg-select[data-index="${idx}"]`);
                    sel.value = data.path;
                    updatePreview(idx);
                    if (window.__studioOnRowBgChanged) window.__studioOnRowBgChanged(idx);
                    statusEl.textContent = 'Ready';
                } catch(e) {
                    statusEl.textContent = 'BG upload failed';
                    statusEl.className = 'status-err';
                }
            });
        });
    }

    function defaultBackgroundPath() {
        const def = backgrounds.find(b => b.is_default);
        return def ? def.path : '';
    }

    function bgNameForPath(p) {
        if (!p) return 'Default';
        const found = backgrounds.find(b => b.path === p);
        if (found) return found.is_default ? 'Default' : found.name;
        try {
            return p.split(/[\\/]/).pop() || p;
        } catch (_) { return p; }
    }

    function previewUrlFor(p) {
        if (!p) return '';
        return API.previewBg + '?path=' + encodeURIComponent(p);
    }

    function updatePreview(idx) {
        const sel = document.querySelector(`.bg-select[data-index="${idx}"]`);
        const img = document.querySelector(`.bg-preview-img[data-index="${idx}"]`);
        const name = document.querySelector(`.bg-preview-name[data-index="${idx}"]`);
        if (!sel || !img || !name) return;
        const p = sel.value || defaultBackgroundPath();
        const url = previewUrlFor(p);
        if (url) {
            img.src = url;
            img.classList.remove('empty');
        } else {
            img.removeAttribute('src');
            img.classList.add('empty');
        }
        img.alt = 'Background: ' + bgNameForPath(p);
        name.textContent = bgNameForPath(p);
    }

    function refreshAllPreviews() {
        document.querySelectorAll('.bg-select').forEach(sel => updatePreview(sel.dataset.index));
    }

    function escHtml(s) {
        const d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    }

    function formatTimeShort(iso) {
        if (!iso) return '';
        const d = new Date(iso);
        if (isNaN(d.getTime())) return '';
        const pad = n => String(n).padStart(2, '0');
        return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
    }

    const EVENT_LABELS = {
        'job.start': 'Bắt đầu...',
        'music.resolved': 'Đã chọn nhạc nền',
        'overlay.rendered': 'Đã render text overlay',
        'overlay.failed_fallback': 'Overlay lỗi — dùng ảnh gốc',
        'segment.start': 'Chuẩn bị FFmpeg...',
        'segment.probe_duration': 'Đo thời lượng audio...',
        'segment.ffmpeg_start': 'FFmpeg đang chạy...',
        'segment.ffmpeg_done': 'FFmpeg xong, đang hoàn tất...',
        'segment.done': 'Encode xong',
        'segment.failed': 'FFmpeg lỗi',
        'job.done': 'Hoàn thành',
        'job.failed': 'Lỗi',
    };

    let progressTimer = null;

    function startProgressPolling() {
        stopProgressPolling();
        progressTimer = setInterval(async () => {
            if (!batchId) return;
            try {
                const res = await fetch(API.progress + batchId);
                const data = await res.json();
                Object.entries(data.jobs || {}).forEach(([idx, job]) => {
                    const statusEl = document.querySelector(`[data-status="${idx}"]`);
                    if (!statusEl || job.status !== 'running') return;
                    const last = job.steps[job.steps.length - 1];
                    if (last) {
                        statusEl.textContent = EVENT_LABELS[last.event] || last.event;
                        statusEl.className = 'status-pending';
                        statusEl.title = last.t + ' ' + last.event + ' ' + last.detail;
                    }
                });
            } catch(e) { /* polling is best-effort */ }
        }, 1000);
    }

    function stopProgressPolling() {
        if (progressTimer) { clearInterval(progressTimer); progressTimer = null; }
    }

    // Poll for batch results after async generation
    function pollBatchResults(batchId, selectedIndices) {
        const resultsList = document.getElementById('results-list');
        let pollTimer = null;
        
        async function poll() {
            try {
                const res = await fetch(API.progress + batchId);
                const data = await res.json();
                const jobs = data.jobs || {};
                
                // Count statuses
                let doneCount = 0, errCount = 0, procCount = 0;
                selectedIndices.forEach(idx => {
                    const job = jobs[String(idx)];
                    if (job?.status === 'done') doneCount++;
                    else if (job?.status === 'error') errCount++;
                    else procCount++;
                });
                
                // Check batch-level status
                const batchJob = jobs['_batch'];
                const batchDone = batchJob?.status === 'done';
                
                // Build summary header
                let summaryHtml = '';
                if (batchDone) {
                    summaryHtml = `<p><strong>Hoàn thành!</strong> ${doneCount} thành công, ${errCount} lỗi.</p>`;
                } else {
                    summaryHtml = `<p>Đang xử lý: ${procCount} đang chạy, ${doneCount} xong, ${errCount} lỗi.</p>`;
                }
                
                resultsList.innerHTML = summaryHtml;
                
                selectedIndices.forEach(idx => {
                    const job = jobs[String(idx)];
                    const result = job?.result;
                    const statusEl = document.querySelector(`[data-status="${idx}"]`);
                    const timeEl = document.querySelector(`[data-time="${idx}"]`);
                    const div = document.createElement('div');
                    div.className = 'result-item';
                    
                    if (job && job.status === 'done' && result) {
                        div.innerHTML = `<span class="status-ok">Done</span> <span>${escHtml(result.name)}</span> <a href="${result.video_url}" download>Download (${result.size_mb} MB)</a> <button class="btn-yt-mini" onclick="uploadToYouTube('${result.video_url}', '${escHtml(result.name)}')">YouTube</button>${result.elapsed_seconds ? ` <span class="result-time">${result.elapsed_seconds}s \u00b7 ${result.completed_at || ''}</span>` : ''}`;
                        if (statusEl) { statusEl.textContent = 'Done'; statusEl.className = 'status-ok'; }
                        if (timeEl) { timeEl.innerHTML = `Done ${result.elapsed_seconds ? `( ${result.elapsed_seconds}s )` : ''}`; if (result.completed_at) timeEl.title = 'Completed at ' + result.completed_at; }
                    } else if (job && job.status === 'error') {
                        div.innerHTML = `<span class="status-err">Failed</span> <span>#${idx}</span> <span class="status-err">${escHtml(job.steps[job.steps.length-1]?.detail?.error || 'Unknown error')}</span>`;
                        if (statusEl) { statusEl.textContent = 'Error'; statusEl.className = 'status-err'; }
                        if (timeEl) { timeEl.innerHTML = 'Failed'; }
                    } else {
                        const step = job?.steps?.[job.steps.length-1];
                        div.innerHTML = `<span class="status-pending">Processing...</span> <span>#${idx}</span> <span>${escHtml(step ? EVENT_LABELS[step.event] || step.event : 'Queued')}</span>`;
                        if (statusEl && step) { statusEl.textContent = EVENT_LABELS[step.event] || step.event; statusEl.className = 'status-pending'; }
                    }
                    
                    if (job && job.steps && job.steps.length) {
                        const details = document.createElement('details');
                        details.className = 'step-log';
                        details.innerHTML = '<summary>Log (' + job.steps.length + ')</summary>'
                            + '<div class="step-log-body">' + stepLogHtml(job.steps) + '</div>';
                        div.appendChild(details);
                    }
                    resultsList.appendChild(div);
                });
                
                if (batchDone) {
                    clearInterval(pollTimer);
                    const btn = document.getElementById('btn-generate');
                    if (btn) { btn.disabled = false; btn.textContent = 'Generate Selected Videos'; }
                }
            } catch(e) {
                clearInterval(pollTimer);
            }
        }
        
        pollTimer = setInterval(poll, 2000);
        poll(); // initial
    }

    function stepLogHtml(steps) {
        return (steps || []).map(s =>
            `<div class="step-log-line"><span class="step-t">${escHtml(s.t)}</span> ` +
            `<span class="step-e">${escHtml(s.event)}</span> ` +
            `<span class="step-d">${escHtml(s.detail)}</span></div>`
        ).join('');
    }

    document.getElementById('upload-form').addEventListener('submit', async function(e) {
        e.preventDefault();
        const input = document.getElementById('audio-files');
        if (!input.files.length) return;

        const fd = new FormData();
        for (const f of input.files) fd.append('files', f);

        const btn = document.getElementById('btn-upload');
        btn.disabled = true;
        btn.textContent = 'Uploading...';

        try {
            const res = await fetch(API.uploadBatch, { method: 'POST', body: fd });
            const data = await res.json();
            batchId = data.batch_id;
            batchFiles = data.files;
            if (window.__studioSetBatchId) window.__studioSetBatchId(batchId);
            if (data.errors.length) {
                alert('Some files skipped:\n' + data.errors.join('\n'));
            }
            document.getElementById('step-table').style.display = '';
            renderTable(batchFiles);
        } catch(err) {
            alert('Upload failed: ' + err.message);
        } finally {
            btn.disabled = false;
            btn.textContent = 'Upload';
        }
    });

    document.getElementById('btn-upload-bg').addEventListener('click', async function() {
        const input = document.getElementById('new-bg-file');
        if (!input.files.length) return;
        const fd = new FormData();
        fd.append('file', input.files[0]);
        this.disabled = true;
        try {
            const res = await fetch(API.uploadBg, { method: 'POST', body: fd });
            const data = await res.json();
            backgrounds.push({ name: data.name, path: data.path, is_default: false });
            refreshBgSelects();
            input.value = '';
        } catch(e) {
            alert('Upload failed');
        } finally {
            this.disabled = false;
        }
    });

    document.getElementById('btn-generate').addEventListener('click', async function() {
        if (!batchId) return;
        const checks = document.querySelectorAll('.row-check');
        const selected = [...checks].filter(c => c.checked).map(c => parseInt(c.dataset.index));
        if (!selected.length) { alert('Chọn ít nhất 1 file'); return; }

        const backgrounds_map = {};
        document.querySelectorAll('.bg-select').forEach(sel => {
            backgrounds_map[sel.dataset.index] = sel.value || null;
        });

        const musicSel = document.getElementById('cfg-music');

        // Make sure whatever's on the form right now is captured, even if
        // the user never switched rows or clicked "Chỉnh" before generating.
        saveFormToOverlayConfig(currentOverlayTarget());

        // Per-file overrides: every file the user explicitly customized,
        // sent as its full nested shape.
        const overlayConfigsMap = {};
        Object.entries(overlayConfigs).forEach(([idx, cfg]) => { overlayConfigsMap[idx] = cfg; });

        const config = {
            resolution: document.getElementById('cfg-resolution').value,
            fps: parseInt(document.getElementById('cfg-fps').value),
            codec: document.getElementById('cfg-codec').value,
            audio_bitrate: document.getElementById('cfg-audio-bitrate').value,
            image_type: document.getElementById('cfg-image-type').value,
            crf: parseInt(document.getElementById('cfg-crf').value),
            max_concurrent: parseInt(document.getElementById('cfg-concurrent').value) || 3,
            music_id: musicSel.value ? parseInt(musicSel.value) : null,
            music_volume: parseInt(document.getElementById('cfg-music-volume').value),
            // Full nested shape, matching overlay_configs entries — the bug
            // fix. Only sent when there's actual text (matches backend gate
            // at video.py:549, which skips rendering when text is empty).
            overlay: defaultOverlayConfig.text ? defaultOverlayConfig : null,
            overlay_configs: Object.keys(overlayConfigsMap).length ? overlayConfigsMap : null
        };

        const btn = this;
        btn.disabled = true;
        btn.textContent = 'Generating...';

        document.getElementById('step-results').style.display = '';
        const resultsList = document.getElementById('results-list');
        resultsList.innerHTML = '<p>Processing...</p>';
        startProgressPolling();

        try {
            const res = await fetch(API.generateBatch, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ batch_id: batchId, selected, backgrounds: backgrounds_map, config }),
            });
            const data = await res.json();
            
            // New async behavior: poll for results
            if (data.scheduled !== undefined) {
                const conc = data.max_concurrent || 3;
                resultsList.innerHTML = `<p>Đã lên lịch ${data.scheduled} video (tối đa ${conc} song song). Đang xử lý...</p>`;
                pollBatchResults(batchId, selected);
            } else {
                // Old sync behavior fallback
                resultsList.innerHTML = '';
                let finalJobs = {};
                try {
                    const progRes = await fetch(API.progress + batchId);
                    finalJobs = (await progRes.json()).jobs || {};
                } catch(e) { /* log unavailable */ }
                data.results.forEach(r => {
                    const div = document.createElement('div');
                    div.className = 'result-item';
                    const statusEl = document.querySelector(`[data-status="${r.index}"]`);
                    const timeEl = document.querySelector(`[data-time="${r.index}"]`);
                    if (r.status === 'done') {
                        const elapsed = r.elapsed_seconds != null ? `${r.elapsed_seconds}s` : '';
                        const completed = r.completed_at ? formatTimeShort(r.completed_at) : '';
                        const timeParts = [elapsed, completed].filter(Boolean).join(' \u00b7 ');
                        div.innerHTML = `<span class="status-ok">Done</span> <span>${escHtml(r.name)}</span> <a href="${r.video_url}" download>Download (${r.size_mb} MB)</a> <button class="btn-yt-mini" onclick="uploadToYouTube('${r.video_url}', '${escHtml(r.name)}')">YouTube</button>${timeParts ? ` <span class="result-time">${escHtml(timeParts)}</span>` : ''}`;
                        if (statusEl) {
                            statusEl.textContent = 'Done';
                            statusEl.className = 'status-ok';
                        }
                        if (timeEl) {
                            const timeHtml = [completed, elapsed && `<span class="time-elapsed">(${escHtml(elapsed)})</span>`].filter(Boolean).join(' ');
                            timeEl.innerHTML = timeHtml || '—';
                            if (r.completed_at) timeEl.title = 'Completed at ' + r.completed_at;
                        }
                    } else {
                        const elapsed = r.elapsed_seconds != null ? `${r.elapsed_seconds}s` : '';
                        div.innerHTML = `<span class="status-err">Failed</span> <span>#${r.index}</span> <span class="status-err">${escHtml(r.message)}</span>${elapsed ? ` <span class="result-time">${escHtml(elapsed)}</span>` : ''}`;
                        if (statusEl) {
                            statusEl.textContent = 'Error';
                            statusEl.className = 'status-err';
                        }
                        if (timeEl) {
                            const now = formatTimeShort(new Date().toISOString());
                            timeEl.innerHTML = `${now}${elapsed ? ` <span class="time-elapsed">(${escHtml(elapsed)})</span>` : ''}`;
                        }
                    }
                    const job = finalJobs[String(r.index)];
                    if (job && job.steps && job.steps.length) {
                        const details = document.createElement('details');
                        details.className = 'step-log';
                        details.innerHTML = '<summary>Log các bước (' + job.steps.length + ')</summary>'
                            + '<div class="step-log-body">' + stepLogHtml(job.steps) + '</div>';
                        div.appendChild(details);
                    }
                    resultsList.appendChild(div);
                });
                stopProgressPolling();
                btn.disabled = false;
                btn.textContent = 'Generate Selected Videos';
            }
        } catch(err) {
            resultsList.innerHTML = `<div class="error-block"><p style="margin:0">${escHtml(err.message)}</p></div>`;
            stopProgressPolling();
            btn.disabled = false;
            btn.textContent = 'Generate Selected Videos';
        }
    });

    loadBackgrounds();
    loadMusicList();

    // Keeps the studio's "preview với file" and "audio chính" dropdowns in
    // sync with the uploaded batch, and kicks off an initial preview render.
    function refreshStudioFileLists(files) {
        const previewRowSelect = document.getElementById('preview-row-select');
        if (previewRowSelect) {
            const prev = previewRowSelect.value;
            previewRowSelect.innerHTML = '';
            files.forEach(f => {
                const opt = document.createElement('option');
                opt.value = f.index;
                opt.textContent = f.name;
                previewRowSelect.appendChild(opt);
            });
            if (files.some(f => String(f.index) === prev)) previewRowSelect.value = prev;
        }
        if (window.__studioRepopulateMixRef) window.__studioRepopulateMixRef(files);
        if (window.__studioRefreshPreview) window.__studioRefreshPreview();
    }
    window.refreshStudioFileLists = refreshStudioFileLists;
})();

// --- Studio: live overlay preview + drag-to-position (mirrors the book
//     detail studio's /books/{id}/overlay-preview against /video/overlay-preview) ---
(function() {
    const ovImg = document.getElementById('ov-preview');
    const ovEmpty = document.getElementById('ov-preview-empty');
    const dragRect = document.getElementById('ov-drag-rect');
    const previewRowSelect = document.getElementById('preview-row-select');
    const offX = document.getElementById('ov-offset-x');
    const offY = document.getElementById('ov-offset-y');
    const offsetLabel = document.getElementById('ov-offset-label');
    if (!ovImg || !previewRowSelect) return;

    let rect = null;
    let objectUrl = null;
    let refreshTimer = null;
    let refreshing = false;
    let refreshQueued = false;

    function currentBgPath() {
        // If actively editing a specific file's overlay override, preview
        // against that file's background (via the batch-upload IIFE's
        // bridge — activeEditIndex lives in that other closure).
        const activeIdx = window.__studioGetActiveEditIndex ? window.__studioGetActiveEditIndex() : null;
        if (activeIdx !== null && activeIdx !== undefined) {
            const sel = document.querySelector(`.bg-select[data-index="${activeIdx}"]`);
            if (sel) return sel.value;
        }
        // Otherwise (editing the shared default) fall back to whichever
        // file is chosen in the "Preview với file" dropdown.
        const idx = previewRowSelect.value;
        if (idx === '') return '';
        const sel = document.querySelector(`.bg-select[data-index="${idx}"]`);
        return sel ? sel.value : '';
    }

    function previewParams() {
        const params = new URLSearchParams();
        params.set('background_path', currentBgPath());
        params.set('text', document.getElementById('ov-text').value);
        params.set('position', document.getElementById('ov-position').value);
        params.set('alignment', document.getElementById('ov-alignment').value);
        params.set('font_size', document.getElementById('ov-font-size').value || '52');
        params.set('text_color', document.getElementById('ov-text-color').value);
        params.set('margin', document.getElementById('ov-margin').value || '20');
        params.set('offset_x', offX.value || '0');
        params.set('offset_y', offY.value || '0');
        
        // Shadow
        params.set('shadow_enabled', document.getElementById('ov-shadow-enabled').checked ? '1' : '0');
        params.set('shadow_color', document.getElementById('ov-shadow-color').value);
        params.set('shadow_offset', document.getElementById('ov-shadow-offset').value || '3');
        
        // Box
        params.set('box_enabled', document.getElementById('ov-box-enabled').checked ? '1' : '0');
        params.set('box_color', document.getElementById('ov-box-color').value);
        params.set('box_opacity', document.getElementById('ov-box-opacity').value || '60');
        params.set('box_padding_x', document.getElementById('ov-box-px').value || '16');
        params.set('box_padding_y', document.getElementById('ov-box-py').value || '8');
        params.set('box_radius', document.getElementById('ov-box-radius').value || '8');
        
        // Marquee
        params.set('marquee_enabled', document.getElementById('ov-marquee-enabled').checked ? '1' : '0');
        params.set('marquee_height', document.getElementById('ov-marquee-height').value || '60');
        params.set('marquee_font_size', document.getElementById('ov-marquee-font-size').value || '36');
        params.set('marquee_text_color', document.getElementById('ov-marquee-text-color').value);
        params.set('marquee_bg_color', document.getElementById('ov-marquee-bg-color').value);
        params.set('marquee_bg_opacity', document.getElementById('ov-marquee-opacity').value || '80');
        params.set('marquee_speed_px_per_sec', document.getElementById('ov-marquee-speed').value || '50');
        
        params.set('t', Date.now());
        return params;
    }

    function positionRect() {
        if (!rect || !rect.img_w || !rect.img_h) { dragRect.style.display = 'none'; return; }
        dragRect.style.display = 'block';
        dragRect.style.left = (rect.x / rect.img_w * 100) + '%';
        dragRect.style.top = (rect.y / rect.img_h * 100) + '%';
        dragRect.style.width = (rect.w / rect.img_w * 100) + '%';
        dragRect.style.height = (rect.h / rect.img_h * 100) + '%';
        dragRect.style.transform = '';
    }

    async function refreshPreview() {
        if (previewRowSelect.options.length === 0) return;
        if (refreshing) { refreshQueued = true; return; }
        refreshing = true;
        try {
            const res = await fetch('/video/overlay-preview?' + previewParams());
            if (!res.ok) {
                ovEmpty.style.display = 'block';
                dragRect.style.display = 'none';
                return;
            }
            ovEmpty.style.display = 'none';
            const header = res.headers.get('X-Overlay-Rect');
            rect = header ? JSON.parse(header) : null;
            const blob = await res.blob();
            const url = URL.createObjectURL(blob);
            ovImg.onload = function() {
                if (objectUrl && objectUrl !== url) URL.revokeObjectURL(objectUrl);
                objectUrl = url;
                positionRect();
            };
            ovImg.src = url;
            offsetLabel.textContent = `${offX.value || 0}, ${offY.value || 0}`;
        } catch (_) {
        } finally {
            refreshing = false;
            if (refreshQueued) { refreshQueued = false; refreshPreview(); }
        }
    }

    function scheduleRefresh() {
        clearTimeout(refreshTimer);
        refreshTimer = setTimeout(refreshPreview, 250);
    }

    // Form input handlers for new overlay form
    const overlayFormIds = [
        'ov-text', 'ov-position', 'ov-alignment', 'ov-font-size', 'ov-text-color', 'ov-margin',
        'ov-shadow-enabled', 'ov-shadow-color', 'ov-shadow-offset',
        'ov-box-enabled', 'ov-box-color', 'ov-box-opacity', 'ov-box-px', 'ov-box-py', 'ov-box-radius',
        'ov-marquee-enabled', 'ov-marquee-height', 'ov-marquee-font-size', 'ov-marquee-text-color', 'ov-marquee-bg-color', 'ov-marquee-opacity', 'ov-marquee-speed'
    ];
    function saveOverlayField() {
        if (window.__studioOnOverlayFieldChanged) window.__studioOnOverlayFieldChanged();
    }

    overlayFormIds.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.addEventListener('input', () => { saveOverlayField(); scheduleRefresh(); });
            el.addEventListener('change', () => { saveOverlayField(); scheduleRefresh(); });
        }
    });

    // Changing anchor position/alignment resets drag offset
    ['ov-position', 'ov-alignment'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('change', () => {
            document.getElementById('ov-offset-x').value = 0;
            document.getElementById('ov-offset-y').value = 0;
            document.getElementById('ov-offset-label').textContent = '0, 0';
            saveOverlayField();
            scheduleRefresh();
        });
    });

    // Only controls which file's background shows in the live preview now
    // — which overlay config is being edited is controlled solely by the
    // "Chỉnh" button / "Overlay mặc định" control (batch-upload IIFE).
    previewRowSelect.addEventListener('change', () => {
        refreshPreview();
    });

    document.getElementById('ov-offset-reset').addEventListener('click', () => {
        document.getElementById('ov-offset-x').value = 0;
        document.getElementById('ov-offset-y').value = 0;
        document.getElementById('ov-offset-label').textContent = '0, 0';
        saveOverlayField();
        scheduleRefresh();
    });

    // Drag the text block; on release the delta is folded into offset_x/y and
    // the server re-renders (it applies the same clamping as the final render).
    let drag = null;
    dragRect.addEventListener('pointerdown', (e) => {
        if (!rect) return;
        e.preventDefault();
        dragRect.setPointerCapture(e.pointerId);
        dragRect.classList.add('dragging');
        drag = { startX: e.clientX, startY: e.clientY, dx: 0, dy: 0 };
    });
    dragRect.addEventListener('pointermove', (e) => {
        if (!drag || !rect) return;
        const scale = rect.img_w / ovImg.clientWidth;
        let dx = (e.clientX - drag.startX) * scale;
        let dy = (e.clientY - drag.startY) * scale;
        dx = Math.max(-rect.x, Math.min(rect.img_w - rect.w - rect.x, dx));
        dy = Math.max(-rect.y, Math.min(rect.img_h - rect.h - rect.y, dy));
        drag.dx = dx;
        drag.dy = dy;
        dragRect.style.transform = `translate(${dx / scale}px, ${dy / scale}px)`;
    });
    dragRect.addEventListener('pointerup', () => {
        if (!drag) return;
        offX.value = Math.round((parseInt(offX.value, 10) || 0) + drag.dx);
        offY.value = Math.round((parseInt(offY.value, 10) || 0) + drag.dy);
        drag = null;
        dragRect.classList.remove('dragging');
        saveOverlayField();
        refreshPreview();
    });
    dragRect.addEventListener('pointercancel', () => {
        drag = null;
        dragRect.classList.remove('dragging');
        positionRect();
    });

    window.__studioRefreshPreview = refreshPreview;
    window.__studioOnRowBgChanged = function(idx) {
        if (String(idx) === previewRowSelect.value) scheduleRefresh();
    };
})();

// --- Mix check: audio chính (uploaded file) at 100% + looping background
//     music at the configured ratio, mirroring the ffmpeg amix used at render time ---
(function() {
    const playBtn = document.getElementById('mix-play');
    const refSelect = document.getElementById('mix-ref');
    const seek = document.getElementById('mix-seek');
    const timeLabel = document.getElementById('mix-time');
    const musicSelect = document.getElementById('cfg-music');
    const volSlider = document.getElementById('cfg-music-volume');
    if (!playBtn || !refSelect) return;

    const refAudio = new Audio();
    refAudio.preload = 'metadata';
    const musicAudio = new Audio();
    musicAudio.loop = true;
    musicAudio.preload = 'none';
    let playing = false;
    let seekDragging = false;
    let currentBatchId = null;

    function refUrl() {
        if (!refSelect.value || currentBatchId == null) return '';
        return `/video/batch/${currentBatchId}/audio/${refSelect.value}`;
    }
    function musicUrl() {
        return musicSelect.value ? `/music/${musicSelect.value}/file` : '';
    }
    function applyVolume() {
        musicAudio.volume = Math.max(0, Math.min(100, parseInt(volSlider.value, 10) || 0)) / 100;
    }
    function fmt(sec) {
        if (!isFinite(sec) || sec < 0) return '0:00';
        const m = Math.floor(sec / 60);
        const s = Math.floor(sec % 60);
        return `${m}:${String(s).padStart(2, '0')}`;
    }
    function stopMix() {
        refAudio.pause();
        musicAudio.pause();
        playing = false;
        playBtn.innerHTML = '&#9654; Nghe thử';
    }
    async function startMix() {
        const hasRef = !!refUrl();
        const hasMusic = !!musicUrl();
        if (!hasRef && !hasMusic) {
            alert('Chưa có audio chính hoặc nhạc nền để nghe thử.');
            return;
        }
        applyVolume();
        try {
            if (hasRef) {
                if (refAudio.getAttribute('data-src') !== refUrl()) {
                    refAudio.src = refUrl();
                    refAudio.setAttribute('data-src', refUrl());
                }
                await refAudio.play();
            }
            if (hasMusic) {
                if (musicAudio.getAttribute('data-src') !== musicUrl()) {
                    musicAudio.src = musicUrl();
                    musicAudio.setAttribute('data-src', musicUrl());
                }
                await musicAudio.play();
            }
            playing = true;
            playBtn.innerHTML = '&#10074;&#10074; Tạm dừng';
        } catch (_) {
            stopMix();
        }
    }
    playBtn.addEventListener('click', () => { playing ? stopMix() : startMix(); });

    volSlider.addEventListener('input', applyVolume);
    refSelect.addEventListener('change', () => {
        const wasPlaying = playing;
        stopMix();
        seek.value = 0;
        if (wasPlaying) startMix();
    });
    musicSelect.addEventListener('change', () => {
        const wasPlaying = playing;
        stopMix();
        if (wasPlaying) startMix();
    });

    refAudio.addEventListener('loadedmetadata', () => {
        seek.disabled = !isFinite(refAudio.duration);
        if (isFinite(refAudio.duration)) seek.max = refAudio.duration;
        timeLabel.textContent = `${fmt(refAudio.currentTime)} / ${fmt(refAudio.duration)}`;
    });
    refAudio.addEventListener('timeupdate', () => {
        if (!seekDragging) seek.value = refAudio.currentTime;
        timeLabel.textContent = `${fmt(refAudio.currentTime)} / ${fmt(refAudio.duration)}`;
    });
    refAudio.addEventListener('ended', stopMix);
    seek.addEventListener('input', () => {
        seekDragging = true;
        timeLabel.textContent = `${fmt(parseFloat(seek.value) || 0)} / ${fmt(refAudio.duration)}`;
    });
    seek.addEventListener('change', () => {
        refAudio.currentTime = parseFloat(seek.value) || 0;
        seekDragging = false;
    });

    window.__studioSetBatchId = function(id) { currentBatchId = id; stopMix(); };
    window.__studioRepopulateMixRef = function(files) {
        refSelect.innerHTML = '';
        if (!files.length) {
            const opt = document.createElement('option');
            opt.value = '';
            opt.textContent = '— Chưa có file —';
            refSelect.appendChild(opt);
            return;
        }
        files.forEach(f => {
            const opt = document.createElement('option');
            opt.value = f.index;
            opt.textContent = f.name;
            refSelect.appendChild(opt);
        });
    };
})();

async function uploadToYouTube(videoUrl, videoName) {
    const title = prompt('YouTube title:', videoName.replace(/\.[^.]+$/, ''));
    if (!title) return;
    const tags = prompt('Tags (comma separated):', 'audiobook,epub,video') || '';
    const privacy = prompt('Privacy (private/unlisted/public):', 'private') || 'private';

    const fd = new FormData();
    try {
        const resp = await fetch(videoUrl);
        const blob = await resp.blob();
        fd.append('file', blob, videoName);
        fd.append('title', title);
        fd.append('description', '');
        fd.append('tags', tags);
        fd.append('privacy_status', privacy);

        const res = await fetch('/youtube/upload-file', { method: 'POST', body: fd });
        const data = await res.json();
        if (data.status === 'done') {
            alert('Upload thành công! Video ID: ' + data.youtube_video_id);
        } else {
            alert('Upload failed: ' + (data.error || 'Unknown error'));
        }
    } catch(err) {
        alert('Upload failed: ' + err.message);
    }
}
