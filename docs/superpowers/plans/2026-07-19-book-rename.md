# Book Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow users to rename a book's title from the book detail page.

**Architecture:** Inline form on book detail page → POST route → SQL `UPDATE book SET title` — follows existing music rename pattern exactly.

**Tech Stack:** FastAPI, Jinja2, SQLite

## Global Constraints

- Follow existing patterns: `POST /{entity}/{id}/rename` + `repository.rename_{entity}`
- No new dependencies, no DB migration needed

---

### Task 1: Repository + Route + Template

**Files:**
- Modify: `app/repository.py` — add `rename_book()`
- Modify: `app/routes/books.py` — add `POST /books/{book_id}/rename`
- Modify: `app/templates/book_detail.html` — add inline rename form

**Interfaces:**
- Consumes: `locked_conn`, existing `Book` dataclass
- Produces: `POST /books/{book_id}/rename` → redirect to `/books/{book_id}`

- [ ] **Step 1: Add `rename_book` to repository**

In `app/repository.py`, add after `get_book` or near other `update_book_*` functions:

```python
def rename_book(conn: sqlite3.Connection, book_id: int, new_title: str) -> bool:
    cur = conn.execute(
        "UPDATE book SET title = ?, updated_at = ? WHERE id = ?",
        (new_title, datetime.now(timezone.utc).isoformat(), book_id),
    )
    conn.commit()
    return cur.rowcount > 0
```

Add the import if not present:
```python
from datetime import datetime, timezone
```

- [ ] **Step 2: Add route**

In `app/routes/books.py`, add after the existing `update_book_music` function (around line 300):

```python
@router.post("/books/{book_id}/rename")
def rename_book(request: Request, book_id: int, title: str = Form(...)):
    new_title = title.strip()
    if not new_title:
        raise HTTPException(status_code=400, detail="Tên không được để trống")
    with locked_conn(request) as conn:
        book = repository.get_book(conn, book_id)
        if book is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy sách")
        repository.rename_book(conn, book_id, new_title)
    return RedirectResponse(url=f"/books/{book_id}", status_code=303)
```

- [ ] **Step 3: Add inline form to template**

In `app/templates/book_detail.html`, after the `</h2>` closing tag (around line 16), add the rename form:

```html
<form method="post" action="/books/{{ book.id }}/rename" style="display:flex;gap:var(--space-sm);margin-bottom:var(--space-md);align-items:center">
    <input type="text" name="title" value="{{ book.title }}" required
           style="min-width:240px;padding:var(--space-xs) var(--space-sm)"
           placeholder="Nhập tên sách mới">
    <button type="submit" class="btn-sm btn-primary">Lưu</button>
    <a href="/books/{{ book.id }}" class="btn-sm btn-outline">Hủy</a>
</form>
```

- [ ] **Step 4: Verify it builds**

```bash
cd D:\Projects\epub-audiobook-app
python -c "from app.main import app; print('OK')"
```

Expected: `OK`
