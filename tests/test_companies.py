from pathlib import Path

from applypeace.companies import registry as reg
from applypeace.companies.ats import slug_candidates
from applypeace.companies.lists import Member, parse_nasdaq100_wikitext, read_csv
from applypeace.companies.names import match_level, normalize, same_company

WIKITEXT = """intro
{| class="wikitable sortable" id="constituents"
|-
! Ticker !! Company !! ISIN !! Industry !! Subsector
|-
| ABNB || [[Airbnb]] || {{ISIN|sl=n|US0090661010}} || Consumer || Services
|-
| GOOGL || [[Alphabet Inc.]] (Class A) || {{ISIN|sl=n|US02079K3059}} || Technology || Software
|-
| GOOG || [[Alphabet Inc.]] (Class C) || {{ISIN|sl=n|US02079K1079}} || Technology || Software
|-
| ASML || [[ASML Holding]] || {{ISIN|sl=n|USN070592100}} || Technology || Semis
|-
| ARM || [[Arm Holdings|Arm]] || {{ISIN|sl=n|US0420682058}} || Technology || Semis
|}
"""


def test_normalize_and_match():
    assert normalize("Palantir Technologies Inc.") == "palantir"
    assert normalize("Alphabet Inc. (Class A)") == "alphabet"
    assert same_company("Palantir Technologies", "Palantir")
    assert same_company("OpenAI", "OpenAI")
    assert not same_company("Gap Inc.", "Gapstars")
    assert not same_company(None, "Anything")


def test_match_level_rejects_shared_first_word():
    assert match_level("Palantir Technologies", "Palantir Technologies Inc.") == "exact"
    assert match_level("Rocket Lab Corporation", "Rocket Lab") == "exact"
    assert match_level("Co\u2013Star", "CoStar Group") == "exact"
    assert match_level("Robinhood", "Robinhood Markets") == "likely"
    assert match_level("Analog", "Analog Devices") == "none"
    assert match_level("Universal", "Universal Health Services") == "none"
    assert match_level("Fox Creek Veterinary Hospital - Wildwood", "Fox Corporation (Class A)") == "none"
    assert match_level("Parker Group Inc", "Parker Hannifin") == "none"
    assert match_level("charles", "Charles Schwab Corporation") == "none"


def test_slug_candidates_skip_generic_first_word():
    slugs = slug_candidates("American Express", ticker="AXP")
    assert "americanexpress" in slugs and "american-express" in slugs
    assert "american" not in slugs
    assert "axp" in slugs


def test_parse_nasdaq100_merges_share_classes():
    members = parse_nasdaq100_wikitext(WIKITEXT)
    names = [m.name for m in members]
    assert names.count("Alphabet Inc.") == 1
    assert "Arm" in names
    by_name = {m.name: m for m in members}
    assert by_name["Airbnb"].hq_country == "US"


def test_read_csv_with_country_and_rank(tmp_path: Path):
    f = tmp_path / "g2000.csv"
    f.write_text("Rank,Company,Country\n1,JPMorgan Chase,United States\n2,Saudi Aramco,Saudi Arabia\n")
    members = read_csv(f)
    assert [(m.rank, m.name, m.hq_country) for m in members] == [
        (1, "JPMorgan Chase", "US"),
        (2, "Saudi Aramco", "Saudi Arabia"),
    ]


def test_registry_merges_overlapping_lists():
    registry: dict = {}
    reg.set_list(registry, "sp500", [Member("Apple Inc.", ticker="AAPL"), Member("3M", ticker="MMM")])
    reg.set_list(registry, "nasdaq100", [Member("Apple", ticker="AAPL")])
    apple = next(c for c in registry.values() if c.get("ticker") == "AAPL")
    assert sorted(apple["lists"]) == ["nasdaq100", "sp500"]
    assert len(registry) == 2

    joined, left = reg.set_list(registry, "sp500", [Member("Apple Inc.", ticker="AAPL")])
    assert (joined, left) == (0, 1)
    assert all(c.get("ticker") != "MMM" for c in registry.values())  # on no list any more


def test_enabled_respects_toggles():
    registry: dict = {}
    reg.set_list(registry, "sp500", [Member("Apple", ticker="AAPL")])
    reg.add_custom(registry, "Anthropic", "greenhouse", "anthropic")
    settings = {"lists": {"sp500": False, "custom": True}}
    assert [c["name"] for c in reg.enabled(registry, settings)] == ["Anthropic"]
