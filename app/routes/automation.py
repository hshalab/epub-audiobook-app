from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, ValidationError

from app import automation_repository, repository
from app.automation_config import AutomationConfig
from app.config import settings
from app.deps import locked_conn


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
_STAGES = ("thumbnail", "video", "upload", "playlist")
_MEDIA_EXTENSIONS = {
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
    ".webp": "image",
    ".mp4": "video",
    ".webm": "video",
    ".mov": "video",
}


class MediaSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_ids: list[int]


def _safe_media_path(path: Path, backgrounds: Path, default: Path) -> Path | None:
    try:
        resolved = path.resolve()
        root = backgrounds.resolve()
        configured_default = default.resolve()
    except OSError:
        return None
    if resolved != configured_default and root not in resolved.parents:
        return None
    return resolved if resolved.is_file() else None


@router.get("/automation/settings", response_model=AutomationConfig)
def get_settings(request: Request):
    with locked_conn(request) as conn:
        return automation_repository.get_system_config(conn)


@router.get("/automation/settings-page", response_class=HTMLResponse)
def settings_page(request: Request):
    with locked_conn(request) as conn:
        config = automation_repository.get_system_config(conn)
    return templates.TemplateResponse(request, "automation_settings.html", {
        "config": config,
    })


@router.put("/automation/settings", response_model=AutomationConfig)
def put_settings(request: Request, config: dict):
    try:
        with locked_conn(request) as conn:
            return automation_repository.save_system_config(conn, config)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=jsonable_encoder(exc.errors())) from exc


@router.get("/automation/media")
def get_media(request: Request):
    backgrounds = Path(settings.data_root) / "backgrounds"
    backgrounds.mkdir(parents=True, exist_ok=True)
    paths = list(backgrounds.iterdir())
    default = Path(settings.default_background_image)
    if default.exists():
        paths.append(default)
    with locked_conn(request) as conn:
        for path in paths:
            safe_path = _safe_media_path(path, backgrounds, default)
            media_type = _MEDIA_EXTENSIONS.get(safe_path.suffix.lower()) if safe_path else None
            if safe_path and media_type:
                automation_repository.upsert_media_asset(conn, str(safe_path), safe_path.name, media_type)
        return {"assets": [dict(row) for row in conn.execute("SELECT * FROM media_assets ORDER BY filename, id")]}


@router.put("/books/{book_id}/automation/media/{role}")
def put_book_media(
    request: Request,
    book_id: int,
    role: str,
    selection: MediaSelection,
):
    if role not in {"background", "webcam"}:
        raise HTTPException(status_code=404, detail="Media role not found")
    with locked_conn(request) as conn:
        if conn.execute("SELECT 1 FROM book WHERE id=?", (book_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="Book not found")
        if len(selection.asset_ids) != len(set(selection.asset_ids)):
            raise HTTPException(status_code=422, detail=[{
                "type": "value_error",
                "loc": ["body", "asset_ids"],
                "msg": "Asset IDs must be unique",
                "input": selection.asset_ids,
            }])
        assets = []
        if selection.asset_ids:
            placeholders = ",".join("?" for _ in selection.asset_ids)
            assets = [dict(row) for row in conn.execute(
                f"SELECT * FROM media_assets WHERE id IN ({placeholders})",
                selection.asset_ids,
            )]
            if len(assets) != len(selection.asset_ids):
                raise HTTPException(status_code=404, detail="Media asset not found")
            if any(
                _MEDIA_EXTENSIONS.get(Path(asset["file_path"]).suffix.lower()) != asset["media_type"]
                or (role == "webcam" and asset["media_type"] != "video")
                for asset in assets
            ):
                raise HTTPException(status_code=422, detail=[{
                    "type": "value_error",
                    "loc": ["body", "asset_ids"],
                    "msg": "Assets do not match the media role or file extension",
                    "input": selection.asset_ids,
                }])
        automation_repository.set_book_media(conn, book_id, role, selection.asset_ids)
        return {"assets": automation_repository.list_book_media(conn, book_id, role)}


@router.post("/books/{book_id}/automation/enqueue")
def enqueue_book(request: Request, book_id: int):
    with locked_conn(request) as conn:
        if repository.get_book(conn, book_id) is None:
            raise HTTPException(status_code=404, detail="book not found")
        patches = conn.execute(
            "SELECT id FROM patch WHERE book_id=? ORDER BY patch_index", (book_id,)
        ).fetchall()
        pipeline_ids = []
        for (patch_id,) in patches:
            pipeline = automation_repository.enqueue_patch_pipeline(conn, patch_id)
            pipeline_ids.append(pipeline["id"])
    return {"pipeline_ids": pipeline_ids}


@router.post("/books/{book_id}/automation/retry/{patch_id}")
def retry_pipeline(request: Request, book_id: int, patch_id: int):
    with locked_conn(request) as conn:
        if repository.get_book(conn, book_id) is None:
            raise HTTPException(status_code=404, detail="book not found")
        patch = repository.get_patch(conn, patch_id)
        if patch is None or patch.book_id != book_id:
            raise HTTPException(status_code=404, detail="patch not found")
        pipeline = automation_repository.get_patch_pipeline(conn, patch_id)
        if pipeline is None:
            pipeline = automation_repository.enqueue_patch_pipeline(conn, patch_id)
        updated = automation_repository.retry_pipeline_stage(conn, pipeline["id"])
        if updated is None:
            raise HTTPException(status_code=400, detail="pipeline stage is not failed")
    return {"id": updated["id"], "stage": updated["stage"]}
