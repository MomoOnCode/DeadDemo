from deaddemo.core.stats.chat_format import ChatPlayer, chat_html

COLORS = {2: "#e0a03c", 3: "#4a90e2"}
DIM = {2: "#7a5a24", 3: "#2b5382"}
NAMES = {2: "Amber", 3: "Sapphire"}


def test_chat_html_colors_by_team_and_escapes():
    players = {1: ChatPlayer("Alice <3", 2), 2: ChatPlayer("", 3)}
    rows = [
        {"match_seconds": 65.0, "hero_id": 1, "chat_type": "all", "text": "gg <b>ez</b>"},
        {"match_seconds": 130.0, "hero_id": 2, "chat_type": "team", "text": "push mid"},
        {"match_seconds": 5.0, "hero_id": 99, "chat_type": "all", "text": "?"},
    ]
    html = chat_html(rows, players, lambda h: f"Hero{h}", COLORS, DIM, NAMES)
    assert "Amber" in html and "Sapphire" in html  # legend
    assert "[1:05]" in html and "Alice &lt;3" in html and "gg &lt;b&gt;ez&lt;/b&gt;" in html
    assert "color:#e0a03c'>Alice" in html  # all-chat uses the bright team color
    assert "[TEAM]" in html and "color:#2b5382'>Hero2" in html  # team chat: dim color, hero-name fallback
    assert "color:#888888'>Hero99" in html  # unknown player: grey
