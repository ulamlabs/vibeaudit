from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.admin.sites import site
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from audit.admin import AuditJobAdmin, AuditRunAdmin
from audit.models import SEND_STRANDED_SECONDS, AuditAgent, AuditJob, AuditRun, AuditSuite
from github_app.models import Installation

# AuditRunAdmin.Media references a real static asset (audit/md_preview.css).
# Under the whitenoise ManifestStaticFilesStorage used in production settings,
# rendering admin pages requires a collected staticfiles manifest, which the
# test run doesn't produce. Swap in the plain (non-manifest) storage just for
# rendering — see test_admin_button_rendering.py, which solves this the same way.
_NON_MANIFEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@pytest.fixture
def installation():
    return Installation.objects.create(
        installation_id=21, account_login="octocat", account_type="User"
    )


@pytest.fixture
def suite():
    s = AuditSuite.objects.create(name="S", is_default=True, model="claude-sonnet-4-6")
    agent, _ = AuditAgent.objects.get_or_create(
        agent_id="project_overview",
        defaults={"name": "PO", "description": "d", "prompt": "p"},
    )
    s.agents.add(agent)
    return s


def _run(installation, suite, report_state="", status=AuditRun.Status.COMPLETED):
    job = AuditJob.objects.create(
        installation=installation, repo_full_name="o/r", email="a@b.c"
    )
    job.state = AuditJob.State.READY
    job.save(update_fields=["state"])
    run = AuditRun.objects.create(job=job, suite=suite, status=status)
    if report_state:
        AuditRun.objects.filter(pk=run.pk).update(report_state=report_state)
        run.refresh_from_db()
    return run


@pytest.mark.django_db
def test_markdown_editable_only_while_awaiting_approval(installation, suite):
    admin_obj = AuditRunAdmin(AuditRun, site)
    awaiting = _run(installation, suite, AuditRun.ReportState.AWAITING_APPROVAL)
    assert "markdown" not in admin_obj.get_readonly_fields(None, awaiting)
    assert "markdown" in admin_obj.get_fields(None, awaiting)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "state",
    [
        AuditRun.ReportState.APPROVED,
        AuditRun.ReportState.SENT,
        AuditRun.ReportState.REJECTED,
        "",
    ],
)
def test_markdown_locked_outside_approval(installation, suite, state):
    admin_obj = AuditRunAdmin(AuditRun, site)
    run = _run(installation, suite, state)
    assert "markdown" in admin_obj.get_readonly_fields(None, run)


@pytest.mark.django_db
def test_report_state_is_always_readonly(installation, suite):
    admin_obj = AuditRunAdmin(AuditRun, site)
    run = _run(installation, suite, AuditRun.ReportState.AWAITING_APPROVAL)
    assert "report_state" in admin_obj.get_readonly_fields(None, run)


@pytest.mark.django_db
def test_approve_report_transitions_and_enqueues(installation, suite, rf):
    admin_obj = AuditRunAdmin(AuditRun, site)
    run = _run(installation, suite, AuditRun.ReportState.AWAITING_APPROVAL)
    request = rf.get("/")
    with (
        patch.object(AuditRunAdmin, "message_user"),
        patch.object(AuditRun, "enqueue_send") as enqueue,
    ):
        admin_obj.approve_report(request, object_id=run.pk)
    enqueue.assert_called_once()
    run.refresh_from_db()
    assert run.report_state == AuditRun.ReportState.APPROVED


@pytest.mark.django_db
def test_reject_report_transitions_and_sends_nothing(installation, suite, rf):
    admin_obj = AuditRunAdmin(AuditRun, site)
    run = _run(installation, suite, AuditRun.ReportState.AWAITING_APPROVAL)
    with (
        patch.object(AuditRunAdmin, "message_user"),
        patch.object(AuditRun, "enqueue_send") as enqueue,
    ):
        admin_obj.reject_report(rf.get("/"), object_id=run.pk)
    enqueue.assert_not_called()
    run.refresh_from_db()
    assert run.report_state == AuditRun.ReportState.REJECTED


@pytest.mark.django_db
def test_approve_report_on_already_approved_run_denies_permission(
    installation, suite, rf
):
    # Task 7 gates the action itself, not just the button: once approved, the
    # permission check now denies the call before the ValueError backstop runs.
    admin_obj = AuditRunAdmin(AuditRun, site)
    run = _run(installation, suite, AuditRun.ReportState.APPROVED)
    with (
        patch.object(AuditRunAdmin, "message_user"),
        patch.object(AuditRun, "enqueue_send") as enqueue,
        pytest.raises(PermissionDenied),
    ):
        admin_obj.approve_report(rf.get("/"), object_id=run.pk)
    enqueue.assert_not_called()


@pytest.mark.django_db
def test_resend_report_requeues_an_approved_report(installation, suite, rf):
    admin_obj = AuditRunAdmin(AuditRun, site)
    run = _run(installation, suite, AuditRun.ReportState.APPROVED)
    with (
        patch.object(AuditRunAdmin, "message_user"),
        patch.object(AuditRun, "enqueue_send") as enqueue,
    ):
        admin_obj.resend_report(rf.get("/"), object_id=run.pk)
    enqueue.assert_called_once()


@pytest.mark.django_db
def test_resend_report_on_a_sent_report_denies_permission(installation, suite, rf):
    # Same reasoning as the approve case: 'sent' is terminal, so the
    # permission check denies the call before the body's own guard runs.
    admin_obj = AuditRunAdmin(AuditRun, site)
    run = _run(installation, suite, AuditRun.ReportState.SENT)
    with (
        patch.object(AuditRunAdmin, "message_user"),
        patch.object(AuditRun, "enqueue_send") as enqueue,
        pytest.raises(PermissionDenied),
    ):
        admin_obj.resend_report(rf.get("/"), object_id=run.pk)
    enqueue.assert_not_called()


def _job(installation, state):
    job = AuditJob.objects.create(
        installation=installation, repo_full_name="o/r", email="a@b.c"
    )
    AuditJob.objects.filter(pk=job.pk).update(state=state)
    job.refresh_from_db()
    return job


@pytest.mark.django_db
@pytest.mark.parametrize(
    "state,approve,reject,mark_failed,cleanup",
    [
        (AuditJob.State.PENDING, False, False, False, True),
        (AuditJob.State.CLONING, False, False, True, False),
        (AuditJob.State.AWAITING_APPROVAL, True, True, False, True),
        (AuditJob.State.READY, False, False, False, True),
        (AuditJob.State.REJECTED, False, False, False, True),
        (AuditJob.State.FAILED, False, False, False, True),
        (AuditJob.State.CLOSED, False, False, False, False),
    ],
)
def test_job_button_visibility_follows_the_state_machine(
    installation, rf, state, approve, reject, mark_failed, cleanup
):
    admin_obj = AuditJobAdmin(AuditJob, site)
    job = _job(installation, state)
    request = rf.get("/")
    assert admin_obj.has_approve_job_permission(request, job.pk) is approve
    assert admin_obj.has_reject_job_permission(request, job.pk) is reject
    assert admin_obj.has_mark_failed_permission(request, job.pk) is mark_failed
    assert admin_obj.has_cleanup_job_permission(request, job.pk) is cleanup


@pytest.mark.django_db
def test_job_permissions_are_false_for_a_missing_object(installation, rf):
    admin_obj = AuditJobAdmin(AuditJob, site)
    assert admin_obj.has_approve_job_permission(rf.get("/"), 999999) is False


@pytest.mark.django_db
@pytest.mark.parametrize(
    "report_state,approve,reject,resend",
    [
        (AuditRun.ReportState.AWAITING_APPROVAL, True, True, False),
        (AuditRun.ReportState.APPROVED, False, False, True),
        (AuditRun.ReportState.SENT, False, False, False),
        (AuditRun.ReportState.REJECTED, False, False, False),
        ("", False, False, False),
    ],
)
def test_report_button_visibility(
    installation, suite, rf, report_state, approve, reject, resend
):
    admin_obj = AuditRunAdmin(AuditRun, site)
    run = _run(installation, suite, report_state)
    request = rf.get("/")
    assert admin_obj.has_approve_report_permission(request, run.pk) is approve
    assert admin_obj.has_reject_report_permission(request, run.pk) is reject
    assert admin_obj.has_resend_report_permission(request, run.pk) is resend


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status,visible",
    [
        (AuditRun.Status.RUNNING, True),
        (AuditRun.Status.PENDING, False),
        (AuditRun.Status.COMPLETED, False),
        (AuditRun.Status.FAILED, False),
    ],
)
def test_terminate_button_visible_only_while_running(
    installation, suite, rf, status, visible
):
    admin_obj = AuditRunAdmin(AuditRun, site)
    run = _run(installation, suite, status=status)
    assert admin_obj.has_terminate_run_permission(rf.get("/"), run.pk) is visible


@pytest.mark.django_db
def test_ready_job_with_nothing_outstanding_needs_attention(installation, suite):
    """A rejected-only job holds its clone until staff act — make that visible."""
    admin_obj = AuditJobAdmin(AuditJob, site)
    run = _run(installation, suite, AuditRun.ReportState.REJECTED)
    assert admin_obj.needs_attention(run.job) is True


@pytest.mark.django_db
def test_ready_job_awaiting_approval_does_not_need_attention(installation, suite):
    admin_obj = AuditJobAdmin(AuditJob, site)
    run = _run(installation, suite, AuditRun.ReportState.AWAITING_APPROVAL)
    assert admin_obj.needs_attention(run.job) is False


@pytest.mark.django_db
def test_has_reset_stranded_send_permission_false_until_stranded(
    installation, suite
):
    admin_obj = AuditRunAdmin(AuditRun, site)
    run = _run(installation, suite, AuditRun.ReportState.SENDING)
    run.sending_since = timezone.now()
    run.save(update_fields=["sending_since"])
    assert admin_obj.has_reset_stranded_send_permission(None, run.pk) is False


@pytest.mark.django_db
def test_has_reset_stranded_send_permission_true_once_stranded(installation, suite):
    admin_obj = AuditRunAdmin(AuditRun, site)
    run = _run(installation, suite, AuditRun.ReportState.SENDING)
    run.sending_since = timezone.now() - timedelta(seconds=SEND_STRANDED_SECONDS + 1)
    run.save(update_fields=["sending_since"])
    assert admin_obj.has_reset_stranded_send_permission(None, run.pk) is True


@pytest.mark.django_db
def test_has_reset_stranded_send_permission_false_for_missing_object():
    admin_obj = AuditRunAdmin(AuditRun, site)
    assert admin_obj.has_reset_stranded_send_permission(None, 999999) is False


@pytest.mark.django_db
def test_run_needs_attention_when_send_is_stranded(installation, suite):
    admin_obj = AuditRunAdmin(AuditRun, site)
    run = _run(installation, suite, AuditRun.ReportState.SENDING)
    run.sending_since = timezone.now() - timedelta(seconds=SEND_STRANDED_SECONDS + 1)
    run.save(update_fields=["sending_since"])
    assert admin_obj.needs_attention(run) is True


@pytest.mark.django_db
def test_run_needs_attention_when_send_failed(installation, suite):
    """A failed-send rollback (approved with sending_since still set) must
    surface in the admin exactly like the other stuck states."""
    admin_obj = AuditRunAdmin(AuditRun, site)
    run = _run(installation, suite, AuditRun.ReportState.SENDING)
    run.sending_since = timezone.now()
    run.save(update_fields=["sending_since"])
    run.transition_report_to(AuditRun.ReportState.APPROVED)
    assert admin_obj.needs_attention(run) is True


@pytest.mark.django_db
def test_run_fresh_approval_does_not_need_attention(installation, suite):
    admin_obj = AuditRunAdmin(AuditRun, site)
    run = _run(installation, suite, AuditRun.ReportState.APPROVED)
    assert admin_obj.needs_attention(run) is False


def _make_ready_jobs(installation, n):
    """READY jobs with no runs: needs_attention must consult
    has_outstanding_runs for every one of them (is_overdue never short-
    circuits), which is exactly the path that used to cost one EXISTS
    query per row."""
    AuditJob.objects.all().delete()
    for i in range(n):
        job = AuditJob.objects.create(
            installation=installation, repo_full_name=f"o/r{i}", email="a@b.c"
        )
        AuditJob.objects.filter(pk=job.pk).update(state=AuditJob.State.READY)


@pytest.mark.django_db
def test_changelist_query_count_does_not_scale_with_row_count(installation):
    """Proves the N+1 is gone: the changelist must issue the same number of
    queries for 1 job as for 5 -- a test that only asserts "some number" would
    not catch a regression back to a per-row EXISTS query."""
    admin_user = User.objects.create_superuser(
        username="root", email="root@example.com", password="pw"
    )
    client = Client()
    client.force_login(admin_user)
    url = reverse("admin:audit_auditjob_changelist")

    _make_ready_jobs(installation, 1)
    with override_settings(STORAGES=_NON_MANIFEST_STORAGES):
        with CaptureQueriesContext(connection) as ctx_one:
            response = client.get(url)
        assert response.status_code == 200
    count_one = len(ctx_one.captured_queries)

    _make_ready_jobs(installation, 5)
    with override_settings(STORAGES=_NON_MANIFEST_STORAGES):
        with CaptureQueriesContext(connection) as ctx_five:
            response = client.get(url)
        assert response.status_code == 200
    count_five = len(ctx_five.captured_queries)

    assert count_one == count_five, (
        f"query count scaled with row count: {count_one} for 1 job vs "
        f"{count_five} for 5 jobs"
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "state, with_outstanding_run",
    [
        (AuditJob.State.READY, True),
        (AuditJob.State.READY, False),
        (AuditJob.State.CLOSED, False),
    ],
)
def test_needs_attention_agrees_between_annotated_and_plain_instance(
    installation, suite, state, with_outstanding_run
):
    """The annotated changelist path and the property fallback must never
    disagree -- maybe_cleanup_sources decides whether to delete a customer's
    cloned sources off has_outstanding_runs, so a drift here would mean the
    admin shows staff a different answer than what actually gates cleanup."""
    job = AuditJob.objects.create(
        installation=installation, repo_full_name="o/r", email="a@b.c"
    )
    AuditJob.objects.filter(pk=job.pk).update(state=state)
    if with_outstanding_run:
        AuditRun.objects.create(
            job=job, suite=suite, status=AuditRun.Status.PENDING
        )

    admin_obj = AuditJobAdmin(AuditJob, site)

    plain = AuditJob.objects.get(pk=job.pk)
    assert getattr(plain, "_has_outstanding", None) is None
    plain_result = admin_obj.needs_attention(plain)

    annotated = admin_obj.get_queryset(None).get(pk=job.pk)
    assert getattr(annotated, "_has_outstanding", None) is not None
    annotated_result = admin_obj.needs_attention(annotated)

    assert plain_result == annotated_result
