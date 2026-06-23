from fastapi import FastAPI

from app.api.v1.hotels import router as hotels_router
from app.api.v1.mapping import router as mapping_router


app = FastAPI(
    title="HB Hotel Mapping API",
    version="1.0.0"
)


app.include_router(
    hotels_router,
    prefix="/api/v1",
    tags=["Hotels"]
)


app.include_router(
    mapping_router,
    prefix="/api/v1",
    tags=["Mapping"]
)


@app.get("/")
async def root():
    return {
        "message": "HB Hotel Mapping API is running"
    }