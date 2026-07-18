(function() {
    const DEBOUNCE_MS = 800;
    const timers = new Map();

    function showToast(msg, type) {
        let toast = document.getElementById('autosave-toast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'autosave-toast';
            toast.style.cssText = 'position:fixed;bottom:1rem;right:1rem;padding:0.5rem 1rem;border-radius:6px;font-size:0.875rem;z-index:9999;transition:opacity 0.3s;pointer-events:none;';
            document.body.appendChild(toast);
        }
        toast.textContent = msg;
        toast.style.background = type === 'ok' ? '#10b981' : type === 'error' ? '#ef4444' : '#6b7280';
        toast.style.color = '#fff';
        toast.style.opacity = '1';
        clearTimeout(toast._hideTimer);
        toast._hideTimer = setTimeout(() => { toast.style.opacity = '0'; }, 2000);
    }

    function saveForm(form) {
        const action = form.action;
        const method = (form.method || 'POST').toUpperCase();
        let body;

        if (form.enctype === 'multipart/form-data') {
            body = new FormData(form);
        } else {
            body = new URLSearchParams(new FormData(form));
        }

        const headers = {};
        if (form.enctype !== 'multipart/form-data') {
            headers['Content-Type'] = 'application/x-www-form-urlencoded';
        }
        headers['X-Requested-With'] = 'autosave';

        showToast('Đang lưu...', 'saving');

        fetch(action, { method, body, headers })
            .then(r => {
                if (r.ok) showToast('Đã lưu ✓', 'ok');
                else showToast('Lỗi lưu', 'error');
            })
            .catch(() => showToast('Lỗi lưu', 'error'));
    }

    function debounceSave(form, key) {
        clearTimeout(timers.get(key));
        timers.set(key, setTimeout(() => saveForm(form), DEBOUNCE_MS));
    }

    function attachAutosave(form) {
        const key = form.action + '_' + Array.from(form.elements).map(e => e.name).join(',');

        form.addEventListener('input', function(e) {
            if (e.target.tagName === 'TEXTAREA') return;
            debounceSave(form, key);
        });

        form.addEventListener('change', function(e) {
            if (e.target.type === 'checkbox' || e.target.type === 'radio' || e.target.tagName === 'SELECT') {
                clearTimeout(timers.get(key));
                saveForm(form);
            }
        });

        form.querySelectorAll('textarea').forEach(ta => {
            ta.addEventListener('input', function() {
                debounceSave(form, key);
            });
        });
    }

    document.addEventListener('DOMContentLoaded', function() {
        document.querySelectorAll('form[data-autosave]').forEach(attachAutosave);
    });
})();
