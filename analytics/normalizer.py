"""
Metrics Normalizer

Zet ruwe TikTok metrics om naar genormaliseerde ratios.
Alle output-waarden zijn platform-agnostisch en vergelijkbaar over tijd.

DESIGN BESLISSINGEN:
- Alle ratios zijn t.o.v. views (niet impressions) — consistenter over platforms
- Watch time percentage gebruik avg_watch_time / video_duration
- Completion rate wordt geschat: als avg_watch_pct > 0.85 → hoge completion
- Amplification rate (shares/reach) meet virality-potentie
"""

from analytics.models import RawTikTokMetrics, NormalizedMetrics


def normalize(raw: RawTikTokMetrics) -> NormalizedMetrics:
    """
    Zet ruwe metrics om naar genormaliseerde ratios.

    Args:
        raw: RawTikTokMetrics van de fetcher

    Returns:
        NormalizedMetrics met alle ratios berekend
    """
    views = max(raw.views, 1)  # Voorkom deling door nul
    reach = max(raw.reach, 1)
    impressions = max(raw.impressions, 1)
    duration = max(raw.video_duration_sec, 1)

    # Basisratios
    like_rate = raw.likes / views
    comment_rate = raw.comments / views
    share_rate = raw.shares / views
    save_rate = raw.saves / views
    profile_visit_rate = raw.profile_visits / views

    # Engagement = alle actieve interacties / views
    engagement_rate = (raw.likes + raw.comments + raw.shares + raw.saves) / views

    # Watch time
    avg_watch_time_pct = min(raw.avg_watch_time_sec / duration, 1.0)

    # Completion rate schatting:
    # TikTok geeft geen directe completion rate via basis API.
    # Benadering: als avg_watch_pct > 0.75 → completion ~= avg_watch_pct * 1.15
    # Logica: bij hoge avg watch time zit de verdeling sterk naar voltooiing
    if avg_watch_time_pct >= 0.75:
        completion_rate = min(avg_watch_time_pct * 1.12, 1.0)
    elif avg_watch_time_pct >= 0.50:
        completion_rate = avg_watch_time_pct * 0.90
    else:
        completion_rate = avg_watch_time_pct * 0.70

    # Distributie: welk % van impressions leidt tot views?
    reach_rate = raw.reach / impressions if raw.impressions > 0 else 0.0

    # Virality proxy: shares t.o.v. het totale bereik
    amplification_rate = raw.shares / reach if raw.reach > 0 else share_rate

    return NormalizedMetrics(
        engagement_rate=round(engagement_rate, 5),
        like_rate=round(like_rate, 5),
        comment_rate=round(comment_rate, 5),
        share_rate=round(share_rate, 5),
        save_rate=round(save_rate, 5),
        profile_visit_rate=round(profile_visit_rate, 5),
        avg_watch_time_pct=round(avg_watch_time_pct, 4),
        completion_rate=round(completion_rate, 4),
        reach_rate=round(reach_rate, 4),
        amplification_rate=round(amplification_rate, 5),
    )
