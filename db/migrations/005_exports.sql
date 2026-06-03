CREATE TABLE IF NOT EXISTS "sales-service".exports (
  id BIGSERIAL PRIMARY KEY,
  exported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  row_count INTEGER,
  output_path TEXT,
  notes TEXT,
  run_id UUID REFERENCES "sales-service".runs(id) ON DELETE SET NULL,
  site_id TEXT
);

CREATE INDEX IF NOT EXISTS exports_run_id_idx ON "sales-service".exports (run_id);
