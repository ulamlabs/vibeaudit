import re
import tempfile
from pathlib import Path

import pypandoc
import typst

from audit.ai.output import PipelineReport

_TEMPLATE = Path(__file__).parent / "template.typ"

# Typst directives that can read files or execute code — should never appear in
# pandoc output, but caught here as belt-and-suspenders.
_UNSAFE_TYPST = re.compile(r"#(import|include|sys\.|eval\()", re.IGNORECASE)


def build_pdf(report: PipelineReport, output_path: Path) -> None:
    """Compile a PipelineReport to a PDF at output_path using typst."""
    source = _render_source(report)
    with tempfile.NamedTemporaryFile(suffix=".typ", mode="w", delete=False) as tmp:
        tmp.write(source)
        tmp_path = Path(tmp.name)
    try:
        # root=tmp_path.parent restricts #read/#import to the temp dir,
        # preventing file exfiltration from injected typst directives.
        pdf_bytes = typst.compile(str(tmp_path), root=str(tmp_path.parent))
        output_path.write_bytes(pdf_bytes)
    finally:
        tmp_path.unlink(missing_ok=True)


def _render_source(report: PipelineReport) -> str:
    """Render a self-contained typst source string from the report.

    Metadata (repo_name, dates, risk, summary) are injected as #let variables.
    The report body markdown is converted to typst markup via pandoc and appended
    after the template — it is never passed through a typst string literal.
    """
    template = _TEMPLATE.read_text()
    preamble = (
        f'#let repo_name = "{_escape(report.repo_name)}"\n'
        f'#let completed_at = "{_escape(report.completed_at)}"\n'
        f'#let risk_level = "{_escape(report.risk_level)}"\n'
        f'#let summary = "{_escape(report.summary)}"\n\n'
    )
    body_typst = _sanitize_typst(_markdown_to_typst(report.markdown))
    return preamble + template + "\n" + body_typst


def _markdown_to_typst(markdown: str) -> str:
    """Convert markdown to typst markup via pandoc.

    Uses markdown-raw_attribute format to disable raw typst passthrough blocks,
    preventing injected repo content from smuggling in typst directives.
    Pandoc also escapes bare # characters in prose to \\# automatically.
    """
    if not markdown.strip():
        return ""
    return pypandoc.convert_text(
        markdown,
        to="typst",
        format="markdown-raw_attribute",
    )


def _sanitize_typst(src: str) -> str:
    """Escape any remaining dangerous typst directives to their literal form.

    Belt-and-suspenders: pandoc should never emit #import/#include/etc. in its
    output, but if it somehow does, this ensures they render as text not code.
    """
    return _UNSAFE_TYPST.sub(lambda m: m.group(0).replace("#", "\\#"), src)


def _escape(text: str) -> str:
    """Escape a string for use inside typst double-quoted string literals."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
