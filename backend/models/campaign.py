"""
Datamodellen voor campagnes en content.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class CampaignStatus(str, Enum):
    DRAFT = "draft"
    GENERATING = "generating"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"


class ApprovalDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_CHANGES = "request_changes"


class Scene(BaseModel):
    scene_number: int
    type: str  # hook | problem | solution | cta
    duration_sec: int
    voiceover: str = ""
    on_screen_text: str = ""
    visual_description: str = ""
    notes: str = ""


class Script(BaseModel):
    script_id: str = Field(default_factory=lambda: str(uuid4())[:8])
    title: str
    total_duration_sec: int
    video_type: str
    scenes: list[Scene]
    full_voiceover_text: str = ""
    production_notes: str = ""


class CaptionData(BaseModel):
    caption: str
    hook_line: str
    hashtags: list[str]
    platform: str


class CampaignBundle(BaseModel):
    """Alles wat een campagne bevat — bundel die ter goedkeuring wordt aangeboden."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    app_id: str
    tenant_id: str = "default"   # Tenant isolatie — "default" = backward compat
    platform: str = "tiktok"
    status: CampaignStatus = CampaignStatus.DRAFT

    # Content
    idea: dict[str, Any] = {}
    script: dict[str, Any] = {}
    caption: dict[str, Any] = {}
    video_path: str | None = None
    thumbnail_path: str | None = None

    # Viral score (algoritme-check)
    viral_score: dict[str, Any] | None = None

    # TikTok post ID na publicatie (voor analytics koppeling)
    post_id: str | None = None

    # Experiment koppeling (optioneel — alleen als EXPERIMENTS_ENABLED=true)
    experiment_id: str | None = None

    # Kosten tracking
    total_cost_usd: float = 0.0

    # Tijdstempels
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    display_name: str | None = None
    approved_at: datetime | None = None
    published_at: datetime | None = None

    # Goedkeuring
    approved_by: str | None = None
    approval_notes: str | None = None
    rejection_reason: str | None = None

    # Geplande publicatietijd
    scheduled_for: datetime | None = None

    # Post type — bepaalt welke media aanwezig is en hoe gepubliceerd wordt
    post_type: str = "video"  # "text" | "photo" | "video"

    class Config:
        use_enum_values = True


class ApprovalRequest(BaseModel):
    campaign_id: str
    decision: ApprovalDecision
    notes: str = ""
    scheduled_for: datetime | None = None
