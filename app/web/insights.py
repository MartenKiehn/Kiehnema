"""Small, readable discoveries from saved job results; no extra model calls."""

import hashlib
import json
import re
from pathlib import Path

from app.brand.extract import parse_color

COLOUR_NAMES = {
    "Black": (0, 0, 0), "White": (255, 255, 255), "Grey": (128, 128, 128),
    "Cream": (245, 240, 220), "Beige": (210, 190, 150), "Brown": (130, 80, 40),
    "Red": (220, 40, 40), "Orange": (240, 140, 30), "Yellow": (245, 220, 50),
    "Green": (50, 150, 60), "Turquoise": (50, 180, 180), "Blue": (50, 110, 210),
    "Dark blue": (25, 40, 90), "Purple": (140, 65, 170), "Pink": (235, 140, 180),
}


def job_insights(path: Path) -> list[dict]:
    saved = {}
    for name in ("crawl/crawl", "brand/brand", "profile", "concept"):
        try:
            data = json.loads((path / f"{name}.json").read_text())
            saved[name] = data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError):
            # A result may still be getting written; pick it up on the next poll.
            saved[name] = {}

    cards = []
    facts = saved["profile"].get("facts", [])
    if facts:
        cards.append({"title": "What your website tells us", "label": "THE FACTS",
                      "items": [{"text": fact["text"], "url": fact["source_url"]} for fact in facts]})
    else:
        excerpts = []
        for page in saved["crawl/crawl"].get("pages", []):
            data = page["data"]
            text = next((block["md"] for block in data.get("blocks", [])
                         if block["area"] == "main" and len(block["md"]) >= 40), data.get("description", ""))
            text = re.sub(r"^(?:#{1,6}|[-*>])\s+", "", text or "").strip()
            if text and not any(item["text"] == text for item in excerpts):
                excerpts.append({"text": text, "url": page["url"]})
            if len(excerpts) == 6:
                break
        if excerpts:
            cards.append({"title": "In your own words", "label": "FROM YOUR WEBSITE", "items": excerpts})

    colours = []
    for colour in saved["brand/brand"].get("colors", []):
        value = colour["hex"]
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            continue
        rgb, _ = parse_color(value)
        name = min(COLOUR_NAMES, key=lambda name: sum((a - b) ** 2 for a, b in zip(rgb, COLOUR_NAMES[name])))
        if not any(item["value"] == value for item in colours):
            colours.append({"value": value, "name": name})
        if len(colours) == 5:
            break
    if colours:
        cards.append({"title": "A look that feels like you", "label": "YOUR COLOURS", "colours": colours,
                      "items": [], "description": "These colours come from your website and help your video feel familiar."})

    scenes = []
    for number, scene in enumerate(saved["concept"].get("scenes", []), 1):
        texts = scene.get("texts", [])
        headline = next((item["text"] for item in texts if item["role"] == "headline"),
                        texts[0]["text"] if texts else "")
        if headline:
            scenes.append({"text": headline, "label": f"Scene {number}"})
    if scenes:
        cards.append({"title": "A first glimpse of your story", "label": "PLANNED FOR YOUR VIDEO", "items": scenes,
                      "description": "Draft scene text. You’ll be able to review and change it before the final video."})

    for card in cards:
        card["id"] = "insight-" + hashlib.sha256(json.dumps(card, sort_keys=True).encode()).hexdigest()[:16]
    return cards
