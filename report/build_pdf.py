import markdown
from weasyprint import HTML, CSS
import pathlib

REPORT_DIR = pathlib.Path(__file__).resolve().parent
SRC = REPORT_DIR / "REPORT.md"
OUT = REPORT_DIR / "REPORT.pdf"

md_text = SRC.read_text(encoding="utf-8")

md = markdown.Markdown(extensions=[
    "extra",
    "toc",
    "sane_lists",
    "admonition",
], extension_configs={
    "toc": {"permalink": False},
})

html_body = md.convert(md_text)

html_doc = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>EdgeAI Predictive Monitor</title>
</head>
<body>
{html_body}
</body>
</html>"""

css = CSS(string="""
@page {
    size: A4;
    margin: 20mm 18mm;
    @bottom-center {
        content: counter(page);
        font-size: 9pt;
        color: #666;
    }
}
body {
    font-family: "DejaVu Sans", "Liberation Sans", sans-serif;
    font-size: 10.5pt;
    line-height: 1.5;
    color: #1a1a1a;
}
h1 {
    font-size: 20pt;
    margin-top: 1.4em;
    page-break-before: always;
    border-bottom: 2px solid #333;
    padding-bottom: 4px;
}
body > h1:first-of-type {
    page-break-before: avoid;
}
h2 {
    font-size: 15pt;
    margin-top: 1.3em;
    color: #222;
}
h3 {
    font-size: 12.5pt;
    margin-top: 1.1em;
}
p, li {
    orphans: 3;
    widows: 3;
}
table {
    border-collapse: collapse;
    width: 100%;
    margin: 1em 0;
    font-size: 9.5pt;
}
table, th, td {
    border: 1px solid #999;
}
th, td {
    padding: 4px 8px;
    text-align: left;
    vertical-align: top;
}
th {
    background: #eee;
}
img {
    max-width: 100%;
    display: block;
    margin: 1em auto;
}
pre, code {
    font-family: "DejaVu Sans Mono", monospace;
    font-size: 9pt;
}
pre {
    background: #f5f5f5;
    padding: 8px;
    border: 1px solid #ddd;
    white-space: pre-wrap;
    word-wrap: break-word;
}
code {
    background: #f0f0f0;
    padding: 1px 3px;
}
blockquote {
    border-left: 3px solid #999;
    margin-left: 0;
    padding-left: 1em;
    color: #444;
}
a {
    color: #0645ad;
    text-decoration: none;
}
hr {
    border: none;
    border-top: 1px solid #ccc;
    margin: 2em 0;
}
""")

HTML(string=html_doc, base_url=str(REPORT_DIR)).write_pdf(str(OUT), stylesheets=[css])
print("wrote", OUT)
