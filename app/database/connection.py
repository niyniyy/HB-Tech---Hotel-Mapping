from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from config import settings


# Async engine for FastAPI and the Celery workers.
#
# Previously NullPool, which opened and tore down a TCP connection for every
# session. At 10 lakh records that is millions of connection handshakes and, with
# several workers running concurrently, exhausts the server's connection slots.
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_recycle=1800,
    pool_pre_ping=True,
)

# Session factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# Base class for all SQLAlchemy models
class Base(DeclarativeBase):
    pass


# Dependency — inject DB session into FastAPI routes.
# Services manage their own transactions; this only rolls back and closes, so a
# request cannot commit a half-finished unit of work behind the service's back.
async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
