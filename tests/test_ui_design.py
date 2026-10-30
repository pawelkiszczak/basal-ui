"""The design tokens of basal_ui/static/tokens.css: they pass WCAG AA in both themes, the two dark-palette blocks stay
identical, every token is used, and the stylesheets take their colours from the tokens. The CSS is parsed into rules
and declarations (comments dropped, blocks matched by brace counting), so formatting does not matter."""

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).parents[1] / "basal_ui/static"
DARK_MEDIA = "@media (prefers-color-scheme: dark)"


def rules(css):
    """Each style rule of a stylesheet as (enclosing at-rules, selector, [(property, value)]), in source order."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    out, stack, buf = [], [], []
    for ch in css:
        if ch == "{":
            stack.append(" ".join("".join(buf).split()))
            buf = []
        elif ch == "}":
            head = stack.pop()
            if not head.startswith("@"):
                out.append((tuple(stack), head, declarations("".join(buf))))
            buf = []
        elif ch == ";" and (not stack or stack[-1].startswith("@")):
            buf = []  # a statement such as @layer a, b;
        else:
            buf.append(ch)
    assert not stack, f"unclosed block: {stack[-1]}"
    return out


def declarations(body):
    pairs = (d.split(":", 1) for d in body.split(";") if d.strip())
    return [(name.strip(), " ".join(value.split())) for name, value in pairs]


def block(sheet, selector, context=()):
    found = [d for ctx, sel, d in sheet if sel == selector and ctx == context]
    assert len(found) == 1, f"{context} {selector}: {len(found)} blocks"
    names = [name for name, _ in found[0]]
    assert len(names) == len(set(names)), f"{selector}: a property set twice"
    return dict(found[0])


TOKENS = rules((STATIC / "tokens.css").read_text())
LIGHT = block(TOKENS, ":root")
DARK_CHOICE = block(TOKENS, ":root[data-theme=dark]")
DARK_PREFERENCE = block(TOKENS, ":root:not([data-theme=light])", (DARK_MEDIA,))
DARK = {**LIGHT, **DARK_CHOICE}
HEX = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})")


def colour(tokens, name):
    """The opaque #hex a colour token resolves to, following var(--x) references."""
    value = tokens.get(f"--{name}")
    assert value is not None, f"--{name} is not defined"
    ref = re.fullmatch(r"var\(--([\w-]+)\)", value)
    if ref:
        return colour(tokens, ref.group(1))
    assert HEX.fullmatch(value), f"--{name}: {value!r} is not an opaque #hex or var(--token), so it cannot be checked"
    return value


def luminance(hex_colour):
    h = hex_colour.lstrip("#")
    h = "".join(c * 2 for c in h) if len(h) == 3 else h
    channels = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    r, g, b = (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


SURFACES = ("paper", "white", "surface-2", "surface-3")
TEXT_ON = [(text, surface) for text in ("ink", "muted", "accent", "eyebrow-quiet", "omni") for surface in SURFACES]
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
    """The dark palette is written twice: CSS cannot put a media query and an attribute selector in one rule, and
    light-dark() cannot carry --shadow or reach browsers before 2024. This keeps the two copies equal."""
    assert DARK_PREFERENCE == DARK_CHOICE
    assert DARK_CHOICE["color-scheme"] == "dark" and LIGHT["color-scheme"] == "light"
    assert set(DARK_CHOICE) - {"color-scheme"} <= set(LIGHT), "a dark token without a light value"


def test_every_token_is_used():
    """A token nothing reads (a stylesheet, a script, the page, or Basecoat for its own variables) is dead weight."""
    readers = [p for p in STATIC.rglob("*") if p.suffix in (".css", ".js", ".html")]
    sources = [p.read_text() for p in readers if p.name not in ("tokens.css", "viz.html")]  # viz.html: its own tokens
    sources.append(re.sub(r"--[\w-]+:", "", (STATIC / "tokens.css").read_text()))  # references inside tokens.css
    used = set(re.findall(r"var\((--[\w-]+)", "\n".join(sources)))
    assert set(LIGHT) - {"color-scheme"} - used == set()


COLOUR_FUNCTION = re.compile(r"\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color|color-mix|light-dark)\(", re.IGNORECASE)
NAMED_COLOURS = (  # CSS named colours, as one string
    "aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue blueviolet brown burlywood "
    "cadetblue chartreuse chocolate coral cornflowerblue cornsilk crimson cyan darkblue darkcyan darkgoldenrod "
    "darkgray darkgreen darkgrey darkkhaki darkmagenta darkolivegreen darkorange darkorchid darkred darksalmon "
    "darkseagreen darkslateblue darkslategray darkslategrey darkturquoise darkviolet deeppink deepskyblue dimgray "
    "dimgrey dodgerblue firebrick floralwhite forestgreen fuchsia gainsboro ghostwhite gold goldenrod gray green "
    "greenyellow grey honeydew hotpink indianred indigo ivory khaki lavender lavenderblush lawngreen lemonchiffon "
    "lightblue lightcoral lightcyan lightgoldenrodyellow lightgray lightgreen lightgrey lightpink lightsalmon "
    "lightseagreen lightskyblue lightslategray lightslategrey lightsteelblue lightyellow lime limegreen linen magenta "
    "maroon mediumaquamarine mediumblue mediumorchid mediumpurple mediumseagreen mediumslateblue mediumspringgreen "
    "mediumturquoise mediumvioletred midnightblue mintcream mistyrose moccasin navajowhite navy oldlace olive "
    "olivedrab orange orangered orchid palegoldenrod palegreen paleturquoise palevioletred papayawhip peachpuff peru "
    "pink plum powderblue purple rebeccapurple red rosybrown royalblue saddlebrown salmon sandybrown seagreen "
    "seashell sienna silver skyblue slateblue slategray slategrey snow springgreen steelblue tan teal thistle tomato "
    "turquoise violet wheat white whitesmoke yellow yellowgreen"
)
NAMED = set(NAMED_COLOURS.split())
COLOUR_PROPERTY = re.compile(
    r"color|background(-color|-image)?|border(-\w+)*|outline(-color)?|fill|stroke|(box|text)-shadow|"
    r"text-decoration(-color)?|column-rule(-color)?"
)
# the switch knob and its shadow; the fixed overlay drawn on images (.image-source), which is not themed
ALLOWED_HEX = {"#fff", "#0002", "#326450", "#232824"}
# a colour function that only blends a token (the status pulse fades --success out)
ALLOWED_FUNCTION = {"color-mix(in srgb, var(--success) 70%, transparent)"}


@pytest.mark.parametrize("name", ["components.css", "views.css"])
def test_components_and_views_take_colours_from_tokens(name):
    for _, selector, decls in rules((STATIC / name).read_text()):
        for prop, value in decls:
            where = f"{name}: {selector} {{ {prop}: {value} }}"
            assert set(re.findall(r"#[0-9a-fA-F]{3,8}\b", value)) <= ALLOWED_HEX, where
            for match in COLOUR_FUNCTION.finditer(value):
                assert any(value[match.start() :].startswith(f) for f in ALLOWED_FUNCTION), where
            if COLOUR_PROPERTY.fullmatch(prop):
                words = re.findall(r"[a-z]+", re.sub(r"--[\w-]+", "", value.lower()))
                assert not set(words) & NAMED, where


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


BASE = ("@layer basal.base",)
SHEETS = {name: rules((STATIC / name).read_text()) for name in ("components.css", "views.css")}


@pytest.mark.parametrize("tokens", [LIGHT, DARK], ids=["light", "dark"])
def test_every_control_has_a_solid_focus_outline(tokens):
    """One focus indicator for buttons, fields, selects, switches and radios: a 2px --accent outline, >= 3:1 against
    every surface it can sit on (WCAG 1.4.11, 2.4.13). No rule hides it for a form control or swaps it for a faint
    ring; the outline only gives way where Basecoat's components do not draw one of their own."""
    focus = block(SHEETS["components.css"], ":focus-visible", BASE)
    assert focus["outline"] == "2px solid var(--accent)"
    for surface in (*SURFACES, "suggest-bg", "omni-light", "accent-light"):
        assert contrast(colour(tokens, "accent"), colour(tokens, surface)) >= 3, surface
    for name, sheet in SHEETS.items():
        for _, selector, decls in sheet:
            if ":focus" not in selector or selector == "#pageTitle:focus":  # #pageTitle: focused by script only
                continue
            for prop, value in decls:
                assert (prop, value) not in (("outline", "0"), ("outline", "none")), f"{name}: {selector} hides it"
                assert "--dirty-ring" not in value, f"{name}: {selector}"


def test_important_only_where_the_base_layer_needs_it():
    """!important inside a cascade layer beats every later layer, the reverse of normal declarations: it is kept for
    [hidden] and the reduced-motion overrides in basal.base, and views win over Basecoat by layer order alone."""
    for name, sheet in SHEETS.items():
        for context, selector, decls in sheet:
            for prop, value in decls:
                if "!important" in value:
                    assert context[:1] == BASE, f"{name}: {selector} {{ {prop}: {value} }}"
