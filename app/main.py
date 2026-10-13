from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api import add_error_handlers, router as api_router
from app.config import APP_NAME, ROOT
from app.db import init_db

FRONTEND_DIST = ROOT / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title=APP_NAME, lifespan=lifespan)
app.include_router(api_router)
add_error_handlers(app)

if (FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    """Serve the built React app; client-side routes all fall back to index.html."""
    if path.startswith("api/"):
        raise HTTPException(404)
    file = FRONTEND_DIST / path
    if path and file.is_file() and FRONTEND_DIST in file.resolve().parents:
        return FileResponse(file)
    index = FRONTEND_DIST / "index.html"
    if index.exists():
        return FileResponse(index)
    return HTMLResponse("<p>Frontend not built. Run <code>npm run dev</code> in <code>frontend/</code> "
                        "(dev, port 5173) or <code>npm run build</code>.</p>")
