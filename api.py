"""JSON API for the staff dashboard (ui/). Every route needs the X-Webhook-Secret header.
Messages are only ever sent from approve_draft(), which a person triggers with a click."""
import os

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

import drafts
import stats
from datasource import get_datasource
from drafts import DraftError
from nightly import run_nightly
from pipeline import default_threshold, run_pipeline
from scoring import score_all
from sender import get_sender


class ApproveBody(BaseModel):
    message: str | None = Field(default=None, max_length=2000)
    reviewer: str = Field(default="staff", max_length=40)


class RejectBody(BaseModel):
    reason: str = Field(min_length=1, max_length=300)
    reviewer: str = Field(default="staff", max_length=40)


def make_router(require_secret) -> APIRouter:
    router = APIRouter(prefix="/api", dependencies=[Depends(require_secret)])

    @router.get("/status")
    def status():
        return {
            "drafting": "placeholder" if os.getenv("DEMO_DRAFTS") == "1" else "claude",
            "sender": type(get_sender()).__name__.replace("Sender", "").lower(),
            "threshold": default_threshold(),
            "pending": len(drafts.list_pending()),
        }

    @router.get("/drafts")
    def list_drafts(status: str | None = None):
        return drafts.list_all(status if status in ("pending", "sent", "rejected") else None)

    @router.post("/drafts/{draft_id}/approve")
    def approve_draft(draft_id: int, body: ApproveBody):
        try:
            drafts.approve_and_send(draft_id, body.reviewer.strip() or "staff", final_message=body.message)
        except DraftError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except Exception:
            raise HTTPException(status_code=502, detail="The sender failed. The draft is still pending.")
        return {"ok": True}

    @router.post("/drafts/{draft_id}/reject")
    def reject_draft(draft_id: int, body: RejectBody):
        try:
            drafts.reject(draft_id, body.reviewer.strip() or "staff", body.reason)
        except DraftError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return {"ok": True}

    @router.get("/stats")
    def get_stats():
        rows = stats.load_rows()
        return {
            "total": stats.summarize(rows),
            "branches": [{"branch": b, **stats.summarize([r for r in rows if r["branch"] == b])}
                         for b in stats.BRANCHES],
            "angles": [{"angle": a, "sent": s, "won_back": w, "rate": r} for a, s, w, r in stats.by_angle(rows)],
            "rejections": [{"reason": r, "count": c} for r, c in stats.top_reasons(rows)],
        }

    @router.get("/at-risk")
    def at_risk(threshold: int | None = None, limit: int = 25):
        level = default_threshold() if threshold is None else threshold
        ds = get_datasource()
        members = {m["id"]: m for m in ds.list_active_members()}
        out = []
        for r in score_all(ds):  # sorted highest first
            if r.score < level or len(out) >= limit:
                break
            m = members[r.member_id]
            out.append({"member_id": r.member_id, "name": m["name"], "branch": m["branch"], "score": r.score,
                        "level": r.level, "reasons": r.reasons, "drafted": drafts.recently_drafted(r.member_id)})
        return out

    @router.post("/members/{member_id}/draft")
    def draft_for_member(member_id: int):
        if get_datasource().get_member(member_id) is None:
            raise HTTPException(status_code=404, detail="Unknown member")
        result = run_pipeline([member_id], trigger="manual", force=True, limit=1)
        if result.skipped_recent:
            raise HTTPException(status_code=409, detail="This member was already contacted in the last 14 days.")
        if result.failed:
            raise HTTPException(status_code=502, detail="Drafting failed. Check the server log.")
        return {"drafted": result.drafted, "draft_ids": result.draft_ids}

    @router.post("/nightly", status_code=202)
    def nightly_now(background: BackgroundTasks):
        background.add_task(run_nightly)
        return {"accepted": True}

    return router
