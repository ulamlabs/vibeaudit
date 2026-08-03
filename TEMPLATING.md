# Template Authoring Guide

Reference for writing custom templates for VibeAudit's PDF reports and emails.

---

## Report Templates (PDF)

Templates are standard Django HTML templates rendered to PDF by **WeasyPrint**. The audit findings are produced as CommonMark Markdown, converted to sanitised HTML, and injected into the template.

### Template variables

| Variable | Type | Description |
|---|---|---|
| `run` | `AuditRun` | The run object (see attributes below). |
| `report_html` | `SafeString` | Audit body as rendered HTML. Output with `{{ report_html }}` — already marked safe. |
| `toc` | `list[tuple[int, str, str]]` | ToC data: `(level, heading_text, slug)`. Level is 1–4. Iterate with `{% for level, text, slug in toc %}`. |
| `generated_at` | `datetime` | UTC timestamp. Use with `{{ generated_at|date:"Y-m-d H:i" }}`. |

#### Useful `run` attributes

| Attribute | Description |
|---|---|
| `run.job.repo_full_name` | Repository identifier, e.g. `owner/repo`. |
| `run.suite.name` | Name of the audit suite. |
| `run.status` | e.g. `completed` |
| `run.summary` | Short plain-text summary from the orchestrator. |

### WeasyPrint-specific CSS

The bundled template uses these patterns specific to WeasyPrint's print engine:

```css
/* Fill ToC entries with dots and a page number */
.toc a::after {
  content: leader('.') target-counter(attr(href), page);
  float: right;
}

/* Force a page break before a section */
.chapter { page-break-before: always; }
```

`target-counter` resolves the page number of the element whose `id` matches the link's `href` — this is how ToC page numbers are populated automatically. See the [WeasyPrint docs](https://doc.courtbouillon.org/weasyprint/stable/) for the full range of supported CSS features.

### Minimal skeleton

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <style>/* your styles */</style>
</head>
<body>
  <h1>{{ run.job.repo_full_name }}</h1>
  <p>{{ generated_at|date:"Y-m-d" }}</p>

  {% if toc %}
  <nav class="toc">
    <h2>Contents</h2>
    <ul>
      {% for level, text, slug in toc %}
      <li class="toc-h{{ level }}"><a href="#{{ slug }}">{{ text }}</a></li>
      {% endfor %}
    </ul>
  </nav>
  {% endif %}

  {{ report_html }}
</body>
</html>
```

---

## Email Templates

Email templates use **MJML** syntax compiled to HTML. Write them as MJML documents — the source files live in `backend/audit/templates/email/src/`.

### report_email — audit complete

| Variable | Type | Description |
|---|---|---|
| `repo_name` | `str` | `job.repo_full_name` |
| `summary` | `str` | Short plain-text summary from the orchestrator. |
| `run_status` | `str` | e.g. `completed` |
| `suite_name` | `str` | Name of the suite that ran. |
| `pdf_attached` | `bool` | `True` when the PDF was small enough to attach (≤ 10 MB). |
| `site_url` | `str` | Site base URL, or empty string. |

### failure_email — clone failed

Sent to staff members in the `audit_notifications` group when cloning fails,
before any `AuditRun` exists.

| Variable | Type | Description |
|---|---|---|
| `repo_name` | `str` | `job.repo_full_name` |
| `run_id` | `str` | `f"job-{job.pk}"` — no run exists yet at the clone stage; this identifies the job instead. |
| `reason` | `str` | Exception message from the failed clone. |
| `site_url` | `str` | Site base URL, or empty string. |

### new_submission_email — awaiting approval

Sent to staff members in the `audit_notifications` group.

| Variable | Type | Description |
|---|---|---|
| `repo_name` | `str` | `job.repo_full_name` |
| `submitter_email` | `str` | Email of the person who submitted the job. |
| `job_id` | `int` | Primary key of the `AuditJob`. |
| `site_url` | `str` | Site base URL. Admin review link: `{{ site_url }}/admin/audit/auditjob/{{ job_id }}/change/` |

### report_approval_email — report awaiting approval

Sent to staff members in the `audit_notifications` group when a run completes
and its report is held at `awaiting_approval`. Not customisable per suite —
unlike `report_email.html`, there is no `AuditSuite.email_html_body` override.

| Variable | Type | Description |
|---|---|---|
| `repo_name` | `str` | `job.repo_full_name` |
| `suite_name` | `str` | `run.suite.name` |
| `summary` | `str` | `run.summary` |
| `cost_usd` | `Decimal \| None` | `run.cost_usd`, `None` when not tracked. |
| `run_id` | `int` | `run.pk`. Admin review link: `{{ site_url }}/admin/audit/auditrun/{{ run_id }}/change/` |
| `site_url` | `str` | Site base URL, or empty string. |

### run_failure_email — run failed

Sent to staff members in the `audit_notifications` group when a run's status
becomes `failed`. Distinct from `failure_email.html`, which covers a failure at
the earlier clone stage. Not customisable per suite.

| Variable | Type | Description |
|---|---|---|
| `repo_name` | `str` | `job.repo_full_name` |
| `suite_name` | `str` | `run.suite.name` |
| `submitter_email` | `str` | `job.email` |
| `reason` | `str` | `run.error` |
| `run_id` | `int` | `run.pk` |
| `site_url` | `str` | Site base URL, or empty string. |

### Django template syntax in MJML

`{{ variables }}` inside `<mj-text>` content work as-is — MJML passes them through untouched.

Django block tags (`{% if %}`, `{% for %}`, etc.) must be wrapped in `<mj-raw>` so MJML doesn't treat them as invalid markup:

```mjml
<mj-raw>{% if site_url %}</mj-raw>
<mj-button href="{{ site_url }}/admin/">Open admin</mj-button>
<mj-raw>{% endif %}</mj-raw>
```

Django template variables are HTML-escaped by default. Use the `safe` filter only on content you control — agent-produced text should stay escaped.
