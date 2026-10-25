// Minimal, safe Markdown renderer for backend answers (headings, lists, tables, quotes, bold/italic).
// Builds React elements only — no HTML injection.
import type { ReactNode } from "react";

function inline(text: string, key: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|_[^_]+_|`[^`]+`)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    const k = `${key}-${i++}`;
    if (tok.startsWith("**")) out.push(<strong key={k}>{tok.slice(2, -2)}</strong>);
    else if (tok.startsWith("`")) out.push(<code key={k}>{tok.slice(1, -1)}</code>);
    else out.push(<em key={k}>{tok.slice(1, -1)}</em>);
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const cells = (row: string) => row.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());

export default function Markdown({ text }: { text: string }) {
  const lines = text.split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const key = `b${i}`;
    if (!line.trim()) { i++; continue; }
    if (line.startsWith("|")) {
      const rows: string[] = [];
      while (i < lines.length && lines[i].startsWith("|")) rows.push(lines[i++]);
      const [head, , ...body] = rows;
      blocks.push(
        <div className="md-table" key={key}><table>
          <thead><tr>{cells(head).map((c, j) => <th key={j}>{inline(c, `${key}h${j}`)}</th>)}</tr></thead>
          <tbody>{body.map((r, ri) => <tr key={ri}>{cells(r).map((c, j) => <td key={j} className={c === "Poor" ? "st-poor" : c === "Watch" ? "st-watch" : c === "Good" ? "st-good" : ""}>{inline(c, `${key}${ri}${j}`)}</td>)}</tr>)}</tbody>
        </table></div>,
      );
      continue;
    }
    if (/^#{1,4} /.test(line)) {
      const level = line.match(/^#+/)![0].length;
      const content = inline(line.replace(/^#+ /, ""), key);
      blocks.push(level <= 2 ? <h3 key={key}>{content}</h3> : <h4 key={key}>{content}</h4>);
      i++; continue;
    }
    if (line.startsWith("> ")) {
      blocks.push(<div className="banner" key={key}>{inline(line.slice(2), key)}</div>);
      i++; continue;
    }
    if (/^\s*(- |\d+\. )/.test(line)) {
      const items: string[] = [];
      const ordered = /^\s*\d+\. /.test(line);
      while (i < lines.length && /^\s*(- |\d+\. )/.test(lines[i])) items.push(lines[i++].replace(/^\s*(- |\d+\. )/, ""));
      const lis = items.map((t, j) => <li key={j}>{inline(t, `${key}l${j}`)}</li>);
      blocks.push(ordered ? <ol key={key}>{lis}</ol> : <ul key={key}>{lis}</ul>);
      continue;
    }
    const isSummary = line.startsWith("In short:");
    blocks.push(<p key={key} className={isSummary ? "in-short" : undefined}>{inline(isSummary ? line.slice(9).trim() : line, key)}</p>);
    i++;
  }
  return <div className="md">{blocks}</div>;
}
