"""Obsidian vault reading, and above all what it refuses to show.

A campaign vault holds the players' handouts and the GM's plot outline in the
same folder tree. The allow-list is the whole feature: everything else here is
in service of it not being bypassable.
"""

import pytest

from ezvtt import config, db, vault


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    """A vault shaped like a real one: shared handouts, secret GM notes."""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "vault.db")
    db.close()
    db.migrate(tmp_path / "vault.db")

    root = tmp_path / "Campaign"
    (root / "Handouts").mkdir(parents=True)
    (root / "Lore").mkdir()
    (root / "GM Only").mkdir()
    (root / ".obsidian").mkdir()

    (root / "Handouts" / "Tavern Menu.md").write_text(
        "# The Prancing Pony\n\nAle, 2cp. See [[Barkeep]].\n", encoding="utf-8"
    )
    (root / "Handouts" / "Barkeep.md").write_text(
        "Friendly. Knows everyone.\n", encoding="utf-8"
    )
    (root / "Lore" / "The Old War.md").write_text(
        "---\ntags: history\nauthor: Gary\n---\n\nIt began in autumn.\n",
        encoding="utf-8",
    )
    (root / "GM Only" / "Session 12.md").write_text(
        "# The traitor is Marcus\n\nHe poisons the wine in act three.\n",
        encoding="utf-8",
    )
    (root / "GM Only" / "Villain Stats.md").write_text(
        "Marcus: AC 15, HP 60.\n", encoding="utf-8"
    )
    (root / ".obsidian" / "workspace.json").write_text("{}", encoding="utf-8")
    (root / "Secret Root Note.md").write_text(
        "Marcus betrays them.\n", encoding="utf-8"
    )

    vault.set_vault_root(str(root))
    yield root
    db.close()


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

def test_a_new_vault_shares_nothing(campaign):
    """Deny by default. Pointing at a vault must not publish it."""
    assert vault.allowed_dirs() == []
    assert vault.tree(is_gm=False) == []
    assert vault.search("Marcus", is_gm=False) == []


def test_the_gm_sees_everything_immediately(campaign):
    names = {entry["name"] for entry in vault.tree(is_gm=True)}
    assert {"Handouts", "Lore", "GM Only"} <= names


def test_obsidian_internals_are_hidden_even_from_the_gm(campaign):
    names = {entry["name"] for entry in vault.tree(is_gm=True)}
    assert ".obsidian" not in names


def test_setting_a_new_vault_clears_the_allow_list(campaign, tmp_path):
    """Folder names mean different things in a different vault."""
    vault.set_allowed_dirs(["Handouts"])
    assert vault.allowed_dirs() == ["Handouts"]

    other = tmp_path / "Other Campaign"
    (other / "Handouts").mkdir(parents=True)
    vault.set_vault_root(str(other))
    assert vault.allowed_dirs() == [], "the old allow-list leaked into a new vault"


def test_clearing_the_path_disables_the_wiki(campaign):
    vault.set_allowed_dirs(["Handouts"])
    vault.set_vault_root("")
    assert vault.is_configured() is False
    assert vault.tree(is_gm=True) == []


def test_allow_list_rejects_paths_outside_the_vault(campaign):
    for bad in ["../", "../..", "/etc", "..\\..\\Windows"]:
        with pytest.raises(vault.VaultError):
            vault.set_allowed_dirs([bad])


def test_allow_list_rejects_a_file(campaign):
    with pytest.raises(vault.VaultError, match="not a folder"):
        vault.set_allowed_dirs(["Handouts/Tavern Menu.md"])


def test_blank_entries_do_not_become_share_everything(campaign):
    """An empty entry would resolve to the vault root."""
    assert vault.set_allowed_dirs(["", "   ", "Handouts"]) == ["Handouts"]
    assert vault.may_read("GM Only/Session 12.md", is_gm=False) is False


# --------------------------------------------------------------------------- #
# What a player may reach
# --------------------------------------------------------------------------- #

@pytest.fixture
def shared(campaign):
    vault.set_allowed_dirs(["Handouts", "Lore"])
    return campaign


def test_player_reads_an_allow_listed_note(shared):
    note = vault.read("Handouts/Tavern Menu.md", is_gm=False)
    assert note["name"] == "Tavern Menu"
    assert "Prancing Pony" in note["html"]


def test_player_cannot_read_an_unlisted_folder(shared):
    """Asked for directly, by exact path -- the obvious attempt."""
    with pytest.raises(vault.VaultError):
        vault.read("GM Only/Session 12.md", is_gm=False)


def test_player_cannot_read_a_note_at_the_vault_root(shared):
    with pytest.raises(vault.VaultError):
        vault.read("Secret Root Note.md", is_gm=False)


@pytest.mark.parametrize("attempt", [
    "../secrets.md",
    "../../etc/passwd",
    "Handouts/../GM Only/Session 12.md",
    "Handouts/../../outside.md",
    "/etc/passwd",
    "..\\..\\Windows\\win.ini",
    "Handouts/./../GM Only/Villain Stats.md",
])
def test_traversal_attempts_are_refused(shared, attempt):
    with pytest.raises(vault.VaultError):
        vault.read(attempt, is_gm=False)


def test_the_refusal_does_not_confirm_the_file_exists(shared):
    """Saying "forbidden" for a real note and "not found" for a fake one tells
    a player exactly which secrets are there to look for."""
    with pytest.raises(vault.VaultError) as real:
        vault.read("GM Only/Session 12.md", is_gm=False)
    with pytest.raises(vault.VaultError) as fake:
        vault.read("GM Only/Does Not Exist.md", is_gm=False)
    with pytest.raises(vault.VaultError) as folder:
        vault.read("No Such Folder/Note.md", is_gm=False)

    assert str(real.value) == str(fake.value) == str(folder.value)


def test_gm_can_read_what_players_cannot(shared):
    note = vault.read("GM Only/Session 12.md", is_gm=True)
    assert "traitor" in note["html"]


def test_player_tree_contains_only_allow_listed_folders(shared):
    names = {entry["name"] for entry in vault.tree(is_gm=False)}
    assert names == {"Handouts", "Lore"}
    assert "GM Only" not in repr(vault.tree(is_gm=False))


def test_player_search_never_reaches_outside_the_allow_list(shared):
    """The GM's plot outline says "Marcus betrays them"."""
    gm_hits = vault.search("Marcus", is_gm=True)
    player_hits = vault.search("Marcus", is_gm=False)

    assert gm_hits, "the GM should find their own notes"
    assert player_hits == [], f"leaked: {player_hits}"


def test_player_search_finds_allow_listed_notes(shared):
    hits = vault.search("Prancing", is_gm=False)
    assert [h["name"] for h in hits] == ["Tavern Menu"]


def test_may_read_does_not_match_a_folder_by_prefix(shared):
    """"Lore" must not grant "Lore Secrets"."""
    assert vault.may_read("Lore/The Old War.md", is_gm=False) is True
    assert vault.may_read("Lore Secrets/Plot.md", is_gm=False) is False
    assert vault.may_read("LoreSecrets.md", is_gm=False) is False


# --------------------------------------------------------------------------- #
# Symlinks
# --------------------------------------------------------------------------- #

def test_a_symlink_out_of_the_vault_is_refused(shared, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("private diary\n", encoding="utf-8")

    link = shared / "Handouts" / "Escape.md"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this machine")

    # Inside an allow-listed folder, so only containment stops this.
    with pytest.raises(vault.VaultError):
        vault.read("Handouts/Escape.md", is_gm=False)


def test_a_symlinked_directory_is_not_walked(shared, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "Diary.md").write_text("private\n", encoding="utf-8")

    try:
        (shared / "Handouts" / "Linked").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this machine")

    assert "Diary" not in repr(vault.tree(is_gm=False))


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def test_frontmatter_is_parsed_out_of_the_body(shared):
    note = vault.read("Lore/The Old War.md", is_gm=False)
    assert note["frontmatter"] == {"tags": "history", "author": "Gary"}
    assert "---" not in note["html"]
    assert "It began in autumn" in note["html"]


def test_wikilinks_become_links_when_the_target_is_readable(shared):
    note = vault.read("Handouts/Tavern Menu.md", is_gm=False)
    assert "data-vault-link" in note["html"]
    assert "Barkeep" in note["html"]


def test_a_wikilink_to_a_forbidden_note_renders_as_plain_text(shared):
    """A dead link would let a player map the GM's folder names."""
    (shared / "Handouts" / "Rumours.md").write_text(
        "The wine is [[Session 12|suspicious]].\n", encoding="utf-8"
    )
    note = vault.read("Handouts/Rumours.md", is_gm=False)
    assert "suspicious" in note["html"]
    assert "data-vault-link" not in note["html"]
    assert "Session 12" not in note["html"]


# --------------------------------------------------------------------------- #
# Sanitising
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("markup, banned", [
    ("<script>alert(1)</script>", "alert"),
    ("<img src=x onerror=alert(1)>", "onerror"),
    ('<a href="javascript:alert(1)">x</a>', "javascript:"),
    ('<iframe src="http://evil.test"></iframe>', "iframe"),
    ("<style>body{display:none}</style>", "display:none"),
    ('<form action="http://evil.test"><input name="p"></form>', "<form"),
    ('<a href="data:text/html;base64,PHNjcmlwdD4=">x</a>', "data:"),
    ('<div onclick="steal()">x</div>', "onclick"),
])
def test_dangerous_markup_is_stripped(markup, banned):
    assert banned not in vault.sanitise(markup)


@pytest.mark.parametrize("url", [
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    "  javascript:alert(1)",
    "java\tscript:alert(1)",
    "java\nscript:alert(1)",
    "data:text/html;base64,PHNjcmlwdD4=",
    "vbscript:msgbox(1)",
    "file:///C:/Windows/win.ini",
])
def test_only_known_schemes_are_permitted(url):
    """The first version of this check tested "starts with a safe character",
    whose trailing character class matched the j of javascript: and the d of
    data: -- so it permitted every scheme it existed to block."""
    assert vault.safe_url(url) is False


@pytest.mark.parametrize("url", [
    "https://example.test/page",
    "http://192.168.1.13:8080/play",
    "mailto:gm@example.test",
    "/media/maps/tavern.png",
    "#a-heading",
    "relative/note.md",
    "Notes/Chapter 2: Endings.md",   # a colon that is not a scheme
])
def test_ordinary_links_are_kept(url):
    assert vault.safe_url(url) is True


def test_ordinary_formatting_survives_sanitising():
    markup = ('<h2>Title</h2><p><strong>bold</strong> and <em>italic</em></p>'
              '<ul><li>one</li></ul><table><tr><td>cell</td></tr></table>'
              '<a href="https://example.test">link</a>')
    cleaned = vault.sanitise(markup)
    for fragment in ("<h2>", "<strong>", "<em>", "<li>", "<td>",
                     'href="https://example.test"'):
        assert fragment in cleaned


def test_a_note_containing_a_script_cannot_run_in_a_players_browser(shared):
    (shared / "Handouts" / "Pasted.md").write_text(
        "Copied from a website.\n\n<script>fetch('http://evil.test')</script>\n",
        encoding="utf-8",
    )
    note = vault.read("Handouts/Pasted.md", is_gm=False)
    assert "<script" not in note["html"]
    assert "evil.test" not in note["html"]


# --------------------------------------------------------------------------- #
# Bounds
# --------------------------------------------------------------------------- #

def test_an_enormous_note_is_refused_rather_than_streamed(shared):
    big = shared / "Handouts" / "Huge.md"
    big.write_text("x" * (vault.MAX_FILE_BYTES + 10), encoding="utf-8")
    with pytest.raises(vault.VaultError, match="too large"):
        vault.read("Handouts/Huge.md", is_gm=False)


def test_non_text_files_are_not_rendered_as_notes(shared):
    (shared / "Handouts" / "map.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    with pytest.raises(vault.VaultError):
        vault.read("Handouts/map.png", is_gm=False)


def test_a_one_letter_search_returns_nothing(shared):
    """Otherwise every keystroke walks the whole vault."""
    assert vault.search("a", is_gm=True) == []


def test_reading_without_a_vault_configured_fails_cleanly(campaign):
    vault.set_vault_root("")
    with pytest.raises(vault.VaultError, match="No vault"):
        vault.read("anything.md", is_gm=True)
