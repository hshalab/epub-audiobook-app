# Book Rename

## Overview

Allow users to rename a book's title from the book detail page.

## Approach

Follow the existing music rename pattern: inline form, POST endpoint, repository function.

## Changes

### 1. Repository (`app/repository.py`)

- Add `rename_book(conn, book_id: int, new_title: str) -> bool`
  - `UPDATE book SET title = ? WHERE id = ?`
  - Returns whether a row was updated.

### 2. Route (`app/routes/books.py`)

- Add `POST /books/{book_id}/rename`
  - Accept `title: str = Form(...)`
  - Strip whitespace; reject empty with 400
  - Call `repository.rename_book` inside `locked_conn`
  - Redirect to `/books/{book_id}` (303)

### 3. Template (`app/templates/book_detail.html`)

- Below the `<h2>` title row, add an inline form (always visible — matches music pattern):
  - Text input (pre-filled with `{{ book.title }}`) + "Save" button + "Cancel" link (back to `/books/{book_id}`)

### Files not changed

- No new dependencies, no CSS changes (reuse existing classes).
- No database migration needed (column already exists).
