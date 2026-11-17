import DOMPurify from "dompurify";
import { marked } from "marked";

DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A") {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  }
});

/** Markdown from the agents, rendered and sanitized (links open in a new tab). */
export function renderMarkdown(md: string): string {
  return DOMPurify.sanitize(marked.parse(md, { async: false, gfm: true }) as string);
}

/** State values are often JSON inside a ```json fence; parse them for display when possible. */
export function parseValue(value: unknown): unknown {
  if (typeof value !== "string") return value;
  const fence = value.match(/```(?:json)?\s*([\s\S]*?)```/);
  const candidate = (fence ? fence[1] : value).trim();
  if (candidate.startsWith("{") || candidate.startsWith("[")) {
    try {
      return JSON.parse(candidate);
    } catch {
      return value;
    }
  }
  return value;
}

export function pretty(value: unknown): string {
  const v = parseValue(value);
  return typeof v === "string" ? v : JSON.stringify(v, null, 2);
}

export function seconds(s: number): string {
  if (s < 60) return `${s.toFixed(1)} s`;
  const m = Math.floor(s / 60);
  return `${m} min ${Math.round(s - m * 60)} s`;
}

export function argSummary(args: Record<string, unknown>, max = 60): string {
  const text = Object.entries(args)
    .map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(", ");
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

export function download(name: string, content: string, type: string) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([content], { type }));
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}

export function guideHtml(md: string, title: string): string {
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>${title.replace(/</g, "&lt;")}</title>
<style>body{font:16px/1.6 system-ui,sans-serif;max-width:46rem;margin:2rem auto;padding:0 1rem;color:#1D2433}
table{border-collapse:collapse;width:100%;margin:1rem 0}th,td{border-bottom:1px solid #d5dbe5;padding:.35rem .5rem;text-align:left;vertical-align:top}
a{color:#0F7B9C}h1,h2,h3{line-height:1.25}</style></head><body>${renderMarkdown(md)}</body></html>`;
}
