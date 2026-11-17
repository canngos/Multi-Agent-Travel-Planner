"""Deterministic link grounding for the planner and the guide writer.

LLMs copying URLs sometimes "improve" them (http -> https, adding www., dropping a trailing slash) and can
invent one from memory. Every link shown to the user must come from a tool result, so:
- a link that matches a researched link after normalising is restored to the researched spelling;
- a link that matches nothing is removed (itinerary: source_url -> null; guide: the Markdown link keeps its
  text but loses the URL).
The known links are every URL found in the research outputs in session state.
"""
import json
import re

from .output import extract_json

URL_RE = re.compile(r"https?://[^\s\"'<>()\[\]|]+")
MD_LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")
QUOTED_TARGET_RE = re.compile(r"\]\(\s*['\"<]\s*(https?://[^'\"<>\s)]+)\s*['\">]\s*\)")
RESEARCH_KEYS = ("transport_summary", "stay_summary", "culture_findings", "food_findings",
                 "flight_options", "ground_options", "airbnb_options", "hotel_options")
ALWAYS_ALLOWED = ("https://transitous.org/sources/",)  # attribution footer


def _norm(url: str) -> str:
    u = re.sub(r"^(?:https?://)+", "", url.strip().rstrip(".,;"))  # also repairs "https://https://..."
    u = re.sub(r"^www\.", "", u)
    return u.rstrip("/").lower()


def known_urls(state, keys=RESEARCH_KEYS) -> dict[str, str]:
    """normalised URL -> the URL as the tools wrote it, from the given state keys."""
    known = {}
    for key in keys:
        value = state.get(key)
        text = value if isinstance(value, str) else json.dumps(value or "", ensure_ascii=False)
        for url in URL_RE.findall(text):
            url = url.rstrip(".,;")
            known.setdefault(_norm(url), url)
    for url in ALWAYS_ALLOWED:
        known.setdefault(_norm(url), url)
    return known


def ground_url(url: str, known: dict[str, str]) -> str | None:
    """The researched spelling of `url`, or None if no tool returned it."""
    if any(url == k for k in known.values()):
        return url
    return known.get(_norm(url))


def ground_markdown(text: str, known: dict[str, str]) -> tuple[str, list[str], list[str]]:
    """Fixes or unlinks every Markdown link and bare URL. Returns (text, fixed, removed)."""
    fixed, removed = [], []

    def link(m: re.Match) -> str:
        label, url = m.group(1), m.group(2)
        good = ground_url(url, known)
        if good is None:
            removed.append(url)
            return label
        if good != url:
            fixed.append(url)
        return f"[{label}]({good})"

    text = QUOTED_TARGET_RE.sub(r"](\1)", text)  # [x]('https://..') or [x](<https://..>) -> [x](https://..)
    text = MD_LINK_RE.sub(link, text)

    def bare(m: re.Match) -> str:
        url = m.group(0)
        good = ground_url(url.rstrip(".,;"), known)
        if good is not None and good != url.rstrip(".,;"):
            fixed.append(url)
            return good + url[len(url.rstrip(".,;")):]
        return url  # unknown bare URLs are left for the review step; links are what users click

    parts = re.split(r"(\[[^\]]*\]\(https?://[^)\s]+\))", text)  # leave the already-checked links alone
    text = "".join(p if MD_LINK_RE.fullmatch(p) else URL_RE.sub(bare, p) for p in parts)
    return text, fixed, removed


def ground_data(node, known: dict[str, str], fixed: list, removed: list):
    """Walks parsed agent JSON and grounds url / source_url / source_urls fields in place."""
    if isinstance(node, list):
        for item in node:
            ground_data(item, known, fixed, removed)
    elif isinstance(node, dict):
        for key in ("url", "source_url"):
            url = node.get(key)
            if isinstance(url, str) and url.startswith("http"):
                good = ground_url(url, known)
                if good is None:
                    removed.append(url)
                    node[key] = None
                elif good != url:
                    fixed.append(url)
                    node[key] = good
        if isinstance(node.get("source_urls"), list):
            kept = []
            for url in node["source_urls"]:
                good = ground_url(url, known) if isinstance(url, str) else None
                if good is None:
                    removed.append(url)
                else:
                    if good != url:
                        fixed.append(url)
                    kept.append(good)
            node["source_urls"] = kept
        for value in node.values():
            if isinstance(value, (list, dict)):
                ground_data(value, known, fixed, removed)


def ground_itinerary(callback_context):
    """After-agent callback for the planner (and the Phase 8 reviser): grounds the links in state["itinerary"]."""
    state = callback_context.state
    try:
        plan = extract_json(state.get("itinerary") or "")
    except ValueError:
        return None
    fixed, removed = [], []
    ground_data(plan, known_urls(state), fixed, removed)
    if fixed or removed:
        if removed:
            plan["notes"] = (str(plan.get("notes") or "") + f" Removed {len(removed)} link(s) that no tool returned.").strip()
        state["itinerary"] = "```json\n" + json.dumps(plan, ensure_ascii=False, indent=1) + "\n```"
        state["itinerary_link_fixes"] = {"fixed": fixed, "removed": removed}
    return None


def ground_guide_before_save(tool, args, tool_context):
    """Before-tool callback for save_guide: grounds the links in the guide text before it is written."""
    if getattr(tool, "name", "") != "save_guide" or not isinstance(args.get("markdown_text"), str):
        return None
    state = tool_context.state
    known = known_urls(state, RESEARCH_KEYS + ("itinerary",))
    text, fixed, removed = ground_markdown(args["markdown_text"], known)
    args["markdown_text"] = text
    if fixed or removed:
        state["guide_link_fixes"] = {"fixed": fixed, "removed": removed}
    return None
