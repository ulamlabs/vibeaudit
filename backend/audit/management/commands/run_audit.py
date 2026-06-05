"""
Management command for running the AI audit runner against a local repo path.

Usage:
    python manage.py run_audit /path/to/repo --output-dir /tmp/audit-out

Writes to output-dir:
    report.json           — full PipelineReport
    report.md             — coherent markdown summary
"""

import uuid
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError


class _FakeJob:
    """Minimal stand-in for AuditJob used by run_runner."""

    def __init__(self, repo_path: Path, output_dir: Path) -> None:
        self.pk = uuid.uuid4()
        self.clone_path = repo_path
        self.job_dir = output_dir
        self.repo_full_name = repo_path.name


class Command(BaseCommand):
    help = "Run the AI audit runner against a local repo for testing."

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
            "--suite",
            type=str,
            default=None,
            help="Name of the AuditSuite to run. Defaults to the is_default suite, "
            "or the in-code default agents if no suite exists.",
        )

    def handle(self, *args, **options):
        repo_path: Path = options["repo_path"].resolve()
        output_dir: Path = options["output_dir"].resolve()

        if not repo_path.is_dir():
            raise CommandError(f"repo_path is not a directory: {repo_path}")

        output_dir.mkdir(parents=True, exist_ok=True)

        from audit.ai.agents import get_audit_agents
        from audit.ai.runner import report_to_markdown, run_pipeline
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
        self.stdout.write("Running orchestrated subagent runner...")
        result = run_pipeline(job, agents, orchestrator_prompt)
        report = result.report

        json_path = output_dir / "report.json"
        json_path.write_text(report.model_dump_json(indent=2))
        markdown_path = output_dir / "report.md"
        markdown_path.write_text(report_to_markdown(report))
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"JSON written: {json_path}"))
        self.stdout.write(self.style.SUCCESS(f"Markdown written: {markdown_path}"))
