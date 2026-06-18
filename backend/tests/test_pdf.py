import pytest

from audit.models import AuditJob, AuditRun, AuditSuite
from github_app.models import Installation


@pytest.fixture
def suite(db):
    return AuditSuite.objects.create(name="test-suite", model="claude-haiku-4-5")


@pytest.fixture
def run(db, suite):
    inst = Installation.objects.create(
        installation_id=42, account_login="octocat", account_type="User"
    )
    job = AuditJob.objects.create(
        installation=inst, repo_full_name="octocat/hello", email="user@example.com"
    )
    return AuditRun.objects.create(
        job=job,
        suite=suite,
        status=AuditRun.Status.COMPLETED,
        markdown="## Hello World\n\nTest.",
        summary="summary",
    )


@pytest.mark.django_db
def test_suite_template_takes_priority(run, tmp_path, settings):
    """Suite template is used even when REPORT_TEMPLATE_PATH is set."""
    from audit.pdf import _load_template_string

    env_file = tmp_path / "env.html"
    env_file.write_text("<html>ENV</html>")
    settings.REPORT_TEMPLATE_PATH = str(env_file)
    run.suite.report_template = "<html>SUITE</html>"
    run.suite.save(update_fields=["report_template"])
    assert _load_template_string(run) == "<html>SUITE</html>"


@pytest.mark.django_db
def test_env_path_used_when_suite_template_blank(run, tmp_path, settings):
    from audit.pdf import _load_template_string

    env_file = tmp_path / "env.html"
    env_file.write_text("<html>ENV</html>")
    settings.REPORT_TEMPLATE_PATH = str(env_file)
    assert _load_template_string(run) == "<html>ENV</html>"


@pytest.mark.django_db
def test_returns_none_when_no_overrides(run, settings):
    from audit.pdf import _load_template_string

    settings.REPORT_TEMPLATE_PATH = ""
    assert _load_template_string(run) is None


@pytest.mark.django_db
def test_render_pdf_html_contains_toc(run):
    """Assembled HTML contains ToC with correct anchor (run.markdown has ## Hello)."""
    from audit.pdf import _render_html

    html = _render_html(run)
    assert 'class="toc"' in html
    assert 'href="#hello-world"' in html


def test_render_markdown_to_html_deduplicates_slugs() -> None:
    from audit.rendering import render_markdown_to_html

    html, items = render_markdown_to_html("## Findings\n\n## Findings\n\ntext")
    slugs = [slug for _, _, slug in items]
    assert slugs == ["findings", "findings-2"]
    assert 'id="findings-2"' in html


@pytest.mark.django_db
def test_render_html_toc_escapes_special_chars(run):
    from audit.pdf import _render_html

    run.markdown = "## A & B\n\ntext"
    html = _render_html(run)
    assert "A &amp; B" in html
