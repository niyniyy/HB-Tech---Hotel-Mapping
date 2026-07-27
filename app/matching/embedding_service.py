import asyncio
import threading

from sentence_transformers import SentenceTransformer

from config import settings

# Read from settings rather than pinned here: the model is a deployment
# decision (base vs fine-tuned) that has to move together with the decision
# band in config.py, and a literal here would let the two drift apart.
MODEL_NAME = settings.EMBEDDING_MODEL


class EmbeddingService:
    """
    Wraps the sentence-transformer model.

    The model is loaded once per process and guarded by a lock, so four Celery
    workers do not each start loading it concurrently on first use.

    `model.encode` is CPU-bound and blocking. Calling it directly from async code
    stalls the event loop for the duration, which stops the worker from doing
    anything else — including talking to the database. The async entry points
    below hand the work to a thread instead.
    """

    _model = None
    _lock = threading.Lock()

    def __init__(self):
        if EmbeddingService._model is None:
            with EmbeddingService._lock:
                if EmbeddingService._model is None:
                    EmbeddingService._model = SentenceTransformer(MODEL_NAME)

        self.model = EmbeddingService._model

    def generate_embedding(self, text: str) -> list[float]:
        return self.model.encode(text).tolist()

    def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        """
        Encode many texts in one call. Batching is several times faster per item
        than looping over single encodes, which matters when seeding embeddings
        for a large master table.
        """
        if not texts:
            return []

        return self.model.encode(
            texts,
            batch_size=64,
            show_progress_bar=False,
        ).tolist()

    async def generate_embedding_async(self, text: str) -> list[float]:
        return await asyncio.to_thread(self.generate_embedding, text)

    async def generate_embeddings_async(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self.generate_embeddings, texts)
