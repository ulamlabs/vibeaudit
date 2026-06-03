"""
Management command for running the AI audit pipeline against a local repo path.

Usage:
    python manage.py run_audit_pipeline /path/to/repo --output-dir /tmp/audit-out

Writes to output-dir:
    report.json           — full PipelineReport
    report.md             — coherent markdown summary
    report.pdf            — compiled PDF (requires typst; skip with --no-pdf)
"""

import uuid
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError


class _FakeJob:
    """Minimal stand-in for AuditJob used by run_pipeline / build_pdf."""

    def __init__(self, repo_path: Path, output_dir: Path) -> None:
        self.pk = uuid.uuid4()
        self.clone_path = repo_path
        self.job_dir = output_dir
        self.repo_full_name = repo_path.name


class Command(BaseCommand):
    help = "Run the AI audit pipeline against a local repo for testing."

    def add_arguments(self, parser):
        parser.add_argument(
            "repo_path",
            type=Path,
            help="Path to the cloned repository to audit.",
        )
        parser.add_argument(
            "--output-dir",
            type=Path,
            default=Path("audit-output"),
            help="Directory to write outputs (default: ./audit-output).",
        )
        parser.add_argument(
            "--no-pdf",
            action="store_true",
            help="Skip PDF generation (useful when typst fonts are unavailable).",
        )
        parser.add_argument(
            "--suite",
            type=str,
            default=None,
            help="Name of the AuditSuite to run. Defaults to the is_default suite, "
            "or the in-code default agents if no suite exists.",
        )

    def handle(self, *args, **options):
        repo_path: Path = options["repo_path"].resolve()
        output_dir: Path = options["output_dir"].resolve()
        skip_pdf: bool = options["no_pdf"]

        if not repo_path.is_dir():
            raise CommandError(f"repo_path is not a directory: {repo_path}")

        output_dir.mkdir(parents=True, exist_ok=True)

        from audit.ai.agents import get_audit_agents
        from audit.ai.pipeline import report_to_markdown, run_pipeline
        from audit.ai.suites import suite_to_agent_definitions
        from audit.models import AuditSuite

        suite_name = options["suite"]
        suite = None
        if suite_name:
            suite = AuditSuite.objects.filter(name=suite_name).first()
            if suite is None:
                raise CommandError(f"No AuditSuite named {suite_name!r}.")
        else:
            suite = AuditSuite.objects.filter(is_default=True).first()

        if suite is not None:
            agents = suite_to_agent_definitions(suite)
            orchestrator_prompt = suite.orchestrator_prompt or None
            self.stdout.write(f"Suite:      {suite.name}")
        else:
            agents = get_audit_agents()
            orchestrator_prompt = None
            self.stdout.write("Suite:      <in-code default agents>")

        self.stdout.write(f"Repo:       {repo_path}")
        self.stdout.write(f"Output dir: {output_dir}")
        self.stdout.write("")

        job = _FakeJob(repo_path, output_dir)
        self.stdout.write("Running orchestrated subagent pipeline...")
        result = run_pipeline(job, agents, orchestrator_prompt)
        report = result.report

        json_path = output_dir / "report.json"
        json_path.write_text(report.model_dump_json(indent=2))
        markdown_path = output_dir / "report.md"
        markdown_path.write_text(report_to_markdown(report))
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"JSON written: {json_path}"))
        self.stdout.write(self.style.SUCCESS(f"Markdown written: {markdown_path}"))

        if not skip_pdf:
            from audit.ai.report.typst_builder import build_pdf

            pdf_path = output_dir / "report.pdf"
            try:
                build_pdf(report, pdf_path)
                self.stdout.write(self.style.SUCCESS(f"PDF written:  {pdf_path}"))
            except Exception as exc:
                self.stderr.write(
                    self.style.WARNING(f"PDF generation failed (use --no-pdf to skip): {exc}")
                )
