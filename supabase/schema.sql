CREATE TABLE IF NOT EXISTS apps (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  url TEXT,
  description TEXT,
  target_audience TEXT,
  usp TEXT,
  niche TEXT DEFAULT 'health',
  active_channels TEXT[] DEFAULT ARRAY['tiktok'],
  active BOOLEAN DEFAULT true,
  tenant_id TEXT DEFAULT 'default',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  metadata JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS brand_memory (
  app_id TEXT PRIMARY KEY REFERENCES apps(id) ON DELETE CASCADE,
  niche TEXT,
  tone_of_voice TEXT,
  creator_persona TEXT,
  top_performing_hooks TEXT[],
  avoided_topics TEXT[],
  performance_history JSONB DEFAULT '{}'::jsonb,
  learned_insights JSONB DEFAULT '[]'::jsonb,
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  memory JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS campaigns (
  id TEXT PRIMARY KEY,
  app_id TEXT REFERENCES apps(id) ON DELETE CASCADE,
  tenant_id TEXT DEFAULT 'default',
  platform TEXT DEFAULT 'tiktok',
  status TEXT DEFAULT 'draft',
  display_name TEXT,
  idea JSONB,
  script JSONB,
  caption JSONB,
  viral_score JSONB,
  video_url TEXT,
  video_metadata JSONB DEFAULT '{}'::jsonb,
  total_cost_usd DECIMAL(6,4) DEFAULT 0,
  cost_breakdown JSONB DEFAULT '[]'::jsonb,
  approved_by TEXT,
  approval_notes TEXT,
  approved_at TIMESTAMPTZ,
  post_id TEXT,
  published_at TIMESTAMPTZ,
  experiment_id TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS cost_records (
  id SERIAL PRIMARY KEY,
  tenant_id TEXT DEFAULT 'default',
  campaign_id TEXT REFERENCES campaigns(id) ON DELETE CASCADE,
  app_id TEXT,
  source TEXT,
  amount_usd DECIMAL(6,4),
  model TEXT,
  tokens_input INTEGER,
  tokens_output INTEGER,
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS maturity_snapshots (
  id SERIAL PRIMARY KEY,
  app_id TEXT REFERENCES apps(id) ON DELETE CASCADE,
  tenant_id TEXT DEFAULT 'default',
  overall_score DECIMAL(4,1),
  dimensions JSONB,
  computed_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS experiments (
  id TEXT PRIMARY KEY,
  app_id TEXT REFERENCES apps(id) ON DELETE CASCADE,
  tenant_id TEXT DEFAULT 'default',
  dimension TEXT,
  status TEXT DEFAULT 'running',
  variants JSONB,
  selected_variant_id TEXT,
  selected_by TEXT,
  conclusion JSONB,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  concluded_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS audit_log (
  id SERIAL PRIMARY KEY,
  app_id TEXT,
  tenant_id TEXT DEFAULT 'default',
  job_type TEXT,
  outcome TEXT,
  details JSONB,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS alerts (
  id TEXT PRIMARY KEY,
  tenant_id TEXT DEFAULT 'default',
  severity TEXT,
  message TEXT,
  acknowledged BOOLEAN DEFAULT false,
  resolved BOOLEAN DEFAULT false,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pipeline_progress (
  campaign_id TEXT PRIMARY KEY REFERENCES campaigns(id) ON DELETE CASCADE,
  step INTEGER DEFAULT 0,
  message TEXT,
  percentage INTEGER DEFAULT 0,
  status TEXT DEFAULT 'running',
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_campaigns_app ON campaigns(app_id);
CREATE INDEX IF NOT EXISTS idx_campaigns_status ON campaigns(status);
CREATE INDEX IF NOT EXISTS idx_campaigns_tenant ON campaigns(tenant_id);
CREATE INDEX IF NOT EXISTS idx_cost_records_tenant_date ON cost_records(tenant_id, created_at);

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_campaigns_updated_at ON campaigns;
CREATE TRIGGER trg_campaigns_updated_at
BEFORE UPDATE ON campaigns
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_pipeline_progress_updated_at ON pipeline_progress;
CREATE TRIGGER trg_pipeline_progress_updated_at
BEFORE UPDATE ON pipeline_progress
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();
