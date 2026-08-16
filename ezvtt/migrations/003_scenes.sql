-- Scenes: several per map, and a memory of which one was last on the table.
--
-- Until Phase 8 a map had exactly one scene, so "the scene for this map" was
-- unambiguous. Now a GM can prep "The tavern" and "The tavern, after the fight"
-- over the same artwork, and clicking that map in the library has to return to
-- whichever of them they were last running -- not to whichever was created
-- first, which is what ordering by id gave.
--
-- A counter rather than a timestamp. datetime('now') has second resolution and
-- even sub-second formats inherit the platform clock's granularity, which on
-- Windows is coarse enough that two switches a moment apart record the same
-- instant -- and then the tie breaks the wrong way. A sequence cannot tie, and
-- is not affected by the clock going backwards over DST or an NTP correction.

ALTER TABLE scenes ADD COLUMN last_active_seq INTEGER NOT NULL DEFAULT 0;

-- Existing scenes are the only scene of their map, so leaving them all at zero
-- is correct: there is nothing for the ordering to disambiguate.
