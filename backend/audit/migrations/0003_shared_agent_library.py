from django.db import migrations, models


def attach_agents_to_suites(apps, schema_editor):
    """Re-attach each existing agent to its suite via the new M2M before the FK is dropped."""
    AuditAgent = apps.get_model("audit", "AuditAgent")
    for agent in AuditAgent.objects.select_related("suite").all():
        agent.suite.agents_new.add(agent)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0002_auditsuite_report_template"),
    ]

    operations = [
        # 1. Add M2M under a temporary name (avoids clash with FK reverse accessor "agents")
        migrations.AddField(
            model_name="auditsuite",
            name="agents_new",
            field=models.ManyToManyField(
                blank=True,
                related_name="suites",
                to="audit.auditagent",
            ),
        ),
        # 2. Populate M2M from existing FK relationships
        migrations.RunPython(attach_agents_to_suites, reverse_code=noop),
        # 3. Drop unique_together before removing the suite FK it references
        migrations.AlterUniqueTogether(
            name="auditagent",
            unique_together=set(),
        ),
        # 4. Remove FK and auxiliary fields
        migrations.RemoveField(model_name="auditagent", name="suite"),
        migrations.RemoveField(model_name="auditagent", name="position"),
        migrations.RemoveField(model_name="auditagent", name="enabled"),
        # 5. Make agent_id globally unique
        migrations.AlterField(
            model_name="auditagent",
            name="agent_id",
            field=models.SlugField(
                help_text="Subagent identifier passed to the orchestrator.",
                max_length=80,
                unique=True,
            ),
        ),
        # 6. Update ordering
        migrations.AlterModelOptions(
            name="auditagent",
            options={"ordering": ["agent_id"]},
        ),
        # 7. Rename M2M to its final name
        migrations.RenameField(
            model_name="auditsuite",
            old_name="agents_new",
            new_name="agents",
        ),
        # 8. Sync email_html_body help_text to match current model (no DB effect)
        migrations.AlterField(
            model_name="auditsuite",
            name="email_html_body",
            field=models.TextField(
                blank=True,
                default="",
                help_text=(
                    "Full HTML email body with Django template syntax. "
                    "Available vars: repo_name, summary, run_status, suite_name, pdf_attached, site_url. "
                    "Blank uses the compiled MJML template from source."
                ),
            ),
        ),
        # 9. Update orchestrator_prompt help_text to reflect its new role as the user prompt (no DB effect)
        migrations.AlterField(
            model_name="auditsuite",
            name="orchestrator_prompt",
            field=models.TextField(
                blank=True,
                help_text=(
                    "Report/structure instructions given to the orchestrator as the user prompt — "
                    "what to do with the findings and how to structure the report. "
                    "Blank uses the built-in default. "
                    "The hard requirements, subagent-calling mechanics, and output form are "
                    "framework-owned and not editable here."
                ),
            ),
        ),
    ]
