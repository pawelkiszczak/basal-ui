"""The colour tokens of basal_ui/static/tokens.css pass WCAG AA in both themes, and the stylesheets use them."""

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).parents[1] / "basal_ui/static"
CSS = (STATIC / "tokens.css").read_text()


def theme(selector):
    block = re.search(re.escape(selector) + r"\s*\{(.*?)\n\}", CSS, re.DOTALL).group(1)
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", block))


LIGHT = theme("\n:root")
DARK = {**LIGHT, **theme(":root[data-theme=dark]")}


def colour(tokens, name):
    value = tokens[f"--{name}"].strip()
    ref = re.fullmatch(r"var\(--([\w-]+)\)", value)
    return colour(tokens, ref.group(1)) if ref else value


def luminance(hex_colour):
    h = hex_colour.lstrip("#")
    h = "".join(c * 2 for c in h) if len(h) in (3, 4) else h
    channels = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    r, g, b = (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


TEXT_ON = [
    (text, surface)
    for text in ("ink", "muted", "accent", "eyebrow-quiet", "omni")
    for surface in ("paper", "white", "surface-2", "surface-3")
]
PAIRS = TEXT_ON + [
    ("muted", "suggest-bg"),
    ("eyebrow-quiet", "suggest-bg"),
    ("omni-quiet", "suggest-bg"),
    ("omni", "omni-light"),
    ("suggest-meta-fg", "suggest-meta-bg"),
    ("accent", "accent-light"),
    ("muted", "accent-light"),
    ("on-accent", "accent"),
    ("on-danger", "danger"),
    ("best-fg", "best-bg"),
    ("terminal-fg", "terminal-bg"),
    ("muted", "code-bg"),
    ("ink", "hover-bg"),
    *[
        (f"{tone}-fg", bg)
        for tone in ("success", "warning", "danger", "info")
        for bg in (f"{tone}-bg", "white", "paper")
    ],
]


@pytest.mark.parametrize("tokens", [LIGHT, DARK], ids=["light", "dark"])
@pytest.mark.parametrize(("fg", "bg"), PAIRS)
def test_text_tokens_pass_wcag_aa(tokens, fg, bg):
    assert contrast(colour(tokens, fg), colour(tokens, bg)) >= 4.5


def test_the_dark_preference_and_the_dark_choice_set_the_same_tokens():
    media = re.search(r":root:not\(\[data-theme=light\]\)\s*\{(.*?)\n  \}", CSS, re.DOTALL).group(1)
    assert dict(re.findall(r"(--[\w-]+):\s*([^;]+);", media)) == theme(":root[data-theme=dark]")


def test_components_and_views_take_colours_from_tokens():
    for name in ("components.css", "views.css"):
        source = re.sub(r"/\*.*?\*/", "", (STATIC / name).read_text(), flags=re.DOTALL)
        allowed = {"#fff", "#0002", "#326450", "#232824"}  # switch knob; the fixed overlay on images (.image-source)
        assert set(re.findall(r"#[0-9a-fA-F]{3,8}\b", source)) <= allowed, name


@pytest.mark.parametrize("tokens", [LIGHT, DARK], ids=["light", "dark"])
def test_chart_series_are_distinct_marks_on_cards(tokens):
    """The chart series (js/charts.js) are graphical objects: >= 3:1 on the card surface (WCAG 1.4.11)."""
    for n in range(1, 4):
        assert contrast(colour(tokens, f"chart-{n}"), colour(tokens, "white")) >= 3, n


def test_every_chart_has_an_accessible_name_and_a_data_table():
    """Each chart of js/charts.js is role=img or role=meter with an aria-label, and lists its numbers in a table."""
    source = (STATIC / "js/charts.js").read_text()
    bodies = re.findall(r"^function chart\w+\(\{label.*?^\}", source, re.DOTALL | re.MULTILINE)
    assert len(bodies) == 6  # meter, bars, histogram, sparkline, reliability, thresholds
    for body in bodies:
        assert re.search(r'role="(img|meter)"', body) and 'aria-label="${label}"' in body, body[:40]
        assert "chartTable(" in body, body[:40]


@pytest.mark.parametrize("tokens", [LIGHT, DARK], ids=["light", "dark"])
def test_field_and_switch_edges_are_visible(tokens):
    """Inputs, selects and the off switch draw --control-border: >= 3:1 on cards and the page (WCAG 1.4.11)."""
    for surface in ("white", "paper", "surface-2"):
        assert contrast(colour(tokens, "control-border"), colour(tokens, surface)) >= 3, surface
