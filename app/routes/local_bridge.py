from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/local-bridge")


def _local_only(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(403, detail="native bridge is available on localhost only")


@router.get("/health")
def health(request: Request):
    _local_only(request)
    return {"status": "ok", "capabilities": ["pick-files", "pick-folder"]}


@router.post("/pick-files")
def pick_files(request: Request):
    _local_only(request)
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
        paths = list(filedialog.askopenfilenames(parent=root, title="Chọn file để upload"))
        root.destroy()
    except Exception as exc:
        raise HTTPException(503, detail=f"native picker unavailable: {exc}") from exc
    return {"paths": paths}


@router.post("/pick-folder")
def pick_folder(request: Request):
    _local_only(request)
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
        path = filedialog.askdirectory(parent=root, title="Chọn thư mục")
        root.destroy()
    except Exception as exc:
        raise HTTPException(503, detail=f"native picker unavailable: {exc}") from exc
    return {"path": path or None}
