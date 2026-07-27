"""YouTube Data API v3 integration: OAuth2 flow, video upload, token management."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# Reusing the same OAuth client for both YouTube and Google Drive (see .env.example) means
# Google's token response often includes every scope ever granted to that client for this
# account, not just the one this flow requested. oauthlib treats that as an error by
# default ("Scope has changed") unless this is set - this is the standard, documented way
# to allow it.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import Flow
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    _GOOGLE_IMPORTS_OK = True
except ModuleNotFoundError:
    _GOOGLE_IMPORTS_OK = False

from app.config import settings

logger = logging.getLogger(__name__)

_SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.upload",
]
_API_SERVICE_NAME = "youtube"
_API_VERSION = "v3"
_UPLOAD_CHUNK_SIZE = 10 * 1024 * 1024  # 10 MB


def _require_google_imports() -> None:
    if not _GOOGLE_IMPORTS_OK:
        raise ModuleNotFoundError(
            "Missing Google API packages. Install: pip install google-auth google-auth-oauthlib google-api-python-client"
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def is_configured() -> bool:
    return bool(settings.youtube_client_id and settings.youtube_client_secret)


def get_creds_from_db(conn: sqlite3.Connection) -> dict | None:
    """Return the stored YouTube credentials row, or None."""
    row = conn.execute(
        "SELECT * FROM youtube_credentials ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def save_credentials(
    conn: sqlite3.Connection,
    access_token: str,
    refresh_token: str,
    token_expiry: str,
    channel_id: str | None = None,
    channel_name: str | None = None,
) -> None:
    """Upsert YouTube credentials (single-row table)."""
    existing = conn.execute("SELECT id FROM youtube_credentials LIMIT 1").fetchone()
    now = _now_iso()
    if existing:
        conn.execute(
            """UPDATE youtube_credentials
               SET access_token=?, refresh_token=?, token_expiry=?,
                   channel_id=?, channel_name=?, updated_at=?
               WHERE id=?""",
            (access_token, refresh_token, token_expiry,
             channel_id, channel_name, now, existing["id"]),
        )
    else:
        conn.execute(
            """INSERT INTO youtube_credentials
               (access_token, refresh_token, token_expiry, channel_id, channel_name, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (access_token, refresh_token, token_expiry,
             channel_id, channel_name, now, now),
        )
    conn.commit()


def delete_credentials(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM youtube_credentials")
    conn.commit()


def _build_credentials(row: dict) -> Credentials:
    _require_google_imports()
    return Credentials(
        token=row["access_token"],
        refresh_token=row["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.youtube_client_id,
        client_secret=settings.youtube_client_secret,
        scopes=_SCOPES,
    )


def _refresh_if_needed(conn: sqlite3.Connection, creds_row: dict) -> Credentials:
    """Build Credentials, refresh if expired, and persist new tokens."""
    _require_google_imports()
    creds = _build_credentials(creds_row)
    if creds.expired or not creds.valid:
        try:
            creds.refresh(Request())
        except Exception:
            logger.exception("YouTube token refresh failed")
            raise
        expiry_str = creds.expiry.isoformat() if creds.expiry else creds_row["token_expiry"]
        save_credentials(
            conn,
            access_token=creds.token or "",
            refresh_token=creds.refresh_token or creds_row["refresh_token"],
            token_expiry=expiry_str,
            channel_id=creds_row.get("channel_id"),
            channel_name=creds_row.get("channel_name"),
        )
    return creds


def get_youtube_service(conn: sqlite3.Connection):
    """Return an authorized YouTube API service object."""
    _require_google_imports()
    creds_row = get_creds_from_db(conn)
    if creds_row is None:
        raise ValueError("YouTube not connected. Please connect first.")
    creds = _refresh_if_needed(conn, creds_row)
    return build(_API_SERVICE_NAME, _API_VERSION, credentials=creds)


def get_authorization_url(redirect_uri: str) -> str:
    """Generate the Google OAuth2 consent screen URL."""
    _require_google_imports()
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": settings.youtube_client_id,
                "client_secret": settings.youtube_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=_SCOPES,
        # PKCE needs the same code_verifier at both the auth-url step and the token-exchange
        # step, but those happen in two separate HTTP requests with no shared Flow instance
        # (no server-side session here) - so auto-generating one here would just get lost by
        # the time exchange_code() runs, causing "invalid_grant: Missing code verifier". Not
        # needed anyway since this is a confidential client (has a client_secret).
        autogenerate_code_verifier=False,
    )
    flow.redirect_uri = redirect_uri
    url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        # select_account forces Google to always show the account chooser, even if the
        # browser already has an active session for a single Google account (otherwise it
        # silently reuses that account without letting the user pick a different one).
        prompt="select_account consent",
    )
    return url


def exchange_code(code: str, redirect_uri: str) -> dict:
    """Exchange authorization code for tokens. Returns channel info."""
    _require_google_imports()
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": settings.youtube_client_id,
                "client_secret": settings.youtube_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=_SCOPES,
        autogenerate_code_verifier=False,
    )
    flow.redirect_uri = redirect_uri
    flow.fetch_token(code=code)
    creds = flow.credentials

    # Get channel info
    youtube = build(_API_SERVICE_NAME, _API_VERSION, credentials=creds)
    ch_resp = youtube.channels().list(part="snippet", mine=True).execute()
    channel_id = ""
    channel_name = ""
    if ch_resp.get("items"):
        ch = ch_resp["items"][0]
        channel_id = ch["id"]
        channel_name = ch["snippet"]["title"]

    expiry_str = creds.expiry.isoformat() if creds.expiry else ""
    return {
        "access_token": creds.token or "",
        "refresh_token": creds.refresh_token or "",
        "token_expiry": expiry_str,
        "channel_id": channel_id,
        "channel_name": channel_name,
    }


def process_upload(conn: sqlite3.Connection, upload_id: int) -> dict:
    """Upload a video for an existing youtube_uploads row.

    Updates the existing row from pending → uploading → done.
    Never creates a second row.
    Returns {youtube_video_id, status}.
    """
    _require_google_imports()
    row = conn.execute("SELECT * FROM youtube_uploads WHERE id=?", (upload_id,)).fetchone()
    if row is None:
        raise ValueError(f"upload {upload_id} not found")
    if row["status"] == "done":
        return {"youtube_video_id": row["youtube_video_id"], "status": "done"}

    video_file = Path(row["video_path"])
    if not video_file.exists():
        raise FileNotFoundError(f"Video file not found: {row['video_path']}")

    conn.execute(
        "UPDATE youtube_uploads SET status='uploading' WHERE id=?",
        (upload_id,),
    )
    conn.commit()

    try:
        youtube = get_youtube_service(conn)
        tags = json.loads(row["tags"]) if row["tags"] else []
        title = (row["title"] or "")[:100]
        description = (row["description"] or "")[:5000]
        privacy_status = row.get("privacy_status", "private")

        body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": (tags or [])[:30],
                "categoryId": "26",
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": False,
            },
        }

        media = MediaFileUpload(
            str(video_file),
            mimetype="video/mp4",
            resumable=True,
            chunksize=_UPLOAD_CHUNK_SIZE,
        )

        req = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

        response = None
        while response is None:
            status, response = req.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                logger.info("YouTube upload %s: %d%%", upload_id, pct)

        youtube_video_id = response.get("id", "")
        conn.execute(
            "UPDATE youtube_uploads SET youtube_video_id=?, status='done', uploaded_at=?, error_message=NULL WHERE id=?",
            (youtube_video_id, _now_iso(), upload_id),
        )
        conn.commit()
        logger.info("YouTube upload %s done: %s", upload_id, youtube_video_id)
        return {"youtube_video_id": youtube_video_id, "status": "done"}

    except Exception as exc:
        conn.execute(
            "UPDATE youtube_uploads SET status='failed', error_message=? WHERE id=?",
            (str(exc), upload_id),
        )
        conn.commit()
        logger.exception("YouTube upload %s failed", upload_id)
        return {"youtube_video_id": None, "status": "failed", "error": str(exc)}


def upload_video(
    conn: sqlite3.Connection,
    video_path: str,
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    privacy_status: str = "private",
) -> dict:
    """Upload a video to YouTube. Compatibility wrapper: enqueues then processes.

    Keeps the same signature and return shape as before.
    Returns {upload_id, youtube_video_id, status}.
    """
    upload_id = enqueue_upload(conn, video_path, title, description, tags, privacy_status)
    result = process_upload(conn, upload_id)
    result["upload_id"] = upload_id
    return result


def enqueue_upload(
    conn: sqlite3.Connection,
    video_path: str,
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    privacy_status: str | None = None,
    video_id: int | None = None,
) -> int:
    """Create a pending youtube_uploads record. Returns upload_id.

    The actual upload is done by the caller (worker or route).
    """
    if privacy_status is None:
        privacy_status = settings.youtube_default_privacy
    now = _now_iso()
    cursor = conn.execute(
        """INSERT INTO youtube_uploads
           (video_id, video_path, title, description, tags, privacy_status, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)""",
        (video_id, video_path, title, description, json.dumps(tags or []), privacy_status, now),
    )
    conn.commit()
    return cursor.lastrowid


def list_uploads(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM youtube_uploads ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_pending_uploads(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM youtube_uploads WHERE status='pending' ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def mark_upload_done(conn: sqlite3.Connection, upload_id: int, youtube_video_id: str) -> None:
    conn.execute(
        "UPDATE youtube_uploads SET youtube_video_id=?, status='done', uploaded_at=? WHERE id=?",
        (youtube_video_id, _now_iso(), upload_id),
    )
    conn.commit()


def mark_upload_failed(conn: sqlite3.Connection, upload_id: int, error: str) -> None:
    conn.execute(
        "UPDATE youtube_uploads SET status='failed', error_message=? WHERE id=?",
        (error, upload_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Thumbnail and playlist post-processing
# ---------------------------------------------------------------------------


def set_thumbnail(conn: sqlite3.Connection, youtube_video_id: str, thumbnail_path: str) -> None:
    """Set the custom thumbnail for a published video."""
    _require_google_imports()
    service = get_youtube_service(conn)
    media = MediaFileUpload(thumbnail_path, mimetype="image/png")
    service.thumbnails().set(videoId=youtube_video_id, media_body=media).execute()


def list_playlists(conn: sqlite3.Connection, max_results: int = 50) -> list[dict]:
    """List the authenticated user's playlists."""
    _require_google_imports()
    service = get_youtube_service(conn)
    resp = service.playlists().list(part="snippet", mine=True, maxResults=max_results).execute()
    return resp.get("items", [])


def create_playlist(
    conn: sqlite3.Connection,
    title: str,
    description: str = "",
    privacy: str = "private",
) -> dict:
    """Create a new playlist. Returns the API response dict."""
    _require_google_imports()
    service = get_youtube_service(conn)
    body = {
        "snippet": {"title": title, "description": description},
        "status": {"privacyStatus": privacy},
    }
    return service.playlists().insert(part="snippet,status", body=body).execute()


def playlist_contains_video(conn: sqlite3.Connection, playlist_id: str, youtube_video_id: str) -> bool:
    """Check if a video is already in a playlist."""
    _require_google_imports()
    service = get_youtube_service(conn)
    page_token = None
    while True:
        params = {"part": "snippet", "playlistId": playlist_id, "maxResults": 50}
        if page_token:
            params["pageToken"] = page_token
        resp = service.playlistItems().list(**params).execute()
        for item in resp.get("items", []):
            if item.get("snippet", {}).get("resourceId", {}).get("videoId") == youtube_video_id:
                return True
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return False


def add_video_to_playlist(conn: sqlite3.Connection, playlist_id: str, youtube_video_id: str) -> dict:
    """Add a video to a playlist. Returns the API response dict."""
    _require_google_imports()
    service = get_youtube_service(conn)
    body = {
        "snippet": {
            "playlistId": playlist_id,
            "resourceId": {"kind": "youtube#video", "videoId": youtube_video_id},
        },
    }
    return service.playlistItems().insert(part="snippet", body=body).execute()


def resolve_book_playlist(
    conn: sqlite3.Connection,
    book_id: int,
    channel_id: str,
    template_values: dict[str, object],
) -> str:
    """Find or create a playlist for a book. Returns playlist_id."""
    existing = conn.execute(
        "SELECT playlist_id FROM youtube_playlist_map WHERE book_id=? AND channel_id=?",
        (book_id, channel_id),
    ).fetchone()
    if existing:
        return existing["playlist_id"]
    title = template_values.get("_playlist_title", template_values.get("book_title", "Audiobook"))
    description = template_values.get("_playlist_description", "")
    privacy = template_values.get("_playlist_privacy", "private")
    playlist = create_playlist(conn, title, description, privacy)
    playlist_id = playlist["id"]
    conn.execute(
        "INSERT INTO youtube_playlist_map (book_id,channel_id,playlist_id,mode,created_at,updated_at) VALUES (?,?,?,?,?,?)",
        (book_id, channel_id, playlist_id, "auto-create", _now_iso(), _now_iso()),
    )
    conn.commit()
    return playlist_id


def postprocess_upload(conn: sqlite3.Connection, upload_id: int) -> dict:
    """Set thumbnail and add to playlist for a completed upload.

    Each step is persisted independently for idempotent retry.
    Returns {status: "published"|"auth_required"|"failed", youtube_video_id}.
    """
    row = _dict(conn.execute("SELECT * FROM youtube_uploads WHERE id=?", (upload_id,)).fetchone())
    if row is None:
        raise ValueError(f"upload {upload_id} not found")
    if row["status"] != "done" or not row["youtube_video_id"]:
        raise ValueError(f"upload {upload_id} is not done")
    if row["thumbnail_status"] == "done" and row["playlist_status"] == "done":
        return {"status": "published", "youtube_video_id": row["youtube_video_id"]}

    try:
        youtube_video_id = row["youtube_video_id"]
        metadata = json.loads(row["metadata_snapshot"]) if row["metadata_snapshot"] else {}
        youtube_config = metadata.get("automation", {}).get("youtube", {})

        if row["thumbnail_status"] != "done":
            thumbnail_path = None
            pipeline = conn.execute(
                "SELECT thumbnail_path FROM patch_pipeline WHERE youtube_upload_id=?",
                (upload_id,),
            ).fetchone()
            if pipeline and pipeline["thumbnail_path"]:
                thumbnail_path = pipeline["thumbnail_path"]
            if not thumbnail_path:
                fallback = metadata.get("background_fallback")
                if fallback and Path(fallback).is_file():
                    thumbnail_path = fallback
            if thumbnail_path:
                try:
                    set_thumbnail(conn, youtube_video_id, thumbnail_path)
                except Exception:
                    conn.execute(
                        "UPDATE youtube_uploads SET thumbnail_status='failed', thumbnail_error=? WHERE id=?",
                        (str(_exception_safe()), upload_id),
                    )
                    conn.commit()
                    raise
            conn.execute(
                "UPDATE youtube_uploads SET thumbnail_status='done' WHERE id=?",
                (upload_id,),
            )
            conn.commit()

        if row["playlist_status"] != "done":
            playlist_mode = youtube_config.get("playlist_mode", "none")
            if playlist_mode != "none":
                creds = get_creds_from_db(conn)
                channel_id = creds["channel_id"] if creds else ""
                playlist_id = youtube_config.get("playlist_id") or ""
                if playlist_mode == "auto-create" and channel_id:
                    book_id = _resolve_book_id(conn, upload_id)
                    from app.automation_config import render_metadata_template
                    template_ctx = {
                        "book_title": row["title"] or "Audiobook",
                        "patch_name": "",
                        "patch_index": 0,
                        "chapter_start": 0,
                        "chapter_end": 0,
                    }
                    playlist_title = youtube_config.get("playlist_title_template", "{book_title}")
                    playlist_desc = youtube_config.get("playlist_description_template", "")
                    rendered_title = render_metadata_template(playlist_title, template_ctx)
                    rendered_desc = render_metadata_template(playlist_desc, template_ctx) if playlist_desc else ""
                    template_values = {
                        "book_title": template_ctx["book_title"],
                        "_playlist_title": rendered_title,
                        "_playlist_description": rendered_desc,
                        "_playlist_privacy": youtube_config.get("playlist_privacy", "private"),
                    }
                    if book_id:
                        playlist_id = resolve_book_playlist(conn, book_id, channel_id, template_values)
                if playlist_id:
                    if not playlist_contains_video(conn, playlist_id, youtube_video_id):
                        add_video_to_playlist(conn, playlist_id, youtube_video_id)
                    conn.execute(
                        "UPDATE youtube_uploads SET playlist_status='done', playlist_id=? WHERE id=?",
                        (playlist_id, upload_id),
                    )
                    conn.commit()
                else:
                    conn.execute(
                        "UPDATE youtube_uploads SET playlist_status='done' WHERE id=?",
                        (upload_id,),
                    )
                    conn.commit()
            else:
                conn.execute(
                    "UPDATE youtube_uploads SET playlist_status='done' WHERE id=?",
                    (upload_id,),
                )
                conn.commit()

        return {"status": "published", "youtube_video_id": youtube_video_id}

    except Exception:
        logger.exception("postprocess_upload %s failed", upload_id)
        return {"status": "failed", "youtube_video_id": row["youtube_video_id"]}


def _exception_safe() -> str:
    import traceback
    return traceback.format_exc()[-2000:]


def _resolve_book_id(conn: sqlite3.Connection, upload_id: int) -> int | None:
    row = conn.execute(
        """SELECT p.book_id FROM patch_pipeline pp
           JOIN patch p ON p.id=pp.patch_id
           WHERE pp.youtube_upload_id=?""",
        (upload_id,),
    ).fetchone()
    return row["book_id"] if row else None
