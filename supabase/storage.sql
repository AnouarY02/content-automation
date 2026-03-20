INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES
  ('campaign-videos', 'campaign-videos', true, 524288000, ARRAY['video/mp4']),
  ('voice-previews', 'voice-previews', true, 52428800, ARRAY['audio/mpeg', 'audio/mp3']),
  ('campaign-assets', 'campaign-assets', false, 524288000, ARRAY['video/mp4', 'audio/mpeg', 'image/jpeg', 'image/png'])
ON CONFLICT (id) DO UPDATE
SET
  public = EXCLUDED.public,
  file_size_limit = EXCLUDED.file_size_limit,
  allowed_mime_types = EXCLUDED.allowed_mime_types;

DROP POLICY IF EXISTS "Public campaign videos" ON storage.objects;
CREATE POLICY "Public campaign videos"
ON storage.objects
FOR SELECT
TO public
USING (bucket_id = 'campaign-videos');

DROP POLICY IF EXISTS "Public voice previews" ON storage.objects;
CREATE POLICY "Public voice previews"
ON storage.objects
FOR SELECT
TO public
USING (bucket_id = 'voice-previews');
