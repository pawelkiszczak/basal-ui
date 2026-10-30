"""Accessibility invariants of basal_ui/static/index.html that need no browser: landmarks, the skip link, heading order,
labels and tab widgets. The rendered views are checked with axe-core in tests/e2e/test_a11y.py."""

import re
from html.parser import HTMLParser
from pathlib import Path

STATIC = Path(__file__).parents[1] / "basal_ui/static"
VIEWS = (
    "overview",
    "models",
    "manager",
    "backends",
    "playground",
    "batch",
    "inspect",
    "api",
    "history",
    "logs",
    "settings",
)
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class Node:
    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent, self.children, self.text = tag, dict(attrs), parent, [], ""

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def ancestors(self):
        node = self.parent
        while node:
            yield node
            node = node.parent

    def text_content(self):
        return self.text + "".join(c.text_content() for c in self.children)


class Tree(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.root = self.node = Node("#document", {}, None)
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.node)
        self.node.children.append(node)
        if tag not in VOID:
            self.node = node

    def handle_endtag(self, tag):
        node = self.node
        while node.parent and node.tag != tag:
            node = node.parent
        if node.parent:
            self.node = node.parent

    def handle_data(self, data):
        self.node.text += data


PAGE = Tree((STATIC / "index.html").read_text()).root
NODES = list(PAGE.walk())
BY_ID = {n.attrs["id"]: n for n in NODES if "id" in n.attrs}


def headings(nodes):
    return [n for n in nodes if re.fullmatch(r"h[1-6]", n.tag)]


def test_landmarks_and_the_skip_link():
    body = next(n for n in NODES if n.tag == "body")
    skip = body.children[0]
    assert skip.tag == "a" and "skip-link" in skip.attrs["class"] and skip.attrs["href"] == "#pageTitle"
    aside = next(n for n in NODES if n.tag == "aside")
    assert any(n.tag == "nav" for n in aside.walk())
    assert [n.attrs.get("id") for n in NODES if n.tag == "main"] == ["main"]
    assert not [n for n in NODES if n.tag == "header" and not any(a.tag == "main" for a in n.ancestors())]
    assert BY_ID["srStatus"].attrs["role"] == "status"
    assert "aria-live" not in BY_ID["toastStack"].attrs  # errors are role=alert, the rest go through #srStatus


def test_one_h1_the_view_title_and_no_heading_skips_or_empty_headings():
    h1 = [n for n in NODES if n.tag == "h1"]
    assert len(h1) == 1 and h1[0].attrs["id"] == "pageTitle" and h1[0].attrs["tabindex"] == "-1"
    assert all(h.text_content().strip() for h in headings(NODES))
    for view in VIEWS:  # each view starts below the page h1: h2 for cards, h3 inside
        section = BY_ID[f"view-{view}"]
        levels = [1] + [int(h.tag[1]) for h in headings(section.walk())]
        assert all(b - a <= 1 for a, b in zip(levels[:-1], levels[1:], strict=True)), (view, levels)
    for dialog in (n for n in NODES if n.tag == "dialog"):
        first = headings(dialog.walk())
        assert not first or first[0].tag == "h2", dialog.attrs.get("id")


def test_view_ids_are_not_scroll_anchors_for_the_view_hashes():
    for view in VIEWS:
        assert view not in BY_ID, view  # #models would scroll the page to an element with id="models"
        assert BY_ID[f"view-{view}"].attrs["data-page"] == view


def test_every_field_has_a_label_and_the_batch_fields_a_visible_one():
    labelled_for = {n.attrs.get("for") for n in NODES if n.tag == "label"}
    for field in (n for n in NODES if n.tag in ("input", "select", "textarea")):
        if field.attrs.get("type") == "hidden":
            continue
        named = (
            field.attrs.get("id") in labelled_for
            or any(a.tag == "label" for a in field.ancestors())
            or field.attrs.get("aria-label")
            or field.attrs.get("aria-labelledby")
        )
        assert named, field.attrs
    for visible in ("batchModel", "batchInput", "batchOutput", "batchConcurrency", "batchEarlyExit", "newKeyName"):
        assert visible in labelled_for, visible
    for label in (n for n in NODES if n.tag == "label" and "for" in n.attrs):
        assert label.attrs["for"] in BY_ID, label.attrs["for"]


def test_tabs_name_their_panels():
    tabs = [n for n in NODES if n.attrs.get("role") == "tab"]
    assert len(tabs) >= 8
    for tab in tabs:
        assert tab.attrs.get("aria-selected") in ("true", "false") and tab.attrs.get("type") == "button", tab.attrs
        panel = BY_ID[tab.attrs["aria-controls"]]
        assert panel.attrs["role"] == "tabpanel" and panel.attrs["aria-labelledby"] == tab.attrs["id"]
    for tablist in (n for n in NODES if n.attrs.get("role") == "tablist"):
        assert tablist.attrs.get("aria-label") or tablist.attrs.get("aria-labelledby")


def test_scripts_drive_the_semantics_the_page_relies_on():
    core = (STATIC / "js/core.js").read_text()
    assert "role','alert'" in core and 'role="progressbar"' in core and "function tablist(" in core
    assert "!typing&&e.altKey" in core  # Alt/Option+digit does not fire while typing (Option+digit types characters)
    shell = (STATIC / "js/shell.js").read_text()
    assert "document.title=" in shell and "$('#pageTitle').focus()" in shell
    for name in ("components.css", "views.css"):
        source = (STATIC / name).read_text()
        assert "#overview" not in source and "#logs " not in source, name


def test_menu_groups_settings_and_the_old_hashes():
    nav = next(n for n in NODES if n.tag == "nav")
    groups = {
        n.attrs["aria-labelledby"]: [b.attrs["data-view"] for b in n.walk() if "data-view" in b.attrs]
        for n in nav.walk()
        if n.attrs.get("role") == "group"
    }
    assert groups == {
        "nav-run": ["playground", "batch"],
        "nav-models": ["models", "manager", "backends"],
        "nav-serve": ["api"],
        "nav-evaluate": ["inspect", "history"],
        "nav-system": ["logs", "settings"],
    }
    for moved in ("modelsDir", "autoLoad", "memoryBudget", "memoryReserve", "checkUpdates", "exportPresets"):
        assert any(a.attrs.get("id") == "view-settings" for a in BY_ID[moved].ancestors()), moved
    assert any(a.attrs.get("id") == "view-settings" for a in BY_ID["themeLabel"].ancestors())
    assert not any(n.attrs.get("data-theme-choice") for n in BY_ID["footerUnload"].parent.walk())
    shell = (STATIC / "js/shell.js").read_text()
    # #models and #manager keep working; #benchmarks and #calibration open the Library at their block
    assert "VIEW_ALIAS={discover:'models',library:'manager',benchmarks:'manager',calibration:'manager'}" in shell


def test_switches_radios_and_buttons_show_a_visible_focus_outline():
    """input:focus drops the outline for text fields; switches and radios (and Basecoat's .btn, whose own ring is
    about 2.2:1) get the 2px accent outline back (WCAG 2.4.7, 1.4.11)."""
    css = re.sub(r"/\*.*?\*/", "", (STATIC / "components.css").read_text(), flags=re.DOTALL)
    for selector in (r"input\[type=checkbox\]:focus-visible", r"\.btn:focus-visible"):
        rule = re.search(selector + r"[^{]*\{([^}]*)\}", css)
        assert rule and "outline: 2px solid var(--accent)" in rule.group(1), selector
    assert css.index("input[type=checkbox]:focus-visible") > css.index("input:focus,"), (
        "must follow the outline: 0 rule"
    )


def test_the_command_palette_is_a_combobox_with_a_listbox():
    combo = BY_ID["paletteInput"]
    assert combo.attrs["role"] == "combobox" and combo.attrs["aria-expanded"] == "true"
    assert BY_ID[combo.attrs["aria-controls"]].attrs["role"] == "listbox"
    assert BY_ID["commandPalette"].tag == "dialog" and BY_ID["commandPalette"].attrs.get("aria-label")
    palette = (STATIC / "js/palette.js").read_text()
    assert "aria-activedescendant" in palette and 'role="option"' in palette and "aria-selected" in palette


def test_the_ui_fixture_server_serves_status_and_decisions():
    """scripts/ui_fixture_server.py (the fake-backed app tests/e2e/test_a11y.py and UI work run on) starts with --preload,
    loads basal-2.0 mini and answers a Decision: a Fleet, Runner or catalog refactor that breaks it fails here."""
    import contextlib
    import json
    import socket
    import subprocess
    import sys
    import time
    import urllib.request

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    script = Path(__file__).parents[1] / "scripts/ui_fixture_server.py"
    proc = subprocess.Popen([sys.executable, str(script), "--port", str(port), "--preload"], stdout=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"

    def call(path, body=None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(base + path, data, {"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())

    try:
        deadline, ready = time.monotonic() + 60, []
        while time.monotonic() < deadline and not ready:
            assert proc.poll() is None, "the fixture server exited"
            with contextlib.suppress(OSError):  # not listening yet
                ready = [m["id"] for m in call("/api/status")["models"] if m["state"] == "ready"]
            time.sleep(0.3)
        assert ready == ["Remek/basal-2.0-mini"]
        request = {"model": ready[0], "state": "s", "questions": {"q": {"type": "choice", "options": ["a", "b"]}}}
        answer = call("/api/decide", request)["answers"]["q"]
        assert answer["type"] == "choice" and len(answer["probabilities"]) == 2  # one per option
    finally:
        proc.terminate()
        proc.wait(10)
