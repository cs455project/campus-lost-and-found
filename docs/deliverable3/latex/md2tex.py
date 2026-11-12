"""Convert a report written in our Markdown subset into a LaTeX body.

    python md2tex.py md/01_test_reports.md body/01_test_reports.tex

Supported: ## / ### / #### headings, paragraphs, **bold**, *italic*, `code`,
[links](url), bullet and numbered lists, pipe tables (rendered as booktabs
longtables with column widths from their content), ``` code blocks, and
two directives of our own:

    @@FIG name@@          -> \\input{figures/name}
    ```latex ... ```      -> raw LaTeX, passed through unchanged
    @@BOX kind Title@@ ... @@END@@   -> keybox / notebox / goodbox

The first line (# Title) and the summary paragraph straight after it are
handled by the report's own .tex file, so they are skipped here.
"""

import re
import sys

SPECIAL = {
    "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
    "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}


def esc(text: str) -> str:
    return "".join(SPECIAL.get(ch, ch) for ch in text)


def esc_code(text: str) -> str:
    out = []
    for ch in text:
        out.append(SPECIAL.get(ch, ch))
        if ch in "/_.-=,:(":
            out.append(r"\allowbreak{}")
    return "".join(out)


def inline(text: str) -> str:
    """Inline Markdown -> LaTeX."""
    text = text.replace("\\_", "_").replace("\\]", "]").replace("&#91;", "[")
    parts = re.split(r"(`[^`]+`)", text)
    out = []
    for part in parts:
        if part.startswith("`") and part.endswith("`") and len(part) > 1:
            out.append(r"\texttt{" + esc_code(part[1:-1]) + "}")
            continue
        # links first, so their text and urls are handled separately
        pieces = re.split(r"(\[[^\]]+\]\([^)]+\))", part)
        for piece in pieces:
            m = re.fullmatch(r"\[([^\]]+)\]\(([^)]+)\)", piece)
            if m:
                url = m.group(2).replace("%", r"\%").replace("#", r"\#")
                out.append(r"\href{" + url + "}{" + emph(esc(m.group(1))) + "}")
            else:
                out.append(emph(esc(piece)))
    return "".join(out)


def emph(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\\textbf{\1}", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\\emph{\1}", text)
    # bare URLs
    text = re.sub(r"(?<![{/])\b(https://[^\s,;)]+)", lambda m: r"\url{" + m.group(1).replace(r"\_", "_") + "}", text)
    return text


def split_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    cells, cur, in_code = [], "", False
    for ch in line:
        if ch == "`":
            in_code = not in_code
        if ch == "|" and not in_code:
            cells.append(cur.strip())
            cur = ""
        else:
            cur += ch
    cells.append(cur.strip())
    return cells


def plain_len(cell: str) -> int:
    return len(re.sub(r"[*`]|\]\([^)]*\)|\[", "", cell))


def table(rows: list[list[str]], opts: dict) -> str:
    header, body = rows[0], rows[1:]
    n = len(header)
    size = opts.get("size") or (r"\footnotesize" if n >= 6 else r"\small")
    sep = 4 if n >= 6 else 5
    # Absolute estimate (pt) on a 16.6 cm text width, then normalised.
    cw = 4.6 if size == r"\footnotesize" else 5.2   # average character width
    avail = 470 - 2 * sep * (n - 1)
    lens = [[plain_len(r[i]) if i < len(r) else 0 for r in body] or [0] for i in range(n)]
    natural, minimum, is_short = [], [], []
    for i in range(n):
        words = [w for r in body if i < len(r) for w in re.split(r"[\s/]+", re.sub(r"[*`]", "", r[i]))]
        lw = max((len(w) for w in words), default=3)
        head = plain_len(header[i]) * 1.12          # bold header, may wrap on spaces
        head_word = max(len(w) for w in header[i].split()) * 1.12 if header[i].split() else 3
        minimum.append(max(min(lw, 16), head_word, 3) * cw * 1.12 + 4)
        mx = max(lens[i]); avg = sum(lens[i]) / len(lens[i])
        is_short.append(max(mx, head) <= 18)
        natural.append(max(mx, head) * cw * 1.12 + 6 if is_short[-1] else max(avg * 1.25, 18) * cw)
    widths = natural[:]
    if sum(widths) > avail:
        short = [i for i in range(n) if is_short[i]]
        fixed = sum(widths[i] for i in short)
        long_ = [i for i in range(n) if i not in short]
        rest = max(avail - fixed, 0)
        tot = sum(natural[i] for i in long_) or 1
        for i in long_:
            widths[i] = max(minimum[i], rest * natural[i] / tot)
        if sum(widths) > avail:   # still too wide: scale everything down to fit
            long_ = long_ or list(range(n))
            over = sum(widths) - avail
            room = sum(max(widths[i] - minimum[i], 0) for i in long_)
            if room >= over:
                for i in long_:
                    widths[i] -= over * max(widths[i] - minimum[i], 0) / room
            else:  # nothing fits naturally: everyone gives up width in proportion
                for i in long_:
                    widths[i] = min(widths[i], minimum[i])
                k = avail / sum(widths)
                widths = [w * k for w in widths]
    else:  # stretch long columns to fill the line
        extra = avail - sum(widths)
        long_ = [i for i in range(n) if not is_short[i]] or list(range(n))
        tot = sum(natural[i] for i in long_)
        for i in long_:
            widths[i] += extra * natural[i] / tot
    if "widths" in opts:
        widths = [float(x) for x in opts["widths"].split(",")]
    weights = [w + 2 * sep for w in widths]
    total = sum(weights)
    cols = "".join(
        r">{\raggedright\arraybackslash}p{\dimexpr %.4f\linewidth-%dpt\relax}" % (w / total, 2 * sep)
        for w in weights)
    head = " & ".join(r"\textbf{" + inline(h) + "}" for h in header) + r" \\"
    lines = [r"{" + size + r"\setlength{\tabcolsep}{%dpt}" % sep,
             r"\begin{longtable}{@{}" + cols.replace("p{", "p{", 1) + "@{}}",
             r"\toprule", head, r"\midrule", r"\endfirsthead",
             r"\toprule", head, r"\midrule", r"\endhead",
             r"\midrule\multicolumn{%d}{r}{\scriptsize\itshape\color{soft}continued on the next page}\\" % n,
             r"\endfoot", r"\bottomrule", r"\endlastfoot"]
    for r in body:
        r = (r + [""] * n)[:n]
        cells = []
        for c in r:
            t = inline(c)
            if c.strip() in ("Pass", "Stopped", "Clean", "Fixed"):
                t = r"\textcolor{found}{\textbf{" + t + "}}"
            cells.append(t)
        lines.append(" & ".join(cells) + r" \\")
    lines += [r"\end{longtable}}", ""]
    # fix the column spec: we built a @{}..@{} spec, remove the outer extra sep
    return "\n".join(lines)


def convert(md: str) -> str:
    lines = md.splitlines()
    out: list[str] = []
    i = 0
    # skip title, date line and the summary paragraph (handled by the wrapper)
    while i < len(lines) and not lines[i].startswith("# "):
        i += 1
    i += 1
    skipped_summary = False
    para: list[str] = []
    list_kind = None
    table_opts: dict = {}
    boxes: list[str] = []

    def flush_para():
        nonlocal para
        if para:
            out.append(inline(" ".join(s.strip() for s in para)))
            out.append("")
            para = []

    def close_list():
        nonlocal list_kind
        if list_kind:
            out.append(r"\end{%s}" % list_kind)
            out.append("")
            list_kind = None

    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not skipped_summary:
            if not s or re.match(r"^(Nov \d|\d+ Nov)", s):
                i += 1
                continue
            if s.startswith("**"):
                # summary paragraph: skip it (rendered as the key box)
                while i < len(lines) and lines[i].strip():
                    i += 1
                skipped_summary = True
                continue
            skipped_summary = True
        if s.startswith("@@TABLE"):
            table_opts = dict(kv.split("=", 1) for kv in s.strip("@").split()[1:])
            i += 1
            continue
        if s.startswith("@@FIG"):
            flush_para(); close_list()
            out.append(r"\input{figures/%s}" % s.strip("@").split()[1])
            out.append("")
            i += 1
            continue
        if s.startswith("@@BOX"):
            flush_para(); close_list()
            _, kind, *title = s.strip("@").split()
            out.append(r"\begin{%s}{%s}" % (kind, inline(" ".join(title))))
            boxes.append(kind)
            i += 1
            continue
        if s == "@@END@@":
            flush_para(); close_list()
            out.append(r"\end{%s}" % boxes.pop())
            out.append("")
            i += 1
            continue
        if s.startswith("```"):
            flush_para(); close_list()
            lang = s[3:].strip()
            block = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            i += 1
            if lang == "latex":
                out.extend(block)
            else:
                out.append(r"\begin{cmdbox}")
                for b in block:
                    out.append(esc_code(b) + r"\par")
                out.append(r"\end{cmdbox}")
            out.append("")
            continue
        m = re.match(r"^(#{2,4})\s+(.*)$", s)
        if m:
            flush_para(); close_list()
            level = len(m.group(1))
            title = re.sub(r"^\d+\.\s+", "", m.group(2))
            star = "*" if title.lower() in ("summary", "sources") or m.group(2).startswith("Appendix") else ""
            cmd = {2: "section", 3: "subsection", 4: "subsubsection"}[level]
            out.append("\\%s%s{%s}" % (cmd, star, inline(title)))
            if star and level == 2:
                out.append(r"\addcontentsline{toc}{section}{%s}" % inline(title))
            out.append("")
            i += 1
            continue
        if s.startswith("|"):
            flush_para(); close_list()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = split_row(lines[i])
                if not all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            out.append(table(rows, table_opts))
            table_opts = {}
            continue
        m = re.match(r"^(-|\d+\.)\s+(.*)$", s)
        if m:
            flush_para()
            kind = "itemize" if m.group(1) == "-" else "enumerate"
            if list_kind != kind:
                close_list()
                out.append(r"\begin{%s}" % kind)
                list_kind = kind
            item = m.group(2)
            i += 1
            while i < len(lines) and lines[i].startswith("  ") and lines[i].strip():
                item += " " + lines[i].strip()
                i += 1
            out.append(r"\item " + inline(item))
            continue
        if not s:
            flush_para(); close_list()
            i += 1
            continue
        close_list()
        para.append(s)
        i += 1
    flush_para(); close_list()
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    with open(src, encoding="utf-8") as f:
        tex = convert(f.read())
    with open(dst, "w", encoding="utf-8") as f:
        f.write(tex)
