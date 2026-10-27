#!/usr/bin/env python3
"""ctodeck: present a slide deck from a YAML file in the terminal, or export it as ASCII, PNGs, or SVGs."""

import curses
import sys
import textwrap
from pathlib import Path
from typing import Any, TypedDict

import click
import svgwrite
import yaml
from PIL import Image, ImageDraw, ImageFont


class _RequiredSlide(TypedDict):
    title: str


class Slide(_RequiredSlide, total=False):
    subtitle: str
    content: list[dict[str, str]]


class Deck(TypedDict):
    title: str
    slides: list[Slide]


DEFAULT_DATA = "slides.yaml"
DEFAULT_OUTPUT = "deck"
FORMATS = ("interactive", "ascii", "png", "svg")

WIDTH = 1280
HEIGHT = 720
MARGIN = 80
BG = "#ffffff"
INK = "#111111"
MUTED = "#555555"
ACCENT = "#2b6cb0"
TITLE_SIZE = 48
SUBTITLE_SIZE = 28
TEXT_SIZE = 30
COVER_SIZE = 72
LINE_GAP = 1.3
BULLET = "•"
FONT_REGULAR = "DejaVuSans.ttf"
FONT_BOLD = "DejaVuSans-Bold.ttf"

ASCII_W = 72


class DataError(Exception):
    pass


def require_text(value: Any, where: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataError(f"{where}: '{field}' must be a non-empty string")
    return value


def load(path: Path | str) -> Deck:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise DataError("expected a YAML mapping with 'title' and 'slides'")
    require_text(data.get("title"), "deck", "title")
    slides = data.get("slides")
    if not isinstance(slides, list) or not slides:
        raise DataError("deck: 'slides' must be a non-empty list")

    for index, slide in enumerate(slides, start=1):
        where = f"slide {index}"
        if not isinstance(slide, dict):
            raise DataError(f"{where} is not a mapping")
        require_text(slide.get("title"), where, "title")
        if slide.get("subtitle") is not None:
            require_text(slide["subtitle"], where, "subtitle")
        content = slide.get("content", [])
        if not isinstance(content, list):
            raise DataError(f"{where}: 'content' must be a list")
        for item_index, item in enumerate(content, start=1):
            if not isinstance(item, dict):
                raise DataError(f"{where}: content item {item_index} is not a mapping")
            require_text(item.get("text"), f"{where}, content item {item_index}", "text")
    return data  # type: ignore[return-value]


def pages_of(deck: Deck) -> list[Slide]:
    """The deck as rendered: a cover with the deck title, then each slide."""
    return [{"title": deck["title"]}, *deck["slides"]]


def ascii_rows(slide: Slide, number: int, total: int, height: int = 0) -> list[tuple[str, str]]:
    """Draw one slide as (kind, line) rows; `height` pads it with blank rows to a minimum number of lines.

    Kinds: "frame" (border), "title", "subtitle", "content".
    """
    inner = ASCII_W - 4

    def row(kind: str, text: str = "") -> tuple[str, str]:
        return kind, f"│ {text:<{inner}} │"

    rows = [("frame", "┌" + "─" * (ASCII_W - 2) + "┐")]
    rows += [row("title", line) for line in textwrap.wrap(slide["title"].upper(), inner)]
    subtitle = textwrap.wrap(slide.get("subtitle", ""), inner)
    rows += [row("subtitle", line) for line in subtitle]
    content = slide.get("content", [])
    if subtitle or content:
        rows.append(row("frame", "─" * inner))
    for item in content:
        wrapped = textwrap.wrap(item["text"], inner - 2)
        rows.append(row("content", f"{BULLET} {wrapped[0]}" if wrapped else BULLET))
        rows += [row("content", f"  {line}") for line in wrapped[1:]]
    rows += [row("content")] * (height - len(rows) - 1)
    page = f" {number}/{total} "
    rows.append(("frame", "└" + page.rjust(ASCII_W - 3, "─") + "─┘"))
    return rows


def ascii_slide(slide: Slide, number: int, total: int, height: int = 0) -> list[str]:
    return [line for _, line in ascii_rows(slide, number, total, height)]


def slide_height(pages: list[Slide]) -> int:
    """Lines of the tallest slide, so that every slide can be drawn the same size."""
    return max(len(ascii_slide(slide, 1, len(pages))) for slide in pages)


def ascii_deck(deck: Deck) -> list[str]:
    pages = pages_of(deck)
    height = slide_height(pages)
    lines: list[str] = []
    for number, slide in enumerate(pages, start=1):
        if lines:
            lines.append("")
        lines += ascii_slide(slide, number, len(pages), height)
    return lines


NEXT_KEYS = {curses.KEY_RIGHT, curses.KEY_DOWN, curses.KEY_NPAGE, ord("l"), ord("j"), ord("n"), ord(" "), 10, 13}
PREV_KEYS = {curses.KEY_LEFT, curses.KEY_UP, curses.KEY_PPAGE, ord("h"), ord("k"), ord("p"), curses.KEY_BACKSPACE, 127, 8}
FIRST_KEYS = {curses.KEY_HOME, ord("g")}
LAST_KEYS = {curses.KEY_END, ord("G")}
QUIT_KEYS = {ord("q"), ord("Q"), 27}
HELP = "←/→ prev/next · g/G first/last · q quit"


def step(index: int, key: int, total: int) -> int:
    """The page shown after pressing `key` on page `index` (unchanged for other keys)."""
    if key in NEXT_KEYS:
        return min(index + 1, total - 1)
    if key in PREV_KEYS:
        return max(index - 1, 0)
    if key in FIRST_KEYS:
        return 0
    if key in LAST_KEYS:
        return total - 1
    return index


def put(screen: "curses.window", y: int, x: int, text: str, attr: int = 0) -> None:
    height, width = screen.getmaxyx()
    if 0 <= y < height and 0 <= x < width:
        try:
            screen.addstr(y, x, text[: width - x], attr)
        except curses.error:  # writing the bottom-right cell raises after drawing
            pass


def present(screen: "curses.window", deck: Deck) -> None:
    pages = pages_of(deck)
    curses.curs_set(0)
    slide_attr, background_attr = curses.A_REVERSE, curses.A_NORMAL
    if curses.has_colors():
        curses.start_color()
        curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_WHITE)  # slide
        curses.init_pair(2, curses.COLOR_WHITE, curses.COLOR_BLUE)  # background
        slide_attr, background_attr = curses.color_pair(1), curses.color_pair(2)
    screen.bkgd(" ", background_attr)
    size = slide_height(pages)
    index = 0
    while True:
        screen.erase()
        height, width = screen.getmaxyx()
        rows = ascii_rows(pages[index], index + 1, len(pages), size)
        top = max((height - len(rows)) // 2, 0)
        left = max((width - ASCII_W) // 2, 0)
        emphasis = {"title": curses.A_BOLD, "subtitle": curses.A_DIM}
        for offset, (kind, line) in enumerate(rows):
            put(screen, top + offset, left, line, slide_attr | emphasis.get(kind, 0))
        put(screen, height - 1, max((width - len(HELP)) // 2, 0), HELP, background_attr)
        screen.refresh()
        key = screen.getch()
        if key in QUIT_KEYS:
            return
        index = step(index, key, len(pages))


Font = ImageFont.FreeTypeFont | ImageFont.ImageFont


def load_font(name: str, size: int) -> Font:
    try:
        return ImageFont.truetype(name, size)
    except OSError:
        return ImageFont.load_default(size)


def wrap(text: str, font: Font, max_width: float) -> list[str]:
    """Greedy word wrap by rendered width."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and font.getlength(candidate) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    return lines + [current] if current else lines


class Text(TypedDict):
    x: float
    y: float  # baseline-independent top of the line
    size: int
    bold: bool
    color: str
    text: str


class Layout(TypedDict):
    texts: list[Text]
    rule: tuple[float, float, float] | None  # (y, x1, x2)


def layout(slide: Slide, cover: bool) -> Layout:
    """Place every line of text of a slide on the canvas."""
    texts: list[Text] = []
    max_width = WIDTH - 2 * MARGIN

    def add(text: str, x: float, y: float, size: int, bold: bool, color: str, width: float) -> float:
        font = load_font(FONT_BOLD if bold else FONT_REGULAR, size)
        for line in wrap(text, font, width):
            texts.append({"x": x, "y": y, "size": size, "bold": bold, "color": color, "text": line})
            y += size * LINE_GAP
        return y

    if cover:
        font = load_font(FONT_BOLD, COVER_SIZE)
        lines = wrap(slide["title"], font, max_width)
        y = (HEIGHT - len(lines) * COVER_SIZE * LINE_GAP) / 2
        add(slide["title"], MARGIN, y, COVER_SIZE, True, INK, max_width)
        return {"texts": texts, "rule": None}

    y = add(slide["title"], MARGIN, MARGIN, TITLE_SIZE, True, INK, max_width)
    if "subtitle" in slide:
        y = add(slide["subtitle"], MARGIN, y + 4, SUBTITLE_SIZE, False, MUTED, max_width)
    rule_y = y + 20
    y = rule_y + 40
    indent = TEXT_SIZE * 1.2
    for item in slide.get("content", []):
        texts.append({"x": MARGIN, "y": y, "size": TEXT_SIZE, "bold": False, "color": ACCENT, "text": BULLET})
        y = add(item["text"], MARGIN + indent, y, TEXT_SIZE, False, INK, max_width - indent) + 12
    return {"texts": texts, "rule": (rule_y, MARGIN, WIDTH - MARGIN)}


def render_png(slide: Slide, cover: bool, path: Path | str) -> None:
    geo = layout(slide, cover)
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)
    if geo["rule"]:
        y, x1, x2 = geo["rule"]
        draw.line([(x1, y), (x2, y)], fill=ACCENT, width=3)
    for t in geo["texts"]:
        font = load_font(FONT_BOLD if t["bold"] else FONT_REGULAR, t["size"])
        draw.text((t["x"], t["y"]), t["text"], fill=t["color"], font=font)
    image.save(path)


def render_svg(slide: Slide, cover: bool, path: Path | str) -> None:
    geo = layout(slide, cover)
    drawing = svgwrite.Drawing(size=(WIDTH, HEIGHT), profile="full")
    drawing.viewbox(0, 0, WIDTH, HEIGHT)
    drawing["font-family"] = "DejaVu Sans, Verdana, sans-serif"
    drawing.add(drawing.rect(insert=(0, 0), size=(WIDTH, HEIGHT), fill=BG))
    if geo["rule"]:
        y, x1, x2 = geo["rule"]
        drawing.add(drawing.line(start=(x1, y), end=(x2, y), stroke=ACCENT, stroke_width=3))
    for t in geo["texts"]:
        # PIL positions the top of the line; SVG positions the baseline (~0.9 em below).
        drawing.add(
            drawing.text(
                t["text"],
                insert=(t["x"], t["y"] + t["size"] * 0.95),
                font_size=t["size"],
                font_weight="bold" if t["bold"] else "normal",
                fill=t["color"],
            )
        )
    drawing.saveas(path, pretty=True)


def load_deck(path: str) -> Deck:
    try:
        return load(path)
    except (OSError, yaml.YAMLError, DataError) as err:
        raise click.ClickException(str(err)) from None


@click.command()
@click.argument("data", default=DEFAULT_DATA)
@click.option("--format", "fmt", type=click.Choice(FORMATS), default="interactive", show_default=True, help="interactive presents the deck in the terminal; ascii prints it; png and svg export it.")
@click.option("-o", "--output", help="Output directory for png and svg (default: deck/; with ascii, writes the text to this file instead of printing).")
def cli(data: str, fmt: str, output: str | None) -> None:
    """Present a slide deck from a YAML file."""
    deck = load_deck(data)

    # Without a terminal to drive (pipes, -o FILE), the interactive mode falls back to plain text.
    if fmt == "interactive" and sys.stdin.isatty() and sys.stdout.isatty() and output is None:
        curses.wrapper(present, deck)
        return

    if fmt in ("interactive", "ascii"):
        text = "\n".join(ascii_deck(deck))
        if output is None:
            click.echo(text)
        else:
            Path(output).write_text(text + "\n", encoding="utf-8")
            click.echo(f"Wrote {output}")
        return

    directory = Path(output or DEFAULT_OUTPUT)
    directory.mkdir(parents=True, exist_ok=True)
    renderer = render_png if fmt == "png" else render_svg
    for number, slide in enumerate(pages_of(deck), start=1):
        renderer(slide, number == 1, directory / f"slide-{number:02d}.{fmt}")
    click.echo(f"Wrote {len(deck['slides']) + 1} slides to {directory}/")


if __name__ == "__main__":
    cli()
