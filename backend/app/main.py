from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db import init_db
from app.routers import agent_apply, applications, criteria, jobs, llm_usage, pipeline, resumes
from app.scheduler import auto_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    auto_scheduler.start()
    yield
    auto_scheduler.stop()


app = FastAPI(title="Job Application Helper", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(jobs.router)
app.include_router(criteria.router)
app.include_router(pipeline.router)
app.include_router(resumes.router)
app.include_router(applications.router)
app.include_router(agent_apply.router)
app.include_router(llm_usage.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
