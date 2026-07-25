"""Analytics foundation (goal #7): views, retention, engagement, revenue,
cost. Metrics/cost/revenue are entered manually in Phase 1 (no platform
Analytics API integration yet — see ARCHITECTURE.md §0's TikTok/YouTube/IG
API constraints, and §22's "manual metrics collection" MVP scope); this
module is where automation plugs in later without changing its public
functions, since callers (API routers) only ever pass in the same
values regardless of where they originated.
"""

from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from content_factory.db.models.agent_run import AgentRun
from content_factory.db.models.analytics import CostLedger, MetricsSnapshot, RevenueSnapshot, ViralScoreRecord
from content_factory.db.models.campaign import Campaign
from content_factory.db.models.enums import VideoStatus
from content_factory.db.models.publication import Publication
from content_factory.db.models.video import Video
from content_factory.logging_config import get_logger
from content_factory.services import content_intelligence

logger = get_logger(__name__)

# Reference ceilings used to bound-normalize raw engagement counts into the
# 0..1 range the Viral Score formula (ARCHITECTURE.md §2.5) needs. Phase 1
# simplification, documented rather than hidden: a proper implementation
# needs a trailing-window z-score baseline per niche/account, which requires
# more published-video history than Phase 1 will have. These constants are
# the explicit place a Phase 2 upgrade plugs in.
REFERENCE_WATCH_TIME_S = 30.0
REFERENCE_SHARES = 50
REFERENCE_COMMENTS = 100
REFERENCE_LIKES = 1000

DUPLICATE_THRESHOLD = 0.6
ITERATE_THRESHOLD = 0.3

_AGENT_NAME_TO_COST_CATEGORY = {
    "research_agent": "llm",
    "script_agent": "llm",
    "tts_provider": "tts",
    "video_renderer": "render",
}


def _bounded_ratio(value: float, reference: float) -> float:
    if reference <= 0:
        return 0.0
    return max(0.0, min(value / reference, 1.0))


def record_metrics(
    db: Session,
    *,
    video: Video,
    views: int,
    avg_watch_time_s: float | None = None,
    completion_rate: float | None = None,
    rewatch_rate: float | None = None,
    shares: int = 0,
    comments: int = 0,
    likes: int = 0,
    saves: int = 0,
    source: str = "manual",
    captured_at: datetime | None = None,
) -> tuple[MetricsSnapshot, ViralScoreRecord]:
    snapshot = MetricsSnapshot(
        video_id=video.id,
        captured_at=captured_at or datetime.now(UTC),
        views=views,
        avg_watch_time_s=avg_watch_time_s,
        completion_rate=completion_rate,
        rewatch_rate=rewatch_rate,
        shares=shares,
        comments=comments,
        likes=likes,
        saves=saves,
        source=source,
    )
    db.add(snapshot)
    db.flush()

    score_record = compute_viral_score(db, video=video, snapshot=snapshot)
    logger.info("metrics_recorded", video_id=video.id, views=views, viral_score=float(score_record.score))
    return snapshot, score_record


def compute_viral_score(db: Session, *, video: Video, snapshot: MetricsSnapshot) -> ViralScoreRecord:
    """Implements the exact formula from ARCHITECTURE.md §2.5:

        0.40*watch_time + 0.25*completion_rate + 0.15*shares
        + 0.10*comments + 0.10*likes
    """
    watch_time_norm = _bounded_ratio(float(snapshot.avg_watch_time_s or 0), REFERENCE_WATCH_TIME_S)
    completion = float(snapshot.completion_rate or 0)
    shares_norm = _bounded_ratio(snapshot.shares, REFERENCE_SHARES)
    comments_norm = _bounded_ratio(snapshot.comments, REFERENCE_COMMENTS)
    likes_norm = _bounded_ratio(snapshot.likes, REFERENCE_LIKES)

    score = (
        0.40 * watch_time_norm
        + 0.25 * completion
        + 0.15 * shares_norm
        + 0.10 * comments_norm
        + 0.10 * likes_norm
    )
    score = round(score, 4)

    if score >= DUPLICATE_THRESHOLD:
        recommendation = "duplicate"
    elif score >= ITERATE_THRESHOLD:
        recommendation = "iterate"
    else:
        recommendation = "retire"

    record = ViralScoreRecord(
        metrics_snapshot_id=snapshot.id,
        video_id=video.id,
        score=score,
        breakdown_json={
            "watch_time_norm": round(watch_time_norm, 4),
            "completion_rate": round(completion, 4),
            "shares_norm": round(shares_norm, 4),
            "comments_norm": round(comments_norm, 4),
            "likes_norm": round(likes_norm, 4),
            "reference_ceilings": {
                "watch_time_s": REFERENCE_WATCH_TIME_S,
                "shares": REFERENCE_SHARES,
                "comments": REFERENCE_COMMENTS,
                "likes": REFERENCE_LIKES,
            },
        },
        recommendation=recommendation,
    )
    db.add(record)
    db.flush()

    script = video.script
    if script is not None:
        niche_id = script.idea.campaign.niche_id if script.idea and script.idea.campaign else None
        content_intelligence.record_hook_outcome(
            db, niche_id=niche_id, hook_text=script.hook_text, viral_score=score
        )

    logger.info("viral_score_computed", video_id=video.id, score=score, recommendation=recommendation)
    return record


def record_agent_run_cost(
    db: Session, *, agent_run: AgentRun, video_id: int | None = None, campaign_id: int | None = None
) -> CostLedger | None:
    """Automatic half of the Cost Control Layer's ledger (ARCHITECTURE.md
    §10a): every completed AgentRun with a nonzero cost gets a CostLedger
    row, called right after agents/base.agent_run's context manager exits.
    Manual, non-AI costs (human review time, misc tooling) use
    record_manual_cost below instead.
    """
    if not agent_run.cost_usd:
        return None
    category = _AGENT_NAME_TO_COST_CATEGORY.get(agent_run.agent_name, "other")
    entry = CostLedger(
        agent_run_id=agent_run.id,
        video_id=video_id,
        campaign_id=campaign_id,
        category=category,
        provider=agent_run.provider,
        cost_usd=agent_run.cost_usd,
        recorded_at=datetime.now(UTC),
    )
    db.add(entry)
    db.flush()
    return entry


def record_manual_cost(
    db: Session,
    *,
    video_id: int | None = None,
    campaign_id: int | None = None,
    category: str,
    cost_usd: float,
    provider: str | None = None,
    note: str | None = None,
) -> CostLedger:
    entry = CostLedger(
        video_id=video_id,
        campaign_id=campaign_id,
        category=category,
        provider=provider,
        cost_usd=cost_usd,
        note=note,
        recorded_at=datetime.now(UTC),
    )
    db.add(entry)
    db.flush()
    logger.info("manual_cost_recorded", video_id=video_id, campaign_id=campaign_id, category=category, cost_usd=cost_usd)
    return entry


def record_revenue(
    db: Session,
    *,
    video: Video,
    campaign_id: int,
    raw_views: int | None = None,
    approved_views: int | None = None,
    payout_realized: float = 0.0,
    payout_pending: float = 0.0,
    status: str = "pending",
    captured_at: datetime | None = None,
) -> RevenueSnapshot:
    snapshot = RevenueSnapshot(
        video_id=video.id,
        campaign_id=campaign_id,
        captured_at=captured_at or datetime.now(UTC),
        raw_views=raw_views,
        approved_views=approved_views,
        payout_realized=payout_realized,
        payout_pending=payout_pending,
        status=status,
    )
    db.add(snapshot)
    db.flush()
    logger.info("revenue_recorded", video_id=video.id, payout_realized=payout_realized, status=status)
    return snapshot


def compute_profit_summary(db: Session, *, video_id: int) -> dict:
    total_cost = db.query(func.coalesce(func.sum(CostLedger.cost_usd), 0)).filter(
        CostLedger.video_id == video_id
    ).scalar()
    total_revenue = db.query(func.coalesce(func.sum(RevenueSnapshot.payout_realized), 0)).filter(
        RevenueSnapshot.video_id == video_id
    ).scalar()
    total_cost = float(total_cost)
    total_revenue = float(total_revenue)
    return {
        "total_cost_usd": total_cost,
        "total_revenue_usd": total_revenue,
        "profit_usd": round(total_revenue - total_cost, 4),
    }


def compute_niche_profit_summary(db: Session, *, niche_id: int) -> dict:
    """ARCHITECTURE.md §9's "profit per niche" rollup — same aggregation
    shape as compute_profit_summary, joined through Campaign.niche_id since
    both CostLedger and RevenueSnapshot carry campaign_id directly."""
    total_cost = (
        db.query(func.coalesce(func.sum(CostLedger.cost_usd), 0))
        .join(Campaign, CostLedger.campaign_id == Campaign.id)
        .filter(Campaign.niche_id == niche_id)
        .scalar()
    )
    total_revenue = (
        db.query(func.coalesce(func.sum(RevenueSnapshot.payout_realized), 0))
        .join(Campaign, RevenueSnapshot.campaign_id == Campaign.id)
        .filter(Campaign.niche_id == niche_id)
        .scalar()
    )
    total_cost = float(total_cost)
    total_revenue = float(total_revenue)
    return {
        "total_cost_usd": total_cost,
        "total_revenue_usd": total_revenue,
        "profit_usd": round(total_revenue - total_cost, 4),
    }


def compute_account_profit_summary(db: Session, *, account_id: int) -> dict:
    """ARCHITECTURE.md §9's "profit per account" rollup. Accounts have no
    direct cost/revenue link — only through the videos published to them
    (Publication.video_id, Phase 2 M4) — so this joins through publications
    rather than campaigns."""
    published_video_ids = (
        db.query(Publication.video_id).filter(Publication.account_id == account_id).scalar_subquery()
    )

    total_cost = (
        db.query(func.coalesce(func.sum(CostLedger.cost_usd), 0))
        .filter(CostLedger.video_id.in_(published_video_ids))
        .scalar()
    )
    total_revenue = (
        db.query(func.coalesce(func.sum(RevenueSnapshot.payout_realized), 0))
        .filter(RevenueSnapshot.video_id.in_(published_video_ids))
        .scalar()
    )
    total_cost = float(total_cost)
    total_revenue = float(total_revenue)
    return {
        "total_cost_usd": total_cost,
        "total_revenue_usd": total_revenue,
        "profit_usd": round(total_revenue - total_cost, 4),
    }


def get_dashboard_summary(db: Session) -> dict:
    """Backs goal #8's minimal dashboard: campaign/content/review/performance
    at a glance. ARCHITECTURE.md §9 flags profit-per-video/niche as "the
    single most-visible screen" — this is the Phase 1 version of that."""
    campaign_count = db.query(Campaign).count()

    status_counts = dict(
        db.query(Video.status, func.count(Video.id)).group_by(Video.status).all()
    )
    video_counts_by_status = {
        (status.value if hasattr(status, "value") else status): count
        for status, count in status_counts.items()
    }

    pending_review_count = (
        db.query(Video).filter(Video.status == VideoStatus.PENDING_REVIEW).count()
    )

    total_cost = float(db.query(func.coalesce(func.sum(CostLedger.cost_usd), 0)).scalar())
    total_revenue = float(db.query(func.coalesce(func.sum(RevenueSnapshot.payout_realized), 0)).scalar())

    return {
        "campaign_count": campaign_count,
        "video_counts_by_status": video_counts_by_status,
        "pending_review_count": pending_review_count,
        "total_cost_usd": total_cost,
        "total_revenue_usd": total_revenue,
        "profit_usd": round(total_revenue - total_cost, 4),
    }
