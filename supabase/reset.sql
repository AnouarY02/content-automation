DELETE FROM storage.objects
WHERE bucket_id IN ('campaign-videos', 'voice-previews', 'campaign-assets');

DELETE FROM storage.buckets
WHERE id IN ('campaign-videos', 'voice-previews', 'campaign-assets');

DROP TABLE IF EXISTS pipeline_progress CASCADE;
DROP TABLE IF EXISTS alerts CASCADE;
DROP TABLE IF EXISTS audit_log CASCADE;
DROP TABLE IF EXISTS experiments CASCADE;
DROP TABLE IF EXISTS maturity_snapshots CASCADE;
DROP TABLE IF EXISTS cost_records CASCADE;
DROP TABLE IF EXISTS campaigns CASCADE;
DROP TABLE IF EXISTS brand_memory CASCADE;
DROP TABLE IF EXISTS apps CASCADE;

DROP FUNCTION IF EXISTS set_updated_at() CASCADE;
