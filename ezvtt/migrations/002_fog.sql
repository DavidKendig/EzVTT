-- Fog of war: a version counter for cache busting.
--
-- The composited player map is cached on disk and referenced by URL. Browsers
-- cache aggressively, so the URL has to change when the fog does; a monotonic
-- counter is a cleaner cache key than a timestamp with second resolution, which
-- would collide during a fast brush stroke.

ALTER TABLE fog ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
