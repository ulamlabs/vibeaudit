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
| `toc` | `SafeString` | Pre-built `<nav class="toc">…</nav>` with page-number leaders. Drop in as-is. |
| `toc_items` | `list[tuple[int, str, str]]` | Raw ToC data if you want to build your own: `(level, heading_text, slug)`. Level is 1–4. |
| `generated_at` | `datetime` | UTC timestamp. Use with `{{ generated_at\|date:"Y-m-d H:i" }}`. |

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

  {{ toc }}

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

### failure_email — audit failed

| Variable | Type | Description |
|---|---|---|
| `repo_name` | `str` | `job.repo_full_name` |
| `run_id` | `int` | Primary key of the failed run. |
| `site_url` | `str` | Site base URL, or empty string. |

### new_submission_email — awaiting approval

Sent to staff members in the `audit_notifications` group.

| Variable | Type | Description |
|---|---|---|
| `repo_name` | `str` | `job.repo_full_name` |
| `submitter_email` | `str` | Email of the person who submitted the job. |
| `job_id` | `int` | Primary key of the `AuditJob`. |
| `site_url` | `str` | Site base URL. Admin review link: `{{ site_url }}/admin/audit/auditjob/{{ job_id }}/change/` |

### Django template syntax in MJML

`{{ variables }}` inside `<mj-text>` content work as-is — MJML passes them through untouched.

Django block tags (`{% if %}`, `{% for %}`, etc.) must be wrapped in `<mj-raw>` so MJML doesn't treat them as invalid markup:

```mjml
<mj-raw>{% if site_url %}</mj-raw>
<mj-button href="{{ site_url }}/admin/">Open admin</mj-button>
<mj-raw>{% endif %}</mj-raw>
```

Django template variables are HTML-escaped by default. Use the `safe` filter only on content you control — agent-produced text should stay escaped.
