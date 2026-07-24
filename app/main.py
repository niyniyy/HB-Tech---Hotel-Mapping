import logging
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.hotel_routes import router as hotel_router
from app.api.mapping import router as mapping_router
from app.api.review_routes import router as review_router
from app.api.data_routes import router as data_router
from config import settings

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s"
)
logger = logging.getLogger("uvicorn")


# ─── FastAPI App ───
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Hotel Mapping Engine — maps the same hotel across multiple suppliers",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ─── CORS ───
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
@app.middleware("http")
async def request_logger(request: Request, call_next):
    logger.info("%s %s", request.method, request.url.path)
    response = await call_next(request)
    logger.info("%s %s -> %d", request.method, request.url.path, response.status_code)
    return response

# ─── Routers ───
app.include_router(hotel_router)
app.include_router(mapping_router)
app.include_router(review_router)
app.include_router(data_router)


# ─── Review console ───
# Served directly by the API: no build step, no separate deployment.
app.mount("/ui", StaticFiles(directory=STATIC_DIR, html=True), name="ui")


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/ui/")


# ─── Health Check ───
@app.get("/health", tags=["Health"])
async def health_check():
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
    }


# ─── Startup ───
@app.on_event("startup")
async def on_startup():
    logger.info(f"{settings.APP_NAME} v{settings.APP_VERSION} started")
    logger.info("Docs available at: http://localhost:8001/docs")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
