"""A Fandango at Home id on a digital copy (spec 2026-11-27-fandango-play-design §3): the one rule for an id and the
forms of a Fandango at Home address it is read from.

Scenario: The Addams Family (1991) is owned only on Fandango at Home; its page is
https://athome.fandango.com/content/browse/details/The-Addams-Family/15429 (the old address of the same page is on
www.vudu.com). Matinee keeps only the number at the end.
"""
import pytest

import app as matinee
import fandango
from fandango import fandango_details_url, fandango_id_ok, parse_fandango_link

ID = "15429"
PAGE = f"https://athome.fandango.com/content/browse/details/The-Addams-Family/{ID}"
OLD_PAGE = f"https://www.vudu.com/content/movies/details/The-Addams-Family/{ID}"


@pytest.mark.parametrize("value, ok", [
    ("15429", True), ("1", True), ("123456789012", True), ("0007", True),
    ("1234567890123", False), ("", False), ("15429a", False), ("15 429", False), ("-15429", False),
    ("15429\n", False), ("١٢", False),            # Arabic-Indic digits are not 0-9
    (None, False), (15429, False), (b"15429", False),
])
def test_fandango_id_ok(value, ok):
    assert fandango_id_ok(value) is ok


def test_the_rule_is_one_pattern():
    assert fandango.FANDANGO_ID_RE.pattern == "^[0-9]{1,12}$"


@pytest.mark.parametrize("pasted", [
    PAGE,
    OLD_PAGE,
    ID,
    f"athome.fandango.com/content/browse/details/The-Addams-Family/{ID}",        # no scheme
    f"www.vudu.com/content/movies/details/The-Addams-Family/{ID}",
    f"vudu.com/content/movies/details/The-Addams-Family/{ID}",                    # no www.
    f"https://www.athome.fandango.com/content/browse/details/The-Addams-Family/{ID}",
    f"http://athome.fandango.com/content/browse/details/The-Addams-Family/{ID}",
    f"{PAGE}/",                                                                   # a trailing slash
    f"{PAGE}?cmp=share&utm_source=copy",                                          # a query string
    f"{PAGE}#reviews",
    f"  {PAGE}\n",
    f"\t{ID}\r\n",
    f"https://ATHOME.FANDANGO.COM/content/browse/details/The-Addams-Family/{ID}",
    f"https://athome.fandango.com/content/browse/details/title/{ID}",              # what Matinee shows back
])
def test_parse_accepts(pasted):
    assert parse_fandango_link(pasted) == ID


@pytest.mark.parametrize("pasted", [None, "", "   ", "\n"])
def test_parse_blank_is_none(pasted):
    assert parse_fandango_link(pasted) is None


@pytest.mark.parametrize("pasted", [
    "1234567890123",                                                              # 13 digits
    "https://athome.fandango.com/content/browse/details/The-Addams-Family/1234567890123",
    f"https://www.fandango.com/the-addams-family-1991/{ID}",                      # another host
    f"https://evil.example.com/content/browse/details/The-Addams-Family/{ID}",
    f"https://athome.fandango.com.evil.example.com/content/browse/details/The-Addams-Family/{ID}",
    "https://athome.fandango.com/content/browse/details/The-Addams-Family",       # no number at the end
    "https://athome.fandango.com/content/browse/details/The-Addams-Family/15429x",
    f"https://athome.fandango.com/content/browse/{ID}",                           # not a film's page
    "https://athome.fandango.com/",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",                                # a YouTube address
    "https://youtu.be/dQw4w9WgXcQ",
    "dQw4w9WgXcQ",
    f"ftp://athome.fandango.com/content/browse/details/The-Addams-Family/{ID}",
    "not a link at all", "15 429",
    15429, ["15429"], True,
])
def test_parse_refuses(pasted):
    with pytest.raises(ValueError):
        parse_fandango_link(pasted)


def test_details_url():
    assert fandango_details_url(ID) == f"https://athome.fandango.com/content/browse/details/title/{ID}"
    assert parse_fandango_link(fandango_details_url(ID)) == ID                    # it reads back
    for bad in (None, "", "1234567890123", "x"):
        assert fandango_details_url(bad) is None


def test_module_is_split_out_and_shipped():
    from pathlib import Path
    assert "fandango" in matinee._SPLIT_MODULES
    assert matinee.fandango_id_ok(ID) and matinee.parse_fandango_link(PAGE) == ID   # app re-exports the rule
    build = (Path(__file__).resolve().parent.parent / "installer" / "build.sh").read_text(encoding="utf-8")
    cp = next(line for line in build.splitlines() if line.startswith("cp -r ")).split()
    assert "fandango.py" in cp
