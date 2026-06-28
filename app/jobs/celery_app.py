from celery import Celery

celery_app = Celery(
    "hotel_mapping",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/1",
    include=["app.jobs.mapping_worker"]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)