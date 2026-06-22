import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5435/HB_Hotel_Mapping"
)


engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True
)


AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)


async def get_async_session():
    async with AsyncSessionLocal() as session:
        yield session