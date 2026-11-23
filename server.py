"""Webhook server. Gym events can only create PENDING drafts; nothing here sends a message.
A human still approves every draft with run_flow.py.

Run:  uvicorn server:create_app --factory --port 8000
 or:  python server.py
"""
import logging
import os
import secrets
import sys
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Literal

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

from api import make_router  # noqa: E402
from db import connect  # noqa: E402
from events import EventError, GymEvent, process_event  # noqa: E402,F401  (GymEvent re-exported)
from nightly import run_nightly  # noqa: E402
from pipeline import run_pipeline  # noqa: E402

log = logging.getLogger("server")


class MessageStatus(BaseModel):
    draft_id: int
    status: Literal["delivered", "read", "failed"]
    replied: bool = False


def _pipeline_in_background(**kwargs) -> None:
    """Runs after the response is sent. Errors are logged, never raised."""
    try:
        r = run_pipeline(**kwargs)
        log.info("Pipeline (%s) for members %s: drafted %d, skipped recent %d, failed %d",
                 kwargs.get("trigger"), kwargs.get("member_ids"), r.drafted, r.skipped_recent, r.failed)
    except Exception as e:
        log.error("Background pipeline crashed (%s): %s", kwargs.get("trigger"), type(e).__name__)


def build_scheduler():
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    tz = os.getenv("TIMEZONE", "Asia/Kolkata")
    scheduler = BackgroundScheduler(timezone=tz)
    scheduler.add_job(run_nightly, CronTrigger(hour=int(os.getenv("NIGHTLY_HOUR") or 2), minute=0, timezone=tz),
                      id="nightly", max_instances=1, coalesce=True, replace_existing=True)
    return scheduler


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    secret = os.getenv("WEBHOOK_SECRET", "").strip()
    if not secret:
        msg = ("WEBHOOK_SECRET is empty or not set, so the server will not start. Add a long random value "
               "to .env, e.g. the output of: python -c \"import secrets; print(secrets.token_urlsafe(32))\"")
        print(msg, file=sys.stderr)
        raise RuntimeError(msg)

    if not os.getenv("ANTHROPIC_API_KEY") and os.getenv("DEMO_DRAFTS") != "1":
        os.environ["DEMO_DRAFTS"] = "1"  # one switch for webhooks, nightly and the dashboard
        log.warning("ANTHROPIC_API_KEY is not set: drafts will be template [DEMO] placeholders, not Claude.")

    scheduler = None if os.getenv("DISABLE_SCHEDULER") == "1" else build_scheduler()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if scheduler:
            scheduler.start()
            log.info("Nightly job scheduled at %s:00 %s", os.getenv("NIGHTLY_HOUR") or 2,
                     os.getenv("TIMEZONE", "Asia/Kolkata"))
        yield
        if scheduler and scheduler.running:
            scheduler.shutdown(wait=False)

    app = FastAPI(title="Gym win-back triggers", lifespan=lifespan)
    app.state.scheduler = scheduler

    def require_secret(x_webhook_secret: str | None = Header(default=None)) -> None:
        if x_webhook_secret is None or not secrets.compare_digest(x_webhook_secret.encode(), secret.encode()):
            raise HTTPException(status_code=401, detail="Missing or invalid X-Webhook-Secret")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    # Staff dashboard: the page is public, but all of its data comes from /api (secret needed).
    ui_dir = Path(__file__).parent / "ui"
    app.mount("/static", StaticFiles(directory=ui_dir), name="static")
    app.include_router(make_router(require_secret))

    @app.get("/", include_in_schema=False)
    def dashboard():
        return FileResponse(ui_dir / "index.html")

    @app.post("/webhooks/gym-events", status_code=202, dependencies=[Depends(require_secret)])
    def gym_event(event: GymEvent, background: BackgroundTasks):
        try:
            outcome = process_event(event)
        except EventError as e:
            raise HTTPException(status_code=e.status_code, detail=e.detail)
        if outcome.duplicate:
            return JSONResponse({"duplicate": True}, status_code=200)
        if outcome.pipeline_kwargs:  # drafting calls Claude and is slow: run it after responding
            background.add_task(_pipeline_in_background, **outcome.pipeline_kwargs)
        return {"accepted": True}

    @app.post("/webhooks/message-status", dependencies=[Depends(require_secret)])
    def message_status(body: MessageStatus):
        # A reply is not a visit, so won_back is never touched here.
        db = connect()
        try:
            updated = db.execute(
                "UPDATE drafts SET delivery_status = ?, replied = CASE WHEN ? THEN 1 ELSE COALESCE(replied, 0) END"
                " WHERE id = ? AND status = 'sent'", (body.status, body.replied, body.draft_id)).rowcount
            db.commit()
        finally:
            db.close()
        if updated != 1:
            raise HTTPException(status_code=404, detail="No sent draft with that id")
        return {"updated": True}

    return app


if __name__ == "__main__":
    import uvicorn

    try:
        application = create_app()
    except RuntimeError:
        sys.exit(1)  # the reason was already printed
    uvicorn.run(application, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT") or 8000))
