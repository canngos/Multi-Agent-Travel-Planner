"""Deterministic link grounding for the research agents, the planner and the guide writer.

LLMs copying URLs sometimes "improve" them (http -> https, adding www., dropping a trailing slash, decoding
%C3%A9 to é) and can invent one from memory. Every link shown to the user must come from a tool result, so:
- a link that matches a known link after normalising is restored to the known spelling;
- a link that matches nothing is removed (JSON: url -> null; Markdown: a link keeps its text but loses the
  URL, a bare URL is deleted).
The research agents' and comparers' outputs are grounded against the raw tool results in the session
(`ground_research_output`); later agents are grounded against those research outputs (`known_urls`).
"""
import json
import re
from urllib.parse import unquote

from .output import extract_json, was_repaired

_URL_CHARS = r"[^\s\"'<>()\[\]|]"
URL_RE = re.compile(rf"https?://(?:{_URL_CHARS}|\({_URL_CHARS}*\))+")  # balanced brackets: .../Sé_(Lisbon)
_TARGET = r"https?://(?:[^()\s]|\([^()\s]*\))+"
MD_LINK_RE = re.compile(rf"\[([^\]]*)\]\(({_TARGET})\)")
_MD_LINK_SPLIT = re.compile(rf"(\[[^\]]*\]\({_TARGET}\))")
QUOTED_TARGET_RE = re.compile(r"\]\(\s*['\"<]\s*(https?://[^'\"<>\s]+)\s*['\">]\s*\)")
RESEARCH_KEYS = ("transport_summary", "stay_summary", "culture_findings", "food_findings",
                 "flight_options", "ground_options", "airbnb_options", "hotel_options")
ALWAYS_ALLOWED = ("https://transitous.org/sources/",)  # attribution footer


def _norm(url: str) -> str:
    """Comparison key: no scheme, no "www.", no trailing slash, host in lower case, path percent-decoded.
    The path keeps its case: short links like kiwi.com/u/AbC123 are case-sensitive."""
    # also repairs "https://https://..." and "httpshttps://..." (both seen in model output)
    u = re.sub(r"^(?:https?(?::/*|(?=https?:)))+", "", url.strip().rstrip(".,;"), flags=re.I)
    u = re.sub(r"^www\.", "", u, flags=re.I)
    host, slash, path = u.partition("/")
    return (host.lower() + slash + unquote(path)).rstrip("/")


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


EMPTY_LINK_RE = re.compile(r"(\s*[–—-]\s*|\s*)\[([^\]]*)\]\(\s*(?:null|none|undefined|nan|#)?\s*\)", re.IGNORECASE)
_LINK_WORDS = {"source", "link", "website", "more", "here", "info"}


def _no_target(m: re.Match) -> str:
    """A link without a target: its label stays as text, unless the label is only "source" or the like."""
    label = m.group(2).strip()
    return "" if label.lower() in _LINK_WORDS or not label else m.group(1) + label


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
    text = EMPTY_LINK_RE.sub(_no_target, text)  # [source](null): a live Monaco guide had two
    text = MD_LINK_RE.sub(link, text)

    def bare(m: re.Match) -> str:
        url = m.group(0)
        clean = url.rstrip(".,;")
        good = ground_url(clean, known)
        if good is None:  # the web UI turns bare URLs into links, so an invented one must go too
            removed.append(clean)
            return url[len(clean):]
        if good != clean:
            fixed.append(url)
            return good + url[len(clean):]
        return url

    parts = _MD_LINK_SPLIT.split(text)  # leave the already-checked links alone
    text = "".join(p if MD_LINK_RE.fullmatch(p) else URL_RE.sub(bare, p) for p in parts)
    return text, fixed, removed


def ground_data(node, known: dict[str, str], fixed: list, removed: list, text: bool = False):
    """Walks parsed agent JSON and grounds url / source_url / source_urls fields in place.

    text=True also grounds URLs written inside other strings (details, notes, summaries), as Markdown."""
    if isinstance(node, list):
        for i, item in enumerate(node):
            if text and isinstance(item, str):
                node[i] = _ground_text(item, known, fixed, removed)
            else:
                ground_data(item, known, fixed, removed, text)
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
        for key in ("source_urls", "fallback_links"):
            if isinstance(node.get(key), list):
                node[key] = _ground_list(node[key], known, fixed, removed)
        for key, value in node.items():
            if key in ("url", "source_url", "source_urls", "fallback_links"):
                continue
            if isinstance(value, (list, dict)):
                ground_data(value, known, fixed, removed, text)
            elif text and isinstance(value, str):
                node[key] = _ground_text(value, known, fixed, removed)


def _ground_list(urls: list, known: dict[str, str], fixed: list, removed: list) -> list:
    kept = []
    for url in urls:
        good = ground_url(url, known) if isinstance(url, str) else None
        if good is None:
            removed.append(url)
        else:
            if good != url:
                fixed.append(url)
            kept.append(good)
    return kept


def _ground_text(value: str, known: dict[str, str], fixed: list, removed: list) -> str:
    if "http" not in value:
        return value
    out, f, r = ground_markdown(value, known)
    fixed += f
    removed += r
    return out


def tool_result_urls(session) -> dict[str, str]:
    """normalised URL -> URL, for every URL in the tool results (function responses) of the session so far."""
    known = {}
    for event in getattr(session, "events", None) or []:
        content = getattr(event, "content", None)
        for part in (getattr(content, "parts", None) or []):
            response = getattr(getattr(part, "function_response", None), "response", None)
            if response:
                for url in URL_RE.findall(json.dumps(response, ensure_ascii=False, default=str)):
                    url = url.rstrip(".,;")
                    known.setdefault(_norm(url), url)
    return known


def ground_research_output(output_key: str, strict: bool = False):
    """After-agent callback factory for the research agents and comparers: every link in their JSON output must
    come from a tool result in the session (their outputs are the "known links" of every later agent).

    Without any tool result in the session (e.g. an agent run alone on prepared state) nothing is changed, unless
    `strict`: then every link goes (the on-demand researcher runs in its own session, which must hold its sources)."""
    def callback(callback_context):
        state = callback_context.state
        try:
            data = extract_json(state.get(output_key) or "")
        except ValueError:
            return None
        if was_repaired(state.get(output_key) or ""):  # later agents and the code checks read clean JSON
            state[output_key] = "```json\n" + json.dumps(data, ensure_ascii=False, indent=1) + "\n```"
            state["json_repaired"] = list(dict.fromkeys((state.get("json_repaired") or []) + [output_key]))
        known = tool_result_urls(callback_context.session)
        if not known and not strict:
            return None
        for url in ALWAYS_ALLOWED:
            known.setdefault(_norm(url), url)
        fixed, removed = [], []
        ground_data(data, known, fixed, removed, text=True)
        # Only invented links make a rewrite necessary: a respelled one (http/https, www., trailing slash) names the
        # same page, and later agents treat both spellings as one link.
        if removed:
            data["notes"] = (str(data.get("notes") or "") + f" Removed {len(removed)} link(s) that no tool "
                                                             "returned.").strip()
            state[output_key] = "```json\n" + json.dumps(data, ensure_ascii=False, indent=1) + "\n```"
            fixes = dict(state.get("research_link_fixes") or {})
            fixes[output_key] = {"fixed": fixed, "removed": removed}
            state["research_link_fixes"] = fixes
        return None

    callback.__name__ = f"ground_{output_key}"
    return callback


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
