-- Handouts: images a GM shows the table.
--
-- A letter, a portrait, a map of the region, the symbol carved into the door.
-- Kept in a library rather than pushed straight from disk, because the same
-- handout comes back out three sessions later and hunting for the file again
-- is the friction this program exists to remove.
--
-- Which one is *showing* is a single setting rather than a column here: it is
-- a property of the table right now, not of the image, and exactly one can be
-- up at a time. See ADR-018.

CREATE TABLE handouts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT    NOT NULL,
    filename   TEXT    NOT NULL UNIQUE,
    width_px   INTEGER NOT NULL,
    height_px  INTEGER NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO settings (key, value) VALUES ('handout_showing', '');
