"""Video Library REST API endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app import video_repository, youtube
from app.deps import locked_conn

router = APIRouter()


@router.get("/video/api/videos")
def list_videos(
    request: Request,
    page: int = 1,
    per_page: int = 20,
    search: str = "",
    upload_status: str = "",
    batch_id: str = "",
    sort: str = "created_at",
    order: str = "desc",
    date_from: str = "",
    date_to: str = "",
):
    if page < 1:
        raise HTTPException(status_code=400, detail="page must be >= 1")
    if per_page < 1 or per_page > 9999:
        raise HTTPException(status_code=400, detail="per_page must be 1-9999")
    valid_sorts = {"created_at", "filename", "file_size_bytes", "upload_status"}
    if sort not in valid_sorts:
        raise HTTPException(status_code=400, detail=f"sort must be one of {valid_sorts}")
    if order not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail="order must be 'asc' or 'desc'")
    with locked_conn(request) as conn:
        result = video_repository.list_videos(
            conn,
            page=page,
            per_page=per_page,
            search=search,
            upload_status=upload_status,
            batch_id=batch_id,
            sort=sort,
            order=order,
            date_from=date_from,
            date_to=date_to,
        )
    return JSONResponse(result)


@router.get("/video/api/videos/{video_id}")
def get_video(request: Request, video_id: int):
    with locked_conn(request) as conn:
        video = video_repository.get_video(conn, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    return JSONResponse(video)


@router.patch("/video/api/videos/{video_id}")
async def update_video(request: Request, video_id: int):
    body = await request.json()
    allowed_fields = {"title", "description", "tags", "privacy"}
    invalid = set(body.keys()) - allowed_fields
    if invalid:
        raise HTTPException(status_code=400, detail=f"Invalid fields: {invalid}")
    with locked_conn(request) as conn:
        video = video_repository.get_video(conn, video_id)
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")
        updated = video_repository.update_video(conn, video_id, **body)
    return JSONResponse(updated)


@router.delete("/video/api/videos/{video_id}")
def delete_video(request: Request, video_id: int):
    with locked_conn(request) as conn:
        if not video_repository.delete_video(conn, video_id):
            raise HTTPException(status_code=404, detail="Video not found")
    return JSONResponse({"status": "deleted"})





@router.post("/video/api/videos/bulk-delete")
async def bulk_delete(request: Request):
    body = await request.json()
    ids = body.get("ids", [])
    if not ids:
        raise HTTPException(status_code=400, detail="No IDs provided")
    with locked_conn(request) as conn:
        count = video_repository.bulk_delete_videos(conn, ids)
    return JSONResponse({"deleted": count})





@router.get("/video/api/upload-queue")
def list_upload_queue(request: Request):
    with locked_conn(request) as conn:
        pending = youtube.get_pending_uploads(conn)
    return JSONResponse({"uploads": pending})



