"""Creator Account Management (ARCHITECTURE.md §8) — Phase 2 M3.

Health checks are triggered externally (an operator or a cron), not on a
background scheduler — the same "manual/external-cron-triggered" pattern
already used by Phase 1's metrics endpoint. `warmup_status` can only ever
move warming -> active, and only when
services/account_service.check_warmup_graduation_eligible allows it.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from content_factory.api.deps import get_db
from content_factory.api.pagination import Pagination, pagination_params
from content_factory.api.serializers import to_account_out
from content_factory.auth.dependencies import require_auth, require_operator
from content_factory.config import Settings, get_settings
from content_factory.db.models.account import OwnedAccount
from content_factory.db.models.enums import AccountWarmupStatus
from content_factory.schemas.account import (
    AccountHealthCheckRequest,
    AccountHealthSnapshotOut,
    OwnedAccountCreate,
    OwnedAccountOut,
    OwnedAccountUpdate,
)
from content_factory.schemas.analytics import ProfitSummaryOut
from content_factory.services import account_service, analytics_service, token_encryption
from content_factory.services.token_encryption import TokenEncryptionNotConfigured

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.post("", response_model=OwnedAccountOut)
def create_account(
    payload: OwnedAccountCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _principal: dict = Depends(require_operator),
) -> OwnedAccountOut:
    existing = (
        db.query(OwnedAccount)
        .filter(OwnedAccount.platform == payload.platform, OwnedAccount.handle == payload.handle)
        .one_or_none()
    )
    if existing is not None:
        raise HTTPException(
            status_code=409, detail=f"Account {payload.platform.value}:{payload.handle!r} already exists"
        )

    encrypted_token = None
    if payload.oauth_token:
        try:
            encrypted_token = token_encryption.encrypt_token(payload.oauth_token, settings)
        except TokenEncryptionNotConfigured as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from None

    try:
        with db.begin_nested():
            account = OwnedAccount(
                platform=payload.platform,
                handle=payload.handle,
                platform_account_id=payload.platform_account_id,
                encrypted_oauth_token=encrypted_token,
                niche_focus_id=payload.niche_focus_id,
                daily_post_cap=payload.daily_post_cap,
            )
            db.add(account)
            db.flush()
    except IntegrityError:
        raise HTTPException(
            status_code=409, detail=f"Account {payload.platform.value}:{payload.handle!r} already exists"
        ) from None

    return to_account_out(account)


@router.get("", response_model=list[OwnedAccountOut])
def list_accounts(
    db: Session = Depends(get_db),
    pagination: Pagination = Depends(pagination_params),
    _principal: dict = Depends(require_auth),
) -> list[OwnedAccountOut]:
    accounts = (
        db.query(OwnedAccount)
        .order_by(OwnedAccount.created_at.desc())
        .offset(pagination.offset)
        .limit(pagination.limit)
        .all()
    )
    return [to_account_out(a) for a in accounts]


@router.get("/{account_id}", response_model=OwnedAccountOut)
def get_account(
    account_id: int, db: Session = Depends(get_db), _principal: dict = Depends(require_auth)
) -> OwnedAccountOut:
    account = db.get(OwnedAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return to_account_out(account)


@router.patch("/{account_id}", response_model=OwnedAccountOut)
def update_account(
    account_id: int,
    payload: OwnedAccountUpdate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _principal: dict = Depends(require_operator),
) -> OwnedAccountOut:
    account = db.get(OwnedAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")

    updates = payload.model_dump(exclude_unset=True, exclude={"oauth_token", "warmup_status"})
    for field, value in updates.items():
        setattr(account, field, value)

    if payload.oauth_token is not None:
        try:
            account.encrypted_oauth_token = token_encryption.encrypt_token(payload.oauth_token, settings)
        except TokenEncryptionNotConfigured as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from None

    if payload.warmup_status is not None:
        if payload.warmup_status == AccountWarmupStatus.ACTIVE:
            try:
                account_service.check_warmup_graduation_eligible(account, settings)
            except account_service.WarmupGraduationNotEligible as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None
            account.warmup_status = AccountWarmupStatus.ACTIVE
        elif payload.warmup_status == AccountWarmupStatus.WARMING and account.warmup_status == AccountWarmupStatus.ACTIVE:
            raise HTTPException(status_code=422, detail="Cannot move an active account back to warming")

    db.flush()
    return to_account_out(account)


@router.post("/{account_id}/health-check", response_model=AccountHealthSnapshotOut)
def run_health_check(
    account_id: int,
    payload: AccountHealthCheckRequest,
    db: Session = Depends(get_db),
    _principal: dict = Depends(require_operator),
) -> AccountHealthSnapshotOut:
    account = db.get(OwnedAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")

    snapshot = account_service.record_health_check(
        db,
        account=account,
        posting_cadence_used=payload.posting_cadence_used,
        engagement_trend=payload.engagement_trend,
        strikes_count=payload.strikes_count,
        api_error_rate=payload.api_error_rate,
    )
    return AccountHealthSnapshotOut.model_validate(snapshot)


@router.get("/{account_id}/profit", response_model=ProfitSummaryOut)
def get_account_profit(
    account_id: int, db: Session = Depends(get_db), _principal: dict = Depends(require_auth)
) -> ProfitSummaryOut:
    """ARCHITECTURE.md §9's per-account profit rollup (Phase 2 M6)."""
    account = db.get(OwnedAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    summary = analytics_service.compute_account_profit_summary(db, account_id=account_id)
    return ProfitSummaryOut(**summary)
