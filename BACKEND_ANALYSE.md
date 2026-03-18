# Backend Analyse — AY Marketing OS
> Gegenereerd op 2026-03-13 | Claude Sonnet 4.6

---

## 1. Architectuuroverzicht

```
┌────────────────────────────────────────────────────────────────────┐
│                  FastAPI Backend  (port 8000)                      │
│                                                                    │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  API Routers  (backend/api/)                                │   │
│  │  campaigns · approvals · analytics · experiments           │   │
│  │  health · maturity · costs · apps · settings               │   │
│  └───────────────────────────┬─────────────────────────────────┘   │
│                              │                                     │
│  ┌───────────────────────────▼─────────────────────────────────┐   │
│  │  Workflow Orchestration  (workflows/)                       │   │
│  │  campaign_pipeline · scheduler · feedback_loop             │   │
│  └──────┬───────────────────────────────────┬──────────────────┘   │
│         │                                   │                      │
│  ┌──────▼──────────┐              ┌──────────▼────────────────┐    │
│  │  AI Agents      │              │  Content Production       │    │
│  │  idea_generator │              │  VideoOrchestrator        │    │
│  │  script_writer  │              │  ProVideoProvider (best)  │    │
│  │  viral_checker  │              │  D-ID Provider (avatars)  │    │
│  │  caption_writer │              │  OpenAI Image Provider    │    │
│  │  analyst_agent  │              │  FFmpeg Provider (gratis) │    │
│  │  brand_memory   │              └──────────┬────────────────┘    │
│  └──────┬──────────┘                         │                     │
│         │                                    │                     │
│  ┌──────▼────────────────────────────────────▼────────────────┐    │
│  │  Data Layer  (backend/repository/)                         │    │
│  │  FileCampaignRepository  ·  SqliteCampaignRepository       │    │
│  │  FileExperimentRepository  ·  FileMaturityRepository       │    │
│  │  Alle repositories wisselen via factory.py                 │    │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                    │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  Observability  (observability/)                            │   │
│  │  StructuredLogger · HealthChecker · AuditStore             │   │
│  │  AlertingService · RetryEngine · CorrelationID             │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                    │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  Security & Budget  (backend/)                              │   │
│  │  RateLimitMiddleware · SecurityHeadersMiddleware            │   │
│  │  AuthMiddleware · TenantRegistry · CostGuardrails          │   │
│  └─────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────┘

Externe diensten:
  Anthropic API   → ideeën, captions, kwaliteitsscore
  OpenAI API      → scripts, DALL-E video engine
  TikTok API      → publiceren + analytics ophalen
  ElevenLabs API  → voiceover (TTS)
  Pexels API      → stockmateriaal
  D-ID API        → avataarvideo
  Kling / Runway  → optionele video-generatie (uitgeschakeld)
  Supabase        → geconfigureerd, nog niet actief gebruikt

Dataopslag (lokaal bestandssysteem):
  data/campaigns/          → CampaignBundle JSON-bestanden
  data/experiments/        → A/B-testconfiguraties
  data/maturity/           → volwassenheidsscores
  data/brand_memory/       → learnings per app
  data/analytics/          → prestatiestatistieken TikTok
  data/audit/              → onveranderlijk auditspoor (JSONL)
  data/cost_tracking/      → dagelijkse budgetlogboeken
  data/health/             → component-healthsnapshots
  data/db/                 → SQLite (optioneel, REPO_BACKEND=sqlite)
  logs/                    → gestructureerde JSONL-logbestanden
```

---

## 2. Module-voor-module beschrijving

### 2.1 backend/main.py — Ingang
FastAPI-applicatie met lifespan (startup/shutdown), CORS-middleware, static file
serving voor de frontend en mounting van alle API-routers. Bevat geen
bedrijfslogica — puur orkestratielaag.

### 2.2 backend/auth.py — Authenticatie
API-sleutelvalidatie via de `X-API-Key`-header. Gebruikt `hmac.compare_digest`
voor timing-veilige vergelijking (bescherming tegen timing attacks). Auth is
automatisch uitgeschakeld in ENVIRONMENT=development.

### 2.3 backend/cost_guardrails.py — Budgetbewaking
Per-tenant budgetten (per campagne, per dag, per maand). Schrijft kosten atomisch
naar `data/cost_tracking/daily_{datum}.json`. Draadsveilig via per-tenant locks.

### 2.4 backend/models/ — Gegevensschema's
Alle Pydantic-modellen:
- `campaign.py`: `CampaignStatus`, `Scene`, `Script`, `CampaignBundle`
- `tenant.py`: `TenantConfig`, `TenantRegistry`

### 2.5 backend/middleware/ — Beveiliging
- `rate_limit.py`: schuifvenster 120 req/min, 10 req/min voor zware endpoints
- `security_headers.py`: X-Content-Type-Options, X-Frame-Options, CSP, HSTS (productie)

### 2.6 backend/repository/ — Databeheer
Protocol-gebaseerde abstractielaag:
- `base.py`: interfaces (`ICampaignRepository`, `IExperimentRepository`, etc.)
- `factory.py`: kiest File vs SQLite op basis van `REPO_BACKEND` env-var
- `file_campaigns.py`: JSON-bestanden met atomische schrijfoperaties
- `sqlite_*.py`: WAL-modus, foreign keys, 10 s timeout

### 2.7 backend/api/ — Endpoints
| Router | Hoofdfuncties |
|--------|--------------|
| campaigns.py | ideeën genereren, pipeline starten, SSE-voortgang, campagne ophalen |
| approvals.py | goedkeuringslijst, beslissing verwerken |
| analytics.py | prestatiestatistieken per app/periode |
| experiments.py | A/B-test beheer |
| health.py | liveness, readiness, auditlog, alerts |
| maturity.py | volwassenheidsscores en -geschiedenis |
| costs.py | budgetverbruik per dag/maand |
| apps.py | app-registratie en -beheer |
| settings.py | systeeminstellingen |

### 2.8 backend/services/approval_service.py — Goedkeuringslogica
- `process_approval()`: valideert status=PENDING_APPROVAL, werkt bundle bij
- `_publish_now()`: harde veiligheidscheck (status=APPROVED + approved_by vereist)
- `try_auto_approve()`: publiceert automatisch als `APPROVAL_REQUIRED=false` of
  `viral_score >= AUTO_APPROVE_THRESHOLD`

### 2.9 agents/ — AI-agenten
Alle agenten erven van `BaseAgent`:
- Laadt provider en model uit `configs/model_config.json`
- `_call_api()` met @retry (3 pogingen, exponentiële backoff via Tenacity)
- Lazy loading van Anthropic/OpenAI-clients
- Promptsjablonen in `prompts/tasks/*.txt`

| Agent | Verantwoordelijkheid |
|-------|---------------------|
| IdeaGeneratorAgent | 5 campagne-ideeën op basis van brand memory |
| ScriptWriterAgent | scènes met voiceover, on-screen tekst |
| ViralCheckerAgent | virality score 0-100, herschrijft tot max 3x |
| CaptionWriterAgent | caption + hashtags afgestemd op platform |
| AnalystAgent | leerpunten uit prestatiegegevens extraheren |
| BrandMemoryAgent | learnings opslaan en ophalen per app |

### 2.10 workflows/campaign_pipeline.py — Hoofdpijplijn
Stappen in volgorde:
1. Budgetcheck (`CostGuardrails.check_budget`)
2. App-context laden (`configs/app_registry.json`)
3. Brand memory laden (`data/brand_memory/{app_id}.json`)
4. Ideeën genereren (`IdeaGeneratorAgent`)
5. Script schrijven (`ScriptWriterAgent`)
6. Viraliteitscheck (`ViralCheckerAgent`) — herschrijft tot 3× als score < 80
7. Video produceren (`VideoOrchestrator`)
8. Caption schrijven (`CaptionWriterAgent`)
9. Bundle opslaan met status=PENDING_APPROVAL
10. Kosten vastleggen

### 2.11 workflows/scheduler.py — Geplande taken
APScheduler met CronTrigger:
- 06:00 — token refresh TikTok
- 07:00 + 19:00 — content publiceren
- Elk uur — analytics ophalen + feedback injecteren
- Dagelijks — maturity evaluatie
- Wekelijks — diepe analyse
- Circuit breaker: 3 opeenvolgende fouten → 6 uur pauze

### 2.12 workflows/feedback_loop.py — Analyticslus
TikTok metrics → normalisatie → PerformanceScore → AnalystAgent → learnings →
brand memory update → beïnvloedt volgende campagne-ideeën.

### 2.13 channels/tiktok/ — TikTok-koppeling
- `publisher.py`: uploadt video, post naar TikTok (INBOX of DIRECT_POST modus)
- `analytics_fetcher.py`: haalt views, likes, watch-time, etc. op

### 2.14 video_engine/ — Video-productie
`VideoOrchestrator` selecteert provider op basis van beschikbare API-sleutels:
1. D-ID (avataarvideo, beste kwaliteit)
2. ProVideoProvider (Pexels stockvideo + ElevenLabs voice)
3. OpenAIImageProvider (DALL-E afbeeldingen + FFmpeg)
4. FFmpegProvider (gratis fallback: kleurverloop + tekst)

### 2.15 quality/ — Kwaliteitscontrole
`AssetQualityScorer` beoordeelt elk script op 4 dimensies:
- hook_strength (35%), clarity (25%), brand_fit (20%), retention_potential (20%)
- Drempel blokkeren: compositescore < 55 of een dimensie < 40

### 2.16 analytics/ — Prestatieanalyse
- `normalizer.py`: ruwe TikTok-metrieken → genormaliseerde rates
- `scorer.py`: `PerformanceScore` (0-100 composite)
- `learning_engine.py`: patronen extraheren, betrouwbaarheid berekenen
- `feedback_injector.py`: learnings terugschrijven naar brand memory

### 2.17 experiments/ — A/B-testen
- Dimensies: hook_type, cta_type, caption_style, video_format, posting_window
- Lifecycle: pending → measuring → concluded
- Winnaar bepaald via `causal_confidence`

### 2.18 maturity/ — Systeemvolwassenheid
Score op 5 dimensies (gewogen):
- replication 25%, prediction 20%, delta 20%, adoption 20%, stability 15%
- Status: EARLY → VALIDATED → INTERN_VOLWASSEN (vereist composite ≥ 75)

### 2.19 observability/ — Waarneembaarheid
- `logger.py`: JSONL-logbestanden (system, errors, scheduler, audit)
- `health_checker.py`: componentmonitoring (TikTok, Anthropic, OpenAI, filesystem)
- `audit_store.py`: write-once auditspoor per maand (365 dagen bewaard)
- `alerting.py`: kritieke meldingen bij fouten
- `correlation.py`: correlatie-ID voor request tracing

### 2.20 utils/file_io.py — Atomische I/O
`atomic_write_json`: schrijft naar `.tmp`, dan `os.replace()`. Gegarandeerd
geen gedeeltelijk geschreven bestanden bij crashes. Thread-safe op POSIX en Windows.

---

## 3. Koppelingen tussen modules

```
campaign_pipeline.py
  ├── IdeaGeneratorAgent ──────────────► Anthropic API
  ├── ScriptWriterAgent ───────────────► OpenAI API
  ├── ViralCheckerAgent ───────────────► Anthropic API
  ├── CaptionWriterAgent ──────────────► OpenAI API
  ├── VideoOrchestrator ───────────────► Pexels + ElevenLabs + D-ID + FFmpeg
  ├── CostGuardrails ──────────────────► data/cost_tracking/
  ├── BrandMemory ─────────────────────► data/brand_memory/
  └── atomic_write_json ───────────────► data/campaigns/

approval_service.py
  ├── FileCampaignRepository ──────────► data/campaigns/
  ├── TikTokPublisher ─────────────────► TikTok API
  └── AuditStore ──────────────────────► data/audit/

scheduler.py
  ├── feedback_loop.py
  │     ├── TikTokAnalyticsFetcher ────► TikTok API
  │     ├── AnalystAgent ─────────────► Anthropic API
  │     └── LearningEngine ───────────► data/analytics/
  ├── approval_service.py (publicatie)
  └── maturity/evaluator.py ───────────► data/maturity/

campaigns.py (API router)
  ├── campaign_pipeline.run_pipeline() (achtergrondtaak)
  ├── IdeaGeneratorAgent (direct voor /generate-ideas)
  └── SSE progress store (in-memory dict)

health.py (API router)
  ├── HealthChecker ───────────────────► alle externe diensten
  ├── AuditStore ──────────────────────► data/audit/
  └── AlertingService ─────────────────► data/health/
```

---

## 4. Datadoorstroming (drie kernscenario's)

### Scenario A — Campagne aanmaken
```
Dashboard → POST /api/campaigns/start
  → run_pipeline() in achtergrond
    → IdeaGeneratorAgent (Anthropic)
    → ScriptWriterAgent (OpenAI)
    → ViralCheckerAgent (Anthropic, max 3×)
    → VideoOrchestrator → provider → MP4
    → CaptionWriterAgent (OpenAI)
    → bundle opslaan (status=PENDING_APPROVAL)
  → SSE stream → Dashboard toont voortgang
```

### Scenario B — Goedkeuren en publiceren
```
Dashboard → POST /api/approvals/decide { decision: "approve" }
  → approval_service.process_approval()
    → bundle.status = APPROVED
    → TikTokPublisher.publish()
      → TikTok init_upload + chunks
      → bundle.post_id = response.post_id
      → bundle.status = PUBLISHED
    → AuditStore.write() → audit JSONL
```

### Scenario C — Analytics terugkoppeling
```
Scheduler (elk uur)
  → TikTokAnalyticsFetcher → RawTikTokMetrics
  → normalizer → NormalizedMetrics
  → scorer → PerformanceScore (0-100)
  → AnalystAgent (Anthropic) → LearningEntry[]
  → LearningEngine → brand_memory bijwerken
  → Volgende IdeaGeneratorAgent-aanroep gebruikt nieuwe learnings
```

---

## 5. Kwaliteitsbeoordeling

> Versie na optimalisatie — 2026-03-13

### 5.1 Architectuur  ★★★★★  UITSTEKEND

| Aspect | Oordeel | Toelichting |
|--------|---------|-------------|
| Scheiding van verantwoordelijkheden | Uitstekend | Routers, services, repository, agents strak gescheiden |
| Repository-abstractie | Goed | Protocol-gebaseerd, wisselen File↔SQLite zonder codewijziging |
| Provider-patroon video-engine | Uitstekend | Graceful degradation: D-ID → ProVideo → OpenAI → FFmpeg |
| Multi-tenant ondersteuning | Goed | Geïmplementeerd + tenant-validatie in API toegevoegd |
| Circuit breaker in scheduler | Goed | 3 fouten → 6 uur pauze, PID-file aanwezig |
| Per-app pipeline lock | Uitstekend | Dubbele gelijktijdige runs geblokkeerd (nieuw) |

### 5.2 Beveiliging  ★★★★☆  GOED

| Bevinding | Status | Uitleg |
|-----------|--------|--------|
| ~~`.env` schrijven tijdens runtime~~ | ✅ OPGELOST | Tokens atomisch in `data/tokens/tiktok.json` |
| `.env` in versiecontrol | ⚠️ Restrisico | Voeg `.env` toe aan `.gitignore` |
| ~~Tenant-id niet gevalideerd in approval flow~~ | ✅ OPGELOST | `process_approval()` accepteert + valideert `tenant_id` |
| Tenant-app validatie op API | ✅ OPGELOST | `_assert_app_belongs_to_tenant()` op start + ideas |
| ~~Race condition `_get_lock`~~ | ✅ OPGELOST | `_locks_mutex` toegevoegd in `cost_guardrails.py` |
| Rate limiter in-memory | Acceptabel | Voldoende voor single-process; Redis bij scale-out |
| Timing-veilige auth ✓ | Uitstekend | `hmac.compare_digest` correct gebruikt |
| Security headers ✓ | Uitstekend | HSTS, X-Frame-Options, Permissions-Policy aanwezig |
| Pad-traversal preventie ✓ | Uitstekend | `_SAFE_ID` regex + agent-output validatie |
| Token bestand buiten webroot ✓ | Uitstekend | `data/tokens/` niet via static files bereikbaar |

### 5.3 Betrouwbaarheid  ★★★★★  UITSTEKEND

| Aspect | Oordeel | Toelichting |
|--------|---------|-------------|
| Atomische bestandsschrijving | Uitstekend | `atomic_write_json` via os.replace() |
| Retry met exponentiële backoff | Uitstekend | Tenacity op alle LLM-aanroepen |
| Foutafhandeling in pipeline | Uitstekend | Foutbundle opgeslagen + lock altijd vrijgegeven (finally) |
| SSE progress store TTL | ✅ OPGELOST | Achtergrondthread ruimt entries op na 30 min |
| Pipeline idempotentie | ✅ OPGELOST | Per-app threading.Lock — dubbele runs geblokkeerd |
| Proactieve token refresh | Uitstekend | Scheduler vernieuwt token dagelijks om 06:00 |
| Auditspoor write-once JSONL | Uitstekend | 365 dagen bewaard, per maand gesplitst |
| ~~JSON parse crash bij malformed LLM-output~~ | ✅ OPGELOST | Alle agents gebruiken `default={}` of `default=[]` |

### 5.4 Prestaties  ★★★★☆  GOED

| Aspect | Oordeel | Toelichting |
|--------|---------|-------------|
| Health snapshot 4 min gecached | Goed | Voorkomt herhaalde externe pings |
| Campaign list O(n) bestandsscan | Voldoende | Acceptabel tot ~500 campagnes; daarna SQLite aanzetten |
| Lazy client instantiatie | Goed | Geen verbinding bij opstart |
| SQLite WAL-modus ✓ | Goed | Betere lees-schrijf gelijktijdigheid |
| Token laden uit bestand | Goed | Sneller dan .env parse bij elke init |

### 5.5 Onderhoudbaarheid  ★★★★☆  GOED

| Aspect | Oordeel | Toelichting |
|--------|---------|-------------|
| Pydantic v2 doorheen het geheel | Goed | Sterke typering, automatische validatie |
| Gestructureerde logging (JSONL) | Uitstekend | Doorzoekbaar, correlatie-ID aanwezig |
| Promptsjablonen in aparte bestanden | Goed | Prompts bijwerken zonder codewijziging |
| Constanten centraal in constants.py | Goed | Geen magische getallen verspreid |
| Model-config JSON (niet hardgecodeerd) | Goed | Provider/model wisselen zonder deploy |
| Kostberekening per model | Goed | `_estimate_cost()` berekent tokens × prijs per model |

### 5.6 AI-pijplijn kwaliteit  ★★★★★  UITSTEKEND

| Aspect | Oordeel | Toelichting |
|--------|---------|-------------|
| Viraliteitsdrempel + herschrijflus | Goed | Max 3× poging, verhoogt gemiddelde score |
| Brand memory als feedback loop | Uitstekend | Learnings beïnvloeden nieuwe ideeën automatisch |
| Kwaliteitsscorer (4 dimensies, blokkeringsdrempel) | Goed | Voorkomt matige content |
| A/B-experimenten met causal confidence | Goed | Statistisch onderbouwde winnaar-selectie |
| Maturity scoring (5 dimensies) | Goed | Objectieve meting van systeemgroei |
| Kostberekening per agent | Goed | `_estimate_cost()` werkt per model/provider |
| JSON parse fallback | ✅ OPGELOST | Pipeline crasht niet meer bij malformed LLM-output |

---

## 6. Algeheel oordeel

```
┌──────────────────────────────────────────────────────────────────┐
│          Na heraudit + ronde 2 optimalisatie (2026-03-13)        │
│                                                                  │
│  Architectuur        ★★★★★  UITSTEKEND                          │
│  Beveiliging         ★★★★☆  GOED  (restrisico: .env in git)     │
│  Betrouwbaarheid     ★★★★★  UITSTEKEND                          │
│  Prestaties          ★★★★☆  GOED                                │
│  Onderhoudbaarheid   ★★★★☆  GOED                                │
│  AI-pijplijn         ★★★★★  UITSTEKEND                          │
│                                                                  │
│  ──────────────────────────────────────────────────────          │
│  Totaalcijfer:       ★★★★☆  GOED                                │
│                                                                  │
│  Alle kritieke issues (ronde 1 + 2) zijn opgelost.              │
│  Productie-proof voor single-process deployment.                 │
│  Enige restrisico's zijn laag en gedocumenteerd.                 │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## 7. Doorgevoerde verbeteringen

### Ronde 1 (2026-03-13)
| # | Bestand | Wijziging |
|---|---------|-----------|
| K1 | `channels/tiktok/publisher.py` | Token opslag verplaatst van `.env` naar `data/tokens/tiktok.json` (atomisch, race-vrij) |
| H1 | `agents/base_agent.py` | `_parse_json_response(default=)` — retourneert fallback bij malformed LLM-output |
| H2 | `backend/api/campaigns.py` | Achtergrondthread ruimt `_progress_store` op na 30 min TTL |
| H3 | `workflows/campaign_pipeline.py` | Per-app `threading.Lock` blokkeert gelijktijdige pipeline-runs |
| H4 | `backend/api/campaigns.py` | `_assert_app_belongs_to_tenant()` op `/start` en `/generate-ideas` |

### Ronde 2 — na heraudit (2026-03-13)
| # | Bestand | Wijziging |
|---|---------|-----------|
| A1 | `agents/viral_checker.py` | `_parse_json_response(raw, default={})` — agent crashte op `None` |
| A2 | `agents/script_writer.py` | `_parse_json_response(raw, default={})` — idem |
| A3 | `agents/idea_generator.py` | `_parse_json_response(raw, default=[])` — idem |
| A4 | `agents/caption_writer.py` | `_parse_json_response(raw, default={})` — idem |
| A5 | `backend/cost_guardrails.py` | `_get_lock()` beveiligd met `_locks_mutex` — race condition verwijderd |
| A6 | `backend/services/approval_service.py` | `process_approval()` accepteert nu `tenant_id` en geeft het door aan `load_bundle` + alle `save_bundle` aanroepen |
| A7 | `backend/api/approvals.py` | `/decide` endpoint geeft `tenant_id` query-param door aan `process_approval()` |

## 8. Resterende aandachtspunten (laag risico)

- **`.env` in git**: Voeg `.env` toe aan `.gitignore`, commit alleen `.env.example` met lege waarden
- **Quality scorer model hardgecodeerd**: `quality/scorer.py` gebruikt altijd `claude-haiku-4-5-20251001`, niet leesbaar uit `model_config.json`
- **Brand memory bestandsvergrendeling**: Bij gelijktijdige schrijfoperaties (meerdere analytics-runs tegelijk) kan data overschreven worden — acceptabel voor single-process deployment
- **Rate limiter**: In-memory is voldoende voor 1 proces; bij meerdere workers → Redis

---

## 8. Externe diensten samenvatting

| Dienst | Gebruik | Status |
|--------|---------|--------|
| Anthropic | Ideeën, captions, kwaliteitsscore, analyse | Actief |
| OpenAI | Scripts, DALL-E video | Actief |
| TikTok API | Publiceren, analytics | Actief (INBOX modus) |
| ElevenLabs | Voiceover TTS | Actief |
| Pexels | Stockvideo | Actief |
| D-ID | Avataarvideo | Configureerbaar |
| Kling AI | Video generatie | Uitgeschakeld |
| Runway ML | Video generatie | Uitgeschakeld |
| Supabase | Database | Geconfigureerd, ongebruikt |
