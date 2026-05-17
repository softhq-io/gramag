"""Azure OpenAI vision extraction for PDF pages and image assets."""

import time
import base64
from pathlib import Path

from ai_client import chat, image_content, vision_chat
from config import AZURE_OPENAI_CHAT_DEPLOYMENT, AZURE_OPENAI_VISION_DEPLOYMENT

VISION_MODEL = AZURE_OPENAI_VISION_DEPLOYMENT
DEEP_VISION_MODEL = AZURE_OPENAI_VISION_DEPLOYMENT


PAGE_EXTRACTION_PROMPT = """\
You are a technical documentation analyst for industrial machines (Swiss print/mailing industry).

Analyze this manual page image. Extract ALL knowledge visible on this page.

IMPORTANT: Focus especially on information that is ONLY visible in the images/diagrams
and would NOT be captured by copy-pasting the text from this page. For example:
- Spatial positions of components ("RS232 port is third from top on the left side")
- Physical appearance (color, shape, size relative to other parts)
- How components connect to each other visually
- Button icons and their layout
- Warning symbol graphics
- Assembly sequences shown in photos
- Part numbers next to diagrams
- Measurements/dimensions in drawings

Structure your response as:

## Page summary
One-line description of what this page is about.

## Components (from diagrams/photos)
For each labeled component: [ref letter/number] Name — spatial position, visual description.

## Visual-only knowledge
Facts that are ONLY visible in images, not written in the text on this page.

## Procedures shown visually
Any step-by-step actions depicted in photos/diagrams.

## Relationships
How components connect, what is adjacent to what.

## Part numbers / references
Any numbers, codes, or references visible on the page.

Be thorough but concise. Output in the same language as the page (German/English)."""


IMAGE_CAPTION_PROMPT = """\
You are analyzing a technical asset file from a machine documentation archive.

This image might be:
- A network/wiring diagram
- A screenshot of software settings (IP addresses, version numbers, hostnames)
- A product bitmap (a design/logo/template used by the machine)
- A photo of a component or installation
- A nameplate / Typenschild

Output (in the language of the image):

## Asset type
What kind of image is this?

## Content
Describe what is visible. Include:
- All visible text (exactly as written — IP addresses, hostnames, version numbers, part numbers)
- Diagram structure (nodes, connections)
- Colors and indicators

## Searchable summary
2-3 sentences a technician could match to a query."""


CONFIG_SUMMARY_PROMPT = """\
Analyze this configuration/data file from a Swiss industrial mailing machine.
Filenames often encode the customer code (e.g. 'ENIWA619.TXT' = customer Eniwa, job 619).

File name: {name}
Content (first 4000 chars):
---
{content}
---

Output ONLY the following, with NO preamble, NO "Based on...", NO meta-commentary.
Be specific and dense — a technician searches for concrete strings (customer names,
addresses, sizes, job numbers, format codes).

CUSTOMER: <name or code, or "unknown">
PURPOSE: <one sentence — what does the machine do with this file>
KEY VALUES: <comma-separated: sizes, formats, counts, addresses, codes actually present in content>
SEARCHABLE: <3-4 sentences describing the file so a natural-language query can hit it>"""


def _gen(content: list[dict], model: str = VISION_MODEL, max_tokens: int = 3000) -> str:
    return vision_chat(content, deployment=model, temperature=0.1, max_tokens=max_tokens)


def vision_extract_page(png_path: str | Path, *, deep: bool = False) -> str:
    content = [
        {"type": "text", "text": PAGE_EXTRACTION_PROMPT},
        image_content(png_path, mime_type="image/png"),
    ]
    return _gen(content, model=DEEP_VISION_MODEL if deep else VISION_MODEL)


AZURE_OPENAI_NATIVE_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


def vision_caption_image(img_path: str | Path, *, deep: bool = False) -> str:
    p = Path(img_path)
    ext = p.suffix.lower().lstrip(".")
    native_mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                   "gif": "image/gif"}.get(ext)

    if native_mime:
        with open(p, "rb") as f:
            img_bytes = f.read()
        mime = native_mime
    else:
        # BMP/PCX/TIFF and unknown → convert to PNG via Pillow
        from PIL import Image
        import io
        img = Image.open(p).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        img_bytes = buf.getvalue()
        mime = "image/png"

    content = [
        {"type": "text", "text": IMAGE_CAPTION_PROMPT},
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:{mime};base64,{base64.b64encode(img_bytes).decode('ascii')}",
                "detail": "high",
            },
        },
    ]
    return _gen(content, model=DEEP_VISION_MODEL if deep else VISION_MODEL, max_tokens=2000)


def summarize_config(name: str, content: str) -> str:
    prompt = CONFIG_SUMMARY_PROMPT.format(name=name, content=content[:4000])
    return chat(prompt, deployment=AZURE_OPENAI_CHAT_DEPLOYMENT, temperature=0.1, max_tokens=1500)


def with_retry(fn, *args, tries: int = 3, backoff: float = 4.0, **kwargs):
    last = None
    for i in range(tries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last = e
            if i < tries - 1:
                time.sleep(backoff * (i + 1))
    raise last
