import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from PIL import Image

import ctodeck

SAMPLE = Path(__file__).with_name("slides.yaml")
EXAMPLE = Path(__file__).parent / "examples" / "deck.yaml"


def run(*args):
    return CliRunner().invoke(ctodeck.cli, [str(a) for a in args])


def write_deck(tmp_path, deck):
    path = tmp_path / "slides.yaml"
    path.write_text(yaml.safe_dump(deck), encoding="utf-8")
    return path


def deck_of(*slides):
    return {"title": "Deck", "slides": list(slides)}


def test_load_sample_data():
    deck = ctodeck.load(SAMPLE)
    assert deck["title"] == "foo"
    assert len(deck["slides"]) == 1


def test_pages_start_with_a_cover_of_the_deck_title():
    pages = ctodeck.pages_of(ctodeck.load(EXAMPLE))
    assert pages[0] == {"title": "CTO Framework"}
    assert len(pages) == 3


@pytest.mark.parametrize(
    "deck, message",
    [
        ([], "expected a YAML mapping"),
        ({"slides": [{"title": "A"}]}, "'title' must be a non-empty string"),
        ({"title": "D"}, "'slides' must be a non-empty list"),
        ({"title": "D", "slides": []}, "'slides' must be a non-empty list"),
        (deck_of("nope"), "slide 1 is not a mapping"),
        (deck_of({"subtitle": "x"}), "slide 1: 'title'"),
        (deck_of({"title": "A", "subtitle": 3}), "slide 1: 'subtitle'"),
        (deck_of({"title": "A", "content": "x"}), "'content' must be a list"),
        (deck_of({"title": "A", "content": ["x"]}), "content item 1 is not a mapping"),
        (deck_of({"title": "A", "content": [{"text": ""}]}), "content item 1: 'text'"),
    ],
)
def test_load_rejects_bad_data(tmp_path, deck, message):
    with pytest.raises(ctodeck.DataError, match=message):
        ctodeck.load(write_deck(tmp_path, deck))


def test_ascii_wraps_long_text_inside_the_frame():
    long = "word " * 40
    deck = {"title": "D", "slides": [{"title": "T", "content": [{"text": long}]}]}
    lines = ctodeck.ascii_deck(deck)
    assert {len(line) for line in lines if line} == {ctodeck.ASCII_W}
    assert sum(line.startswith("│   ") for line in lines) > 1  # continuation lines are indented


def test_ascii_numbers_the_slides():
    lines = ctodeck.ascii_deck(ctodeck.load(EXAMPLE))
    footers = [line for line in lines if line.startswith("└")]
    assert "1/3" in footers[0] and "3/3" in footers[-1]


def test_wrap_respects_max_width():
    font = ctodeck.load_font(ctodeck.FONT_REGULAR, 30)
    lines = ctodeck.wrap("lorem ipsum dolor sit amet " * 10, font, 400)
    assert len(lines) > 1
    assert all(font.getlength(line) <= 400 for line in lines)


def test_cli_prints_ascii_by_default():
    result = run(EXAMPLE)
    assert result.exit_code == 0
    assert "WHY A FRAMEWORK" in result.output


def test_cli_writes_ascii_to_a_file(tmp_path):
    out = tmp_path / "deck.txt"
    assert run(EXAMPLE, "-o", out).exit_code == 0
    assert "PRINCIPLES" in out.read_text(encoding="utf-8")


def test_cli_writes_one_png_per_page(tmp_path):
    result = run(EXAMPLE, "--format", "png", "-o", tmp_path / "out")
    assert result.exit_code == 0
    files = sorted((tmp_path / "out").glob("slide-*.png"))
    assert [f.name for f in files] == ["slide-01.png", "slide-02.png", "slide-03.png"]
    with Image.open(files[0]) as image:
        assert image.size == (ctodeck.WIDTH, ctodeck.HEIGHT)


def test_cli_writes_one_svg_per_page(tmp_path):
    result = run(EXAMPLE, "--format", "svg", "-o", tmp_path)
    assert result.exit_code == 0
    root = ET.parse(tmp_path / "slide-02.svg").getroot()
    texts = [el.text for el in root.iter("{http://www.w3.org/2000/svg}text")]
    assert "Why a framework" in texts


def test_cli_reports_bad_data_without_a_traceback(tmp_path):
    path = write_deck(tmp_path, {"title": "D", "slides": [{"subtitle": "x"}]})
    result = run(path)
    assert result.exit_code != 0
    assert "slide 1" in result.output
    assert "Traceback" not in result.output


def test_cli_reports_missing_file(tmp_path):
    result = run(tmp_path / "missing.yaml")
    assert result.exit_code != 0
    assert "Error" in result.output


def test_step_moves_and_clamps():
    assert ctodeck.step(0, ord("l"), 3) == 1
    assert ctodeck.step(2, ord("l"), 3) == 2
    assert ctodeck.step(1, ord("h"), 3) == 0
    assert ctodeck.step(0, ord("h"), 3) == 0
    assert ctodeck.step(1, ord("G"), 3) == 2
    assert ctodeck.step(2, ord("g"), 3) == 0
    assert ctodeck.step(1, ord("x"), 3) == 1


def test_ascii_slides_all_have_the_same_height():
    deck = ctodeck.load(EXAMPLE)
    pages = ctodeck.pages_of(deck)
    size = ctodeck.slide_height(pages)
    heights = {len(ctodeck.ascii_slide(p, 1, len(pages), size)) for p in pages}
    assert heights == {size}
