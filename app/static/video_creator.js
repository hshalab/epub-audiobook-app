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

    // Loaded lazily the first time the Video Library tab is activated
    // (see the tab-switcher IIFE below), not unconditionally on page load.
    window.__videoLibraryLoad = loadVideos;
})();

(function() {
    const btnCreate = document.getElementById('tab-btn-create');
    const btnLibrary = document.getElementById('tab-btn-library');
    const panelCreate = document.getElementById('tab-panel-create');
    const panelLibrary = document.getElementById('tab-panel-library');
    if (!btnCreate || !btnLibrary || !panelCreate || !panelLibrary) return;
    let libraryLoaded = false;

    function activate(tab) {
        const isCreate = tab === 'create';
        btnCreate.classList.toggle('active', isCreate);
        btnLibrary.classList.toggle('active', !isCreate);
        btnCreate.setAttribute('aria-selected', String(isCreate));
        btnLibrary.setAttribute('aria-selected', String(!isCreate));
        panelCreate.hidden = !isCreate;
        panelLibrary.hidden = isCreate;
        if (!isCreate && !libraryLoaded) {
            libraryLoaded = true;
            if (window.__videoLibraryLoad) window.__videoLibraryLoad();
        }
    }

    btnCreate.addEventListener('click', () => activate('create'));
    btnLibrary.addEventListener('click', () => activate('library'));
    window.__videoSwitchToLibraryTab = () => {
        // Mark as loaded *before* activate() so its lazy-load-once guard
        // doesn't also fire loadVideos() below and double-fetch.
        libraryLoaded = true;
        activate('library');
        // Force a fresh reload even if the library tab was already visited
        // before, since the whole point of this link is "go see the video
        // you just made" — the lazy-load-once guard in activate() would
        // otherwise leave a stale table.
        if (window.__videoLibraryLoad) window.__videoLibraryLoad();
    };
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
            `;
            tbody.appendChild(tr);
        });
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
                    summaryHtml = `<p><strong>Hoàn thành!</strong> ${doneCount} thành công, ${errCount} lỗi. <a href="#" id="link-view-library">Xem trong Video Library</a></p>`;
                } else {
                    summaryHtml = `<p>Đang xử lý: ${procCount} đang chạy, ${doneCount} xong, ${errCount} lỗi.</p>`;
                }

                resultsList.innerHTML = summaryHtml;
                const libLink = document.getElementById('link-view-library');
                if (libLink) libLink.addEventListener('click', (e) => {
                    e.preventDefault();
                    if (window.__videoSwitchToLibraryTab) window.__videoSwitchToLibraryTab();
                });

                selectedIndices.forEach(idx => {
                    const job = jobs[String(idx)];
                    const result = job?.result;
                    const statusEl = document.querySelector(`[data-status="${idx}"]`);
                    const timeEl = document.querySelector(`[data-time="${idx}"]`);
                    const div = document.createElement('div');
                    div.className = 'result-item';
                    
                    if (job && job.status === 'done' && result) {
                        div.innerHTML = `<div class="result-main"><span class="status-ok">Hoàn thành</span> <strong>${escHtml(result.name)}</strong> <span class="result-time">${result.size_mb} MB${result.elapsed_seconds ? ` · ${result.elapsed_seconds}s` : ''}</span></div><video class="result-video" controls preload="metadata" src="${result.video_url}"></video><div class="result-actions"><a class="btn-outline btn-sm" href="${result.video_url}" download>Tải xuống</a> <button class="btn-yt-mini" onclick="uploadToYouTube('${result.video_url}', '${escHtml(result.name)}')">YouTube</button></div>`;
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
            if (!res.ok) throw new Error(data.detail || 'Không thể nạp audio');
            batchId = data.batch_id;
            batchFiles = data.files;
            if (window.__studioSetBatchId) window.__studioSetBatchId(batchId);
            if (data.errors.length) {
                alert('Some files skipped:\n' + data.errors.join('\n'));
            }
            document.getElementById('step-table').classList.remove('hidden');
            renderTable(batchFiles);
            document.getElementById('step-table').scrollIntoView({behavior: 'smooth', block: 'start'});
        } catch(err) {
            alert('Upload failed: ' + err.message);
        } finally {
            btn.disabled = false;
            btn.textContent = 'Nạp lại audio vào lô';
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

        const config = {
            max_concurrent: parseInt(document.getElementById('cfg-concurrent').value) || 3,
            music_id: musicSel.value ? parseInt(musicSel.value) : null,
            music_volume: parseInt(document.getElementById('cfg-music-volume').value),
        };

        const btn = this;
        btn.disabled = true;
        btn.textContent = 'Generating...';

        document.getElementById('step-results').classList.remove('hidden');
        const resultsList = document.getElementById('results-list');
        resultsList.innerHTML = '<p>Processing...</p>';
        startProgressPolling();

        try {
            const greetingData = new FormData();
            const introAudio = document.getElementById('cfg-intro-audio')?.files[0];
            const outroAudio = document.getElementById('cfg-outro-audio')?.files[0];
            if (introAudio) greetingData.append('intro_audio', introAudio);
            if (outroAudio) greetingData.append('outro_audio', outroAudio);
            if (introAudio || outroAudio) {
                const greetingRes = await fetch(`/video/batch/${batchId}/greetings`, {method: 'POST', body: greetingData});
                if (!greetingRes.ok) throw new Error((await greetingRes.json()).detail || 'Không thể upload audio chào');
            }
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
                        div.innerHTML = `<div class="result-main"><span class="status-ok">Hoàn thành</span> <strong>${escHtml(r.name)}</strong> <span class="result-time">${r.size_mb} MB${timeParts ? ` · ${escHtml(timeParts)}` : ''}</span></div><video class="result-video" controls preload="metadata" src="${r.video_url}"></video><div class="result-actions"><a class="btn-outline btn-sm" href="${r.video_url}" download>Tải xuống</a> <button class="btn-yt-mini" onclick="uploadToYouTube('${r.video_url}', '${escHtml(r.name)}')">YouTube</button></div>`;
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
        if (window.__studioRepopulateMixRef) window.__studioRepopulateMixRef(files);
    }
    window.refreshStudioFileLists = refreshStudioFileLists;
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
