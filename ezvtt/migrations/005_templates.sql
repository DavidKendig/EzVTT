-- Area-of-effect templates: the circle, cone, and line a GM drops on the board.
--
-- Named aoe_templates rather than templates because ezvtt/templates/ is the
-- Jinja directory, and a module called templates.py sitting beside it would be
-- a trap for the next person. The wire format and the UI both say "templates",
-- which is what a GM calls them.
--
-- Shared state rather than a local drawing: the whole point of dropping a
-- fireball is that the table sees whose square it covers. Measuring, which is a
-- question the person asking has, stays on their own screen -- see ADR-014.
--
-- Geometry is in grid units for the same reason token positions are: adjusting
-- a map's grid size must not scatter what is already on it.

CREATE TABLE aoe_templates (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    scene_id INTEGER NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
    kind     TEXT    NOT NULL CHECK (kind IN ('circle', 'cone', 'line')),
    -- Origin: a circle's centre, a cone's apex, a line's near end.
    x        REAL    NOT NULL DEFAULT 0.0,
    y        REAL    NOT NULL DEFAULT 0.0,
    -- Radius for a circle, length for a cone or line, in grid squares.
    size     REAL    NOT NULL DEFAULT 4.0,
    -- Line thickness. Unused by a circle; a cone's spread is derived from its
    -- length, which is how the 5e cone is defined.
    width    REAL    NOT NULL DEFAULT 1.0,
    -- Degrees clockwise from east. Ignored by a circle.
    angle    REAL    NOT NULL DEFAULT 0.0,
    color    TEXT    NOT NULL DEFAULT '#d9a441',
    label    TEXT,
    -- A trap's blast radius can be prepped before anyone triggers it. Hidden
    -- templates are omitted from a player's payload entirely, like tokens.
    is_hidden  INTEGER NOT NULL DEFAULT 0,
    created_at TEXT   NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_aoe_scene ON aoe_templates(scene_id);
