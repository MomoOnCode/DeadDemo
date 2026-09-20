"""Rich-text item tooltips built from the deadlock-api item asset, in the layout the game uses."""

from __future__ import annotations

import html
import re
from typing import Any

_SPAN_RE = re.compile(r"<span[^>]*class=\"([^\"]*)\"[^>]*>(.*?)</span>", re.S)
_TAG_RE = re.compile(r"<(?!/?(b|i|br|span)\b)[^>]+>")

SLOT_COLORS = {"weapon": "#e8a33c", "vitality": "#7fd15f", "spirit": "#b47ee8"}


def _clean_desc(text: str) -> str:
    """Keep the game's <span class="highlight"> emphasis as bold; drop everything else."""
    if not text:
        return ""
    text = _SPAN_RE.sub(lambda m: f"<b>{m.group(2)}</b>", text)
    text = _TAG_RE.sub("", text)
    return text.replace("\n", "<br>")


def _prop_line(props: dict[str, Any], name: str, important: bool = False) -> str | None:
    p = props.get(name)
    if not p:
        return None
    value = p.get("value")
    if value in (None, "", "0") and not important:
        return None
    label = p.get("label") or name
    postfix = p.get("postfix") or ""
    v = str(value) if value not in (None, "") else "?"
    if postfix and v.endswith(postfix):  # the API sometimes bakes the unit into the value already
        postfix = ""
    line = f"<b>{html.escape(v)}{html.escape(postfix)}</b> {html.escape(label)}"
    return f"<span style='color:#f2d27c'>{line}</span>" if important else line


def item_tooltip_html(raw: dict[str, Any], purchased_clock: str | None = None) -> str:
    name = html.escape(str(raw.get("name") or raw.get("class_name") or "Item"))
    slot = str(raw.get("item_slot_type") or "")
    tier = raw.get("item_tier")
    cost = raw.get("cost")
    color = SLOT_COLORS.get(slot, "#cccccc")
    header = f"<div style='font-size:13px'><b style='color:{color}'>{name}</b></div>"
    meta = " · ".join(x for x in [
        f"Tier {tier}" if tier else "", slot.title() if slot else "",
        f"{cost} souls" if cost else "", f"bought {purchased_clock}" if purchased_clock else "",
    ] if x)
    parts = [header, f"<div style='color:#aaaaaa'>{html.escape(meta)}</div>"]
    desc = raw.get("description") or {}
    props = raw.get("properties") or {}
    for section in raw.get("tooltip_sections") or []:
        stype = str(section.get("section_type") or "")
        lines: list[str] = []
        for attr in section.get("section_attributes") or []:
            loc = attr.get("loc_string")
            if loc:
                lines.append(f"<div>{_clean_desc(str(loc))}</div>")
            for pname in attr.get("important_properties") or []:
                line = _prop_line(props, pname, important=True)
                if line:
                    lines.append(f"<div>{line}</div>")
            for pname in (attr.get("elevated_properties") or []) + (attr.get("properties") or []):
                line = _prop_line(props, pname)
                if line:
                    lines.append(f"<div>{line}</div>")
        if lines:
            title = {"innate": "Passive", "active": "Active", "passive": "Passive"}.get(stype, stype.title())
            parts.append(f"<div style='margin-top:6px;color:#8fc7ff'><b>{html.escape(title)}</b></div>")
            parts.extend(lines)
    if not raw.get("tooltip_sections") and isinstance(desc, dict):
        for key in ("desc", "passive", "active"):
            if desc.get(key):
                parts.append(f"<div style='margin-top:6px'>{_clean_desc(str(desc[key]))}</div>")
    comps = raw.get("component_items") or []
    if comps:
        parts.append(f"<div style='margin-top:6px;color:#aaaaaa'>Builds from: {html.escape(', '.join(comps))}</div>")
    return "<div style='max-width:340px'>" + "".join(parts) + "</div>"
