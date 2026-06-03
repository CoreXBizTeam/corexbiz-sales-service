CREATE TABLE IF NOT EXISTS "sales-service".runs (
  id UUID PRIMARY KEY,
  site_id TEXT NOT NULL,
  site_url TEXT,
  list_name TEXT,
  source_type TEXT NOT NULL,
  criteria JSONB NOT NULL DEFAULT '{}',
  notes TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'queued',
  error TEXT,
  message TEXT,
  webhook_url TEXT,
  webhook_sent_at TIMESTAMPTZ,
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS runs_site_status_idx
  ON "sales-service".runs (site_id, status);

CREATE INDEX IF NOT EXISTS runs_created_at_idx
  ON "sales-service".runs (created_at DESC);
