"""The look: the mark survives Streamlit's Markdown pass, chips are escaped."""

import re

from spider.theme import (TERMINAL_MARK, banner, chip, confidence_chip, css,
                          evidence, logo_svg, origin_chip)


def test_the_mark_is_one_line_so_markdown_cannot_split_it():
    """A blank line inside the SVG makes Streamlit insert a <p>, which knocks
    the leg <g> out of the render tree and draws a spider with no legs."""
    markup = logo_svg()
    assert "\n" not in markup
    assert "><" in markup and "\n\n" not in markup
    assert markup.startswith("<svg") and markup.rstrip().endswith("</svg>")


def test_the_mark_still_has_all_eight_legs_after_flattening():
    markup = logo_svg()
    legs = re.findall(r"<path d=\"M[\d.]+ [\d.]+ C", markup)
    assert len(legs) == 8
    feet = re.findall(r'<circle cx="[\d.]+" cy="[\d.]+" r="2.5"', markup)
    assert len(feet) == 8                        # a graph node at each foot
    assert markup.count("<circle") == 10         # plus two eyes


def test_the_stylesheet_is_balanced_and_defines_the_palette():
    text = css()
    assert text.count("{") == text.count("}")
    for token in ("--red", "--blue", "--ink", "--display", "--mono"):
        assert token in text
    # the surfaces a theme usually forgets
    for surface in ("::selection", "caret-color", ":focus-visible",
                    "::-webkit-scrollbar"):
        assert surface in text


def test_chips_escape_what_a_page_said():
    assert "&lt;script&gt;" in chip("<script>")
    assert "sp-chip-derived" in chip("x", "derived")


def test_a_confidence_is_coloured_by_what_it_means():
    assert "sp-chip-ok" in confidence_chip(0.92)
    assert "sp-chip-derived" in confidence_chip(0.62)
    assert "sp-chip-inferred" in confidence_chip(0.3)
    assert confidence_chip(None) == ""


def test_origin_chips_cover_the_three_origins():
    for origin in ("extracted", "derived", "inferred"):
        assert f"sp-chip-{origin}" in origin_chip(origin)


def test_evidence_escapes_the_quote_and_keeps_the_source():
    block = evidence('he said "3,000 m" & more', "http://a.test/p")
    assert "&quot;" in block or "&#34;" in block
    assert "&amp;" in block
    assert "http://a.test/p" in block


def test_the_terminal_mark_has_eight_legs_and_prints_plainly():
    lines = [line for line in TERMINAL_MARK.strip("\n").splitlines() if line.strip()]
    assert len(lines) == 5
    assert all(ord(c) < 128 for c in TERMINAL_MARK)      # no font surprises
    assert "P R O J E C T   S P I D E R" in banner()
    assert "a crawler" in banner("a crawler")


def test_the_terminal_mark_does_not_need_the_dashboard_installed(tmp_path):
    """`spider init` prints a spider; Streamlit is an optional extra."""
    import subprocess
    import sys
    import textwrap

    code = textwrap.dedent('''
        import sys
        class Fail:
            def find_spec(self, name, path=None, target=None):
                if name == "streamlit" or name.startswith("streamlit."):
                    raise ImportError("no streamlit here")
                return None
        sys.meta_path.insert(0, Fail())
        from spider.theme import banner, chip, css, logo_svg
        from spider.cli import main
        assert "P R O J E C T" in banner()
        assert logo_svg().startswith("<svg")
        assert "--red" in css()
        print("OK")
    ''')
    result = subprocess.run([sys.executable, "-c", code], capture_output=True,
                            text=True)
    assert result.returncode == 0, result.stderr[-400:]
    assert "OK" in result.stdout


def test_the_theme_never_repositions_streamlit_layout_containers():
    """Streamlit scrolls its main area inside an absolutely positioned view
    container. Giving that container (or every child of .stApp) a position or
    a stacking context makes it grow to content height, and the page stops
    scrolling - which is exactly what an overlay pseudo-element caused once.
    """
    import re

    text = css()
    containers = [r"\.stApp\s*>\s*\*", r"\.stApp::before", r"\.stApp::after",
                  r'\[data-testid="stAppViewContainer"\]',
                  r'\[data-testid="stMain"\]']
    for pattern in containers:
        for match in re.finditer(pattern + r"[^{]*\{([^}]*)\}", text):
            body = match.group(1)
            for banned in ("position:", "z-index:", "overflow:", "height:"):
                assert banned not in body, (
                    f"{pattern} sets {banned} - that is what broke scrolling")


def test_the_web_is_painted_as_a_background_not_an_overlay():
    text = css()
    surface = text[text.index(".stApp {"):text.index("/* ---", text.index(".stApp {"))]
    assert "background-image:" in surface and "svg+xml" in surface
    assert "background-attachment: fixed" in surface
    assert "position: fixed" not in surface
