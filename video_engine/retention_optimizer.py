"""
Retention Optimizer — Closed-loop video performance feedback.

Analyseert video performance data (TikTok/Instagram/YouTube analytics)
en optimaliseert toekomstige video-productieparameters automatisch.

Feedback loop:
1. Video wordt geproduceerd met bepaalde parameters (hook_duration, pacing, etc.)
2. Video wordt gepost op platform(s)
3. Analytics data wordt verzameld (views, retention curve, engagement)
4. Optimizer berekent welke parameters correleren met hoge performance
5. Toekomstige video's worden geoptimaliseerd op basis van learnings

Data model:
- VideoRecord: productie-metadata + performance metrics
- RetentionProfile: per-seconde retentie curve
- OptimizationInsight: geleerde parameter-adjustments
"""

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger
from utils.runtime_paths import ensure_dir, get_runtime_data_dir

# Persistent storage voor analytics data
DATA_DIR = ensure_dir(get_runtime_data_dir("analytics"))


@dataclass
class VideoRecord:
    """Productie-metadata + performance van één video."""

    video_id: str
    created_at: float = field(default_factory=time.time)

    # Productie parameters
    hook_duration_sec: float = 0.0
    total_duration_sec: float = 0.0
    scene_count: int = 0
    scene_types: list[str] = field(default_factory=list)
    hook_text: str = ""
    cta_text: str = ""
    music_track: str = ""
    music_bpm: float = 0.0
    has_beat_sync: bool = False
    has_talking_head: bool = False
    sfx_count: int = 0
    caption_style: str = "triple_layer"
    niche: str = ""
    app_name: str = ""

    # Platform deployment
    platform: str = ""  # tiktok, reels, shorts
    post_url: str = ""
    posted_at: float = 0.0

    # Performance metrics (ingevuld na analytics import)
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    saves: int = 0
    avg_watch_time_sec: float = 0.0
    completion_rate: float = 0.0  # % die hele video kijkt
    retention_3s: float = 0.0  # % nog kijkend na 3 seconden (hook effectiviteit)
    retention_curve: list[float] = field(default_factory=list)  # per-seconde %
    ctr: float = 0.0  # click-through rate (link clicks / views)
    engagement_rate: float = 0.0  # (likes+comments+shares) / views

    # Variant info
    variant_group: str = ""  # voor A/B test grouping
    is_control: bool = False

    def performance_score(self) -> float:
        """Gewogen performance score (0-100).

        Weging:
        - retention_3s: 30% (hook effectiviteit)
        - completion_rate: 25% (content kwaliteit)
        - engagement_rate: 25% (audience connection)
        - ctr: 20% (conversion effectiviteit)
        """
        return (
            self.retention_3s * 0.30
            + self.completion_rate * 100 * 0.25
            + min(self.engagement_rate * 1000, 100) * 0.25  # normalize engagement
            + min(self.ctr * 1000, 100) * 0.20  # normalize CTR
        )


@dataclass
class OptimizationInsight:
    """Geleerde parameter-adjustment op basis van data."""

    parameter: str  # bijv. "hook_duration_sec", "music_bpm", "sfx_count"
    optimal_value: Any
    confidence: float  # 0-1, gebaseerd op sample size
    improvement_pct: float  # verwachte verbetering vs baseline
    sample_size: int
    insight_text: str  # menselijk leesbare uitleg
    created_at: float = field(default_factory=time.time)


class RetentionOptimizer:
    """Closed-loop optimizer voor video performance.

    Verzamelt performance data, berekent correlaties, en geeft
    geoptimaliseerde productie-parameters terug.
    """

    def __init__(self, data_dir: Path | None = None):
        self.data_dir = data_dir or DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.records_file = self.data_dir / "video_records.json"
        self.insights_file = self.data_dir / "optimization_insights.json"

    # ── Data Management ──────────────────────────────────────────

    def save_record(self, record: VideoRecord) -> None:
        """Sla video record op (productie + performance data)."""
        records = self._load_records()
        # Update of voeg toe
        records = [r for r in records if r["video_id"] != record.video_id]
        records.append(asdict(record))
        self._save_records(records)
        logger.info(f"[Retention] Record opgeslagen: {record.video_id}")

    def update_performance(
        self, video_id: str, **metrics: Any,
    ) -> bool:
        """Update performance metrics van een bestaande video.

        Gebruik:
            optimizer.update_performance("vid_abc123",
                views=15000, likes=850, retention_3s=78.5,
                completion_rate=0.42, ctr=0.023,
            )
        """
        records = self._load_records()
        for rec in records:
            if rec["video_id"] == video_id:
                rec.update(metrics)
                # Herbereken engagement rate als we views + interactions hebben
                views = rec.get("views", 0)
                if views > 0:
                    interactions = rec.get("likes", 0) + rec.get("comments", 0) + rec.get("shares", 0)
                    rec["engagement_rate"] = interactions / views
                self._save_records(records)
                logger.info(f"[Retention] Performance updated: {video_id}")
                return True
        logger.warning(f"[Retention] Video niet gevonden: {video_id}")
        return False

    def import_tiktok_analytics(self, csv_path: Path) -> int:
        """Import TikTok analytics CSV en koppel aan video records.

        TikTok CSV format: Date, Video Views, Likes, Comments, Shares,
        Average Watch Time, Traffic Source Types, etc.

        Returns: aantal geüpdatete records.
        """
        import csv

        if not csv_path.exists():
            return 0

        updated = 0
        try:
            with open(csv_path, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Match op basis van post URL of video ID
                    video_url = row.get("Video Link", row.get("video_link", ""))
                    views = int(row.get("Video Views", row.get("views", 0)) or 0)
                    likes = int(row.get("Likes", row.get("likes", 0)) or 0)
                    comments = int(row.get("Comments", row.get("comments", 0)) or 0)
                    shares = int(row.get("Shares", row.get("shares", 0)) or 0)
                    avg_watch = float(row.get("Average Watch Time", row.get("avg_watch_time", 0)) or 0)

                    if video_url:
                        records = self._load_records()
                        for rec in records:
                            if rec.get("post_url") == video_url:
                                rec["views"] = views
                                rec["likes"] = likes
                                rec["comments"] = comments
                                rec["shares"] = shares
                                rec["avg_watch_time_sec"] = avg_watch
                                if views > 0:
                                    rec["engagement_rate"] = (likes + comments + shares) / views
                                updated += 1
                        self._save_records(records)

        except Exception as e:
            logger.error(f"[Retention] TikTok import mislukt: {e}")

        logger.info(f"[Retention] TikTok analytics import: {updated} records geüpdatet")
        return updated

    # ── Analysis & Optimization ──────────────────────────────────

    def analyze(self, min_records: int = 5) -> list[OptimizationInsight]:
        """Analyseer alle video records en genereer optimization insights.

        Vergelijkt top-performers met underperformers voor elk parameter.
        Minimaal `min_records` nodig voor betrouwbare analyse.
        """
        records = self._load_records()
        records_with_views = [r for r in records if r.get("views", 0) > 100]

        if len(records_with_views) < min_records:
            logger.info(
                f"[Retention] Te weinig data voor analyse: "
                f"{len(records_with_views)}/{min_records} records met views"
            )
            return []

        # Bereken performance score voor elk record
        scored = []
        for r in records_with_views:
            rec = VideoRecord(**{k: v for k, v in r.items() if k in VideoRecord.__dataclass_fields__})
            scored.append((rec, rec.performance_score()))

        # Sorteer op performance
        scored.sort(key=lambda x: x[1], reverse=True)

        # Top 30% vs bottom 30%
        n = len(scored)
        top_n = max(2, n * 30 // 100)
        top = [s[0] for s in scored[:top_n]]
        bottom = [s[0] for s in scored[-top_n:]]

        insights = []

        # Analyseer numerieke parameters
        numeric_params = [
            ("hook_duration_sec", "Hook duur"),
            ("total_duration_sec", "Totale video duur"),
            ("scene_count", "Aantal scenes"),
            ("music_bpm", "Muziek BPM"),
            ("sfx_count", "Aantal sound effects"),
        ]

        for param, label in numeric_params:
            top_vals = [getattr(r, param, 0) for r in top if getattr(r, param, 0) > 0]
            bot_vals = [getattr(r, param, 0) for r in bottom if getattr(r, param, 0) > 0]

            if top_vals and bot_vals:
                top_avg = sum(top_vals) / len(top_vals)
                bot_avg = sum(bot_vals) / len(bot_vals)

                if bot_avg > 0:
                    diff_pct = ((top_avg - bot_avg) / bot_avg) * 100
                    confidence = min(1.0, len(top_vals) / 10)

                    if abs(diff_pct) > 10:  # Alleen significante verschillen
                        insights.append(OptimizationInsight(
                            parameter=param,
                            optimal_value=round(top_avg, 2),
                            confidence=confidence,
                            improvement_pct=round(diff_pct, 1),
                            sample_size=len(top_vals) + len(bot_vals),
                            insight_text=(
                                f"Top-performers hebben gemiddeld {label}={top_avg:.1f} "
                                f"vs {bot_avg:.1f} bij underperformers "
                                f"({diff_pct:+.0f}% verschil)"
                            ),
                        ))

        # Analyseer boolean parameters
        bool_params = [
            ("has_beat_sync", "Beat-sync"),
            ("has_talking_head", "Talking head"),
        ]

        for param, label in bool_params:
            top_true = sum(1 for r in top if getattr(r, param, False))
            bot_true = sum(1 for r in bottom if getattr(r, param, False))
            top_pct = top_true / len(top) * 100 if top else 0
            bot_pct = bot_true / len(bottom) * 100 if bottom else 0

            diff = top_pct - bot_pct
            if abs(diff) > 15:
                insights.append(OptimizationInsight(
                    parameter=param,
                    optimal_value=top_pct > 50,
                    confidence=min(1.0, len(scored) / 15),
                    improvement_pct=round(diff, 1),
                    sample_size=len(top) + len(bottom),
                    insight_text=(
                        f"{label}: {top_pct:.0f}% van top-performers vs "
                        f"{bot_pct:.0f}% van underperformers"
                    ),
                ))

        # A/B test analyse: vergelijk varianten binnen dezelfde groep
        ab_insights = self._analyze_ab_tests(records_with_views)
        insights.extend(ab_insights)

        # Sla insights op
        self._save_insights(insights)
        logger.info(f"[Retention] {len(insights)} optimization insights gegenereerd")

        return insights

    def get_optimized_params(self) -> dict[str, Any]:
        """Geef geoptimaliseerde productie-parameters terug.

        Combineert opgeslagen insights met default values.
        Alleen insights met confidence > 0.5 worden toegepast.
        """
        defaults = {
            "hook_duration_sec": 3.0,
            "total_duration_sec": 28.0,
            "scene_count": 4,
            "music_bpm_range": (90, 120),
            "sfx_count": 5,
            "has_beat_sync": True,
            "has_talking_head": False,
            "hook_pacing_weight": 0.80,
            "cta_pacing_weight": 0.85,
        }

        insights = self._load_insights()
        applied = 0

        for insight in insights:
            if insight.get("confidence", 0) > 0.5:
                param = insight["parameter"]
                if param in defaults:
                    defaults[param] = insight["optimal_value"]
                    applied += 1

        if applied:
            logger.info(f"[Retention] {applied} optimizations toegepast")

        return defaults

    def get_performance_report(self) -> dict:
        """Genereer performance rapport over alle video's.

        Returns dict met:
        - total_videos, total_views, avg_engagement
        - top_performers (top 5)
        - worst_performers (bottom 5)
        - trend (improving/declining/stable)
        - insights (optimization suggestions)
        """
        records = self._load_records()
        records_with_views = [r for r in records if r.get("views", 0) > 100]

        if not records_with_views:
            return {
                "total_videos": len(records),
                "total_views": 0,
                "message": "Nog geen performance data beschikbaar. "
                           "Upload analytics of gebruik update_performance().",
            }

        # Score alles
        scored = []
        for r in records_with_views:
            rec = VideoRecord(**{k: v for k, v in r.items() if k in VideoRecord.__dataclass_fields__})
            scored.append({
                "video_id": rec.video_id,
                "views": rec.views,
                "engagement_rate": round(rec.engagement_rate * 100, 2),
                "retention_3s": rec.retention_3s,
                "completion_rate": round(rec.completion_rate * 100, 1),
                "score": round(rec.performance_score(), 1),
                "platform": rec.platform,
                "hook_text": rec.hook_text[:50],
            })

        scored.sort(key=lambda x: x["score"], reverse=True)

        total_views = sum(r.get("views", 0) for r in records_with_views)
        avg_engagement = sum(
            r.get("engagement_rate", 0) for r in records_with_views
        ) / len(records_with_views) * 100

        # Trend: vergelijk eerste helft vs tweede helft (chronologisch)
        records_with_views.sort(key=lambda r: r.get("created_at", 0))
        half = len(records_with_views) // 2
        if half >= 2:
            early_avg = sum(
                VideoRecord(**{k: v for k, v in r.items() if k in VideoRecord.__dataclass_fields__}).performance_score()
                for r in records_with_views[:half]
            ) / half
            late_avg = sum(
                VideoRecord(**{k: v for k, v in r.items() if k in VideoRecord.__dataclass_fields__}).performance_score()
                for r in records_with_views[half:]
            ) / (len(records_with_views) - half)
            trend_pct = ((late_avg - early_avg) / early_avg * 100) if early_avg > 0 else 0
            trend = "improving" if trend_pct > 5 else ("declining" if trend_pct < -5 else "stable")
        else:
            trend = "insufficient_data"
            trend_pct = 0

        return {
            "total_videos": len(records),
            "videos_with_data": len(records_with_views),
            "total_views": total_views,
            "avg_engagement_pct": round(avg_engagement, 2),
            "top_performers": scored[:5],
            "worst_performers": scored[-5:] if len(scored) > 5 else [],
            "trend": trend,
            "trend_change_pct": round(trend_pct, 1),
            "insights": [asdict(i) for i in self.analyze(min_records=3)],
        }

    # ── A/B Test Analysis ────────────────────────────────────────

    def _analyze_ab_tests(self, records: list[dict]) -> list[OptimizationInsight]:
        """Analyseer A/B test groepen en bepaal winnaars."""
        insights = []

        # Groepeer per variant_group
        groups: dict[str, list[dict]] = {}
        for r in records:
            vg = r.get("variant_group", "")
            if vg:
                groups.setdefault(vg, []).append(r)

        for group_name, variants in groups.items():
            if len(variants) < 2:
                continue

            # Bereken score per variant
            scored = []
            for v in variants:
                rec = VideoRecord(**{k: val for k, val in v.items() if k in VideoRecord.__dataclass_fields__})
                scored.append((v, rec.performance_score()))

            scored.sort(key=lambda x: x[1], reverse=True)
            winner = scored[0][0]
            loser = scored[-1][0]

            if scored[0][1] > 0 and scored[-1][1] > 0:
                diff = ((scored[0][1] - scored[-1][1]) / scored[-1][1]) * 100
                insights.append(OptimizationInsight(
                    parameter=f"ab_test_{group_name}",
                    optimal_value=winner.get("hook_text", "")[:50],
                    confidence=min(1.0, min(v.get("views", 0) for v in variants) / 1000),
                    improvement_pct=round(diff, 1),
                    sample_size=len(variants),
                    insight_text=(
                        f"A/B test '{group_name}': winnaar hook "
                        f"'{winner.get('hook_text', '')[:30]}' "
                        f"scoort {diff:.0f}% beter"
                    ),
                ))

        return insights

    # ── Persistence ──────────────────────────────────────────────

    def _load_records(self) -> list[dict]:
        if self.records_file.exists():
            try:
                return json.loads(self.records_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, IOError):
                return []
        return []

    def _save_records(self, records: list[dict]) -> None:
        self.records_file.write_text(
            json.dumps(records, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _load_insights(self) -> list[dict]:
        if self.insights_file.exists():
            try:
                return json.loads(self.insights_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, IOError):
                return []
        return []

    def _save_insights(self, insights: list[OptimizationInsight]) -> None:
        self.insights_file.write_text(
            json.dumps([asdict(i) for i in insights], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
