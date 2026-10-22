import { Fragment, type ReactNode } from "react";

// A small Markdown subset for assistant answers: headings, paragraphs, bullet and numbered
// lists, tables, quotes, code, bold, italic, inline code and https links. It builds React
// elements directly, so model output can never inject HTML.

const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\(https?:\/\/[^)\s]+\)|\*[^*\s][^*]*\*)/g;
const NUMERIC = /^[-+−]?[$]?\d[\d,.]*\s?(%|x|pts?|FP|fp)?$/;

function inline(text: string): ReactNode[] {
  return text.split(INLINE).map((part, i) => {
    if (!part) return null;
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={i}>{inline(part.slice(2, -2))}</strong>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={i}>{part.slice(1, -1)}</code>;
    const link = /^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/.exec(part);
    if (link) return <a key={i} href={link[2]} target="_blank" rel="noopener noreferrer">{link[1]}</a>;
    if (part.length > 2 && part.startsWith("*") && part.endsWith("*")) return <em key={i}>{part.slice(1, -1)}</em>;
    return <Fragment key={i}>{part}</Fragment>;
  });
}

const cells = (row: string) => row.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
const isRule = (row: string) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(row);

type Block =
  | { kind: "h"; level: number; text: string }
  | { kind: "p"; lines: string[] }
  | { kind: "ul" | "ol"; items: { text: string; depth: number }[] }
  | { kind: "table"; head: string[]; rows: string[][] }
  | { kind: "quote"; lines: string[] }
  | { kind: "code"; text: string }
  | { kind: "hr" };

export function parseBlocks(src: string): Block[] {
  const lines = src.replace(/\r\n?/g, "\n").split("\n");
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i]!;
    const trimmed = line.trim();
    if (!trimmed) { i++; continue; }
    if (trimmed.startsWith("```")) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i]!.trim().startsWith("```")) body.push(lines[i++]!);
      i++;
      blocks.push({ kind: "code", text: body.join("\n") });
      continue;
    }
    const heading = /^(#{1,4})\s+(.*)$/.exec(trimmed);
    if (heading) { blocks.push({ kind: "h", level: heading[1]!.length, text: heading[2]!.replace(/#+$/, "").trim() }); i++; continue; }
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) { blocks.push({ kind: "hr" }); i++; continue; }
    if (trimmed.startsWith("|") && i + 1 < lines.length && isRule(lines[i + 1]!)) {
      const head = cells(trimmed);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && lines[i]!.trim().startsWith("|")) rows.push(cells(lines[i++]!));
      blocks.push({ kind: "table", head, rows });
      continue;
    }
    const listKind = (l: string) => (/^\s*[-*+]\s+/.test(l) ? "ul" : /^\s*\d+[.)]\s+/.test(l) ? "ol" : null);
    const kind = listKind(line);
    if (kind) {
      const items: { text: string; depth: number }[] = [];
      while (i < lines.length && lines[i]!.trim()) {
        const l = lines[i]!;
        const k = listKind(l);
        const depth = Math.min(2, Math.floor((l.length - l.trimStart().length) / 2));
        if (k && (k === kind || depth > 0)) {
          items.push({ text: l.trim().replace(/^([-*+]|\d+[.)])\s+/, ""), depth });
        } else if (items.length && /^\s+/.test(l)) {
          items[items.length - 1]!.text += " " + l.trim(); // wrapped continuation line
        } else break;
        i++;
      }
      blocks.push({ kind, items });
      continue;
    }
    if (trimmed.startsWith(">")) {
      const body: string[] = [];
      while (i < lines.length && lines[i]!.trim().startsWith(">")) body.push(lines[i++]!.trim().replace(/^>\s?/, ""));
      blocks.push({ kind: "quote", lines: body });
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i]!.trim() && !/^(#{1,4}\s|```|\||>|\s*[-*+]\s|\s*\d+[.)]\s)/.test(lines[i]!.trim())) {
      para.push(lines[i++]!.trim());
    }
    if (!para.length) para.push(lines[i++]!.trim());
    blocks.push({ kind: "p", lines: para });
  }
  return blocks;
}

export function Markdown({ text }: { text: string }) {
  return (
    <div className="md">
      {parseBlocks(text).map((b, i) => {
        switch (b.kind) {
          case "h":
            return b.level <= 2 ? <h3 key={i}>{inline(b.text)}</h3> : <h4 key={i}>{inline(b.text)}</h4>;
          case "p":
            return <p key={i}>{b.lines.map((l, j) => <Fragment key={j}>{j > 0 && <br />}{inline(l)}</Fragment>)}</p>;
          case "ul":
          case "ol": {
            const Tag = b.kind;
            return (
              <Tag key={i}>
                {b.items.map((it, j) => <li key={j} className={it.depth ? `d${it.depth}` : undefined}>{inline(it.text)}</li>)}
              </Tag>
            );
          }
          case "table":
            return (
              <div key={i} className="table-wrap">
                <table className="data">
                  <thead><tr>{b.head.map((h, j) => <th key={j} className={b.rows.every((r) => NUMERIC.test(r[j] ?? "")) ? "n" : undefined}>{inline(h)}</th>)}</tr></thead>
                  <tbody>
                    {b.rows.map((r, j) => (
                      <tr key={j}>{b.head.map((_, k) => <td key={k} className={NUMERIC.test(r[k] ?? "") ? "n" : undefined}>{inline(r[k] ?? "")}</td>)}</tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          case "quote":
            return <blockquote key={i}>{b.lines.map((l, j) => <p key={j}>{inline(l)}</p>)}</blockquote>;
          case "code":
            return <pre key={i}><code>{b.text}</code></pre>;
          case "hr":
            return <hr key={i} />;
        }
      })}
    </div>
  );
}
