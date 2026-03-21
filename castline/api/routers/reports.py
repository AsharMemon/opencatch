"""Catch report submission and signal tracking endpoints.

Ported from Django castline.backend.apps.core.views.catch_report et al.
Persists to JSONL files for MVP; will migrate to async SQLAlchemy + PostgreSQL.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException, Query

from castline.api.models.schemas import CatchReportCreate, CatchReportResponse, ReviewCreate, ReviewResponse

logger = logging.getLogger(__name__)
router = APIRouter()

# Storage path
_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)


def _next_report_id() -> int:
    """Simple auto-increment ID from the reports file line count."""
    reports_path = _DATA_DIR / "catch_reports.jsonl"
    if not reports_path.exists():
        return 1
    with open(reports_path) as f:
        return sum(1 for _ in f) + 1


@router.post("/catch-report", response_model=CatchReportResponse)
async def submit_catch_report(
    request: Request,
    report: CatchReportCreate,
):
    """Submit a user catch report for the feedback loop.

    Validates inputs and persists to JSONL storage.
    Each report contributes to the negative signal analysis pipeline
    and can be used to retrain models with real-world outcome data.
    """
    # Additional validation beyond Pydantic
    if report.kept_count > report.catch_count:
        raise HTTPException(
            status_code=400,
            detail="kept_count cannot exceed catch_count",
        )

    report_id = _next_report_id()
    entry = report.model_dump()
    entry["id"] = report_id
    entry["_saved_at"] = datetime.now(timezone.utc).isoformat()

    # Convert datetime objects to strings for JSON serialization
    for k, v in entry.items():
        if isinstance(v, datetime):
            entry[k] = v.isoformat()

    reports_path = _DATA_DIR / "catch_reports.jsonl"
    with open(reports_path, "a") as f:
        f.write(json.dumps(entry) + "\n")

    logger.info("Catch report #%d saved for %s", report_id, report.location)

    return CatchReportResponse(
        id=report_id,
        status="saved",
    )


@router.get("/catch-reports")
async def list_catch_reports(
    request: Request,
    location: str = Query("", description="Filter by location name"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List submitted catch reports, optionally filtered by location."""
    reports_path = _DATA_DIR / "catch_reports.jsonl"
    if not reports_path.exists():
        return {"reports": [], "total": 0}

    reports = []
    with open(reports_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                report = json.loads(line)
                if location and location.lower() not in report.get("location", "").lower():
                    continue
                reports.append(report)
            except json.JSONDecodeError:
                continue

    total = len(reports)
    # Most recent first
    reports.reverse()
    reports = reports[offset : offset + limit]

    return {"reports": reports, "total": total, "limit": limit, "offset": offset}


@router.get("/signals")
async def signal_dashboard(
    request: Request,
    days: int = Query(30, ge=1, le=365),
    location: str = Query(""),
):
    """Dashboard data for negative signal analysis.

    Returns signal distribution, accuracy metrics, and top error locations.
    Reads from JSONL storage files.
    """
    reports_path = _DATA_DIR / "catch_reports.jsonl"
    predictions_path = _DATA_DIR / "prediction_logs.jsonl"

    report_count = 0
    prediction_count = 0
    reports_by_location: dict[str, list] = {}

    if reports_path.exists():
        cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
        with open(reports_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    report = json.loads(line)
                    # Filter by date if available
                    saved_at = report.get("_saved_at", "")
                    if saved_at:
                        try:
                            dt = datetime.fromisoformat(saved_at)
                            if dt.timestamp() < cutoff:
                                continue
                        except ValueError:
                            pass
                    # Filter by location
                    loc = report.get("location", "Unknown")
                    if location and location.lower() not in loc.lower():
                        continue
                    report_count += 1
                    reports_by_location.setdefault(loc, []).append(report)
                except json.JSONDecodeError:
                    continue

    if predictions_path.exists():
        with open(predictions_path) as f:
            prediction_count = sum(1 for line in f if line.strip())

    # Compute basic signal stats
    top_locations = sorted(
        reports_by_location.items(),
        key=lambda x: len(x[1]),
        reverse=True,
    )[:10]

    return {
        "period_days": days,
        "total_predictions": prediction_count,
        "total_reports": report_count,
        "total_resolved": 0,
        "resolution_rate": 0.0,
        "top_reporting_locations": [
            {"location": loc, "report_count": len(reports)}
            for loc, reports in top_locations
        ],
        "signal_distribution": {
            "positive": sum(
                1 for reports in reports_by_location.values()
                for r in reports if r.get("catch_count", 0) > 0
            ),
            "negative": sum(
                1 for reports in reports_by_location.values()
                for r in reports if r.get("catch_count", 0) == 0
            ),
        },
        "metrics": {
            "accuracy": 0.0,
            "precision": 0.0,
            "recall": 0.0,
            "note": "Accuracy metrics require prediction-report pairing (coming soon)",
        },
    }


# ── Reviews ──────────────────────────────────────────────────────────────


def _next_review_id() -> str:
    """Generate a unique review ID."""
    import uuid
    return str(uuid.uuid4())[:12]


@router.post("/reviews", response_model=ReviewResponse)
async def submit_review(review: ReviewCreate):
    """Submit a location review.

    Persists to JSONL storage. Will migrate to PostgreSQL with user auth.
    """
    review_id = _next_review_id()
    now = datetime.now(timezone.utc)
    entry = review.model_dump()
    entry["id"] = review_id
    entry["created_at"] = now.isoformat()

    reviews_path = _DATA_DIR / "reviews.jsonl"
    with open(reviews_path, "a") as f:
        f.write(json.dumps(entry) + "\n")

    logger.info("Review %s saved for location %s", review_id, review.location_id)

    return ReviewResponse(
        id=review_id,
        location_id=review.location_id,
        user_id=review.user_id,
        rating=review.rating,
        text=review.text,
        created_at=now,
    )


@router.get("/reviews")
async def list_reviews(
    location_id: str = Query("", description="Filter by location ID"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List reviews, optionally filtered by location."""
    reviews_path = _DATA_DIR / "reviews.jsonl"
    if not reviews_path.exists():
        return {"reviews": [], "total": 0}

    reviews = []
    with open(reviews_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                review = json.loads(line)
                if location_id and review.get("location_id") != location_id:
                    continue
                reviews.append(review)
            except json.JSONDecodeError:
                continue

    total = len(reviews)
    reviews.reverse()  # Most recent first
    reviews = reviews[offset : offset + limit]

    return {"reviews": reviews, "total": total, "limit": limit, "offset": offset}
