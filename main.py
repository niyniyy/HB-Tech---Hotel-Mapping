import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.hotel_routes import router as hotel_router
from config import settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s"
)
logger = logging.getLogger(__name__)

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

# ─── Routers ───
app.include_router(hotel_router)


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
    logger.info("Docs available at: http://localhost:8000/docs")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
