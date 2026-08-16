-- Initiative: the columns the tracker needs that 001 did not anticipate.
--
-- The table itself has existed since the initial schema, unused. Three things
-- were missing.
--
-- modifier -- so "roll for everyone" is one click rather than a click and then
-- twenty pieces of mental arithmetic. It also breaks ties the way the rules do:
-- equal totals, higher modifier acts first.
--
-- is_hidden -- a GM rolls the ambushers into the order before the party knows
-- they are there. An entry may be concealed the same way a token is: omitted
-- from a player's payload entirely rather than flagged in it. See ADR-004.
--
-- initiative_round on scenes rather than a global -- combat belongs to the
-- encounter. Switching to another scene mid-fight and back must return to round
-- four with the right creature acting, which is the whole point of ADR-012.
-- Zero means no combat is running.

ALTER TABLE initiative ADD COLUMN modifier  INTEGER NOT NULL DEFAULT 0;
ALTER TABLE initiative ADD COLUMN is_hidden INTEGER NOT NULL DEFAULT 0;

ALTER TABLE scenes ADD COLUMN initiative_round INTEGER NOT NULL DEFAULT 0;
