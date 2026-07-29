"""Queue job chạy nền, song song có giới hạn theo từng loại task."""
from app.jobqueue.models import HandlerSpec, Job, JobFatalError

__all__ = ["HandlerSpec", "Job", "JobFatalError"]
