"""
CamTrap Verify — FastAPI backend entry point.

Creates the app, registers middleware and routers, mounts static files.
"""
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from settings import configure_logging, settings
from services.session_service import on_startup
from api.routers import health, fs, session, species, decisions, media, results, trapper, convert, cross, multiclassifier

configure_logging()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    on_startup()
    yield


app = FastAPI(title="Camera Trap Verification API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

for _router in [
    health.router,
    fs.router,
    session.router,
    species.router,
    decisions.router,
    media.router,
    results.router,
    trapper.router,
    convert.router,
    cross.router,
    multiclassifier.router,
]:
    app.include_router(_router)


def _static_dir() -> Path | None:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "static"  # type: ignore[attr-defined]
    candidate = Path(__file__).parent.parent / "frontend" / "dist"
    return candidate if candidate.exists() else None


_docs_dir = Path(__file__).parent.parent / "site"
if _docs_dir.exists():
    app.mount("/docs", StaticFiles(directory=str(_docs_dir), html=True), name="docs")

_sd = _static_dir()
if _sd is not None:
    app.mount("/", StaticFiles(directory=str(_sd), html=True), name="static")
