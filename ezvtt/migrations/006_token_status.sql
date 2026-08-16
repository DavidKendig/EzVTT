-- Hit points and conditions on a token.
--
-- hp/hp_max are NULL until a GM types a number, because most things on a board
-- are furniture and a bar over every barrel is noise.
--
-- hp_public decides whether players are shown a *bar*. They are never sent the
-- numbers behind it unless the token is theirs -- a player who can read
-- "Goblin 7/11" out of a payload knows exactly how many hits are left, which is
-- the GM's information to give or withhold. See ADR-017.
--
-- Conditions are the opposite: a prone goblin is prone in front of everyone, so
-- they are public to the whole table. Stored as a comma-separated list of ids
-- from a fixed vocabulary, validated on the way in.

ALTER TABLE tokens ADD COLUMN hp         INTEGER;
ALTER TABLE tokens ADD COLUMN hp_max     INTEGER;
ALTER TABLE tokens ADD COLUMN hp_public  INTEGER NOT NULL DEFAULT 1;
ALTER TABLE tokens ADD COLUMN conditions TEXT    NOT NULL DEFAULT '';
