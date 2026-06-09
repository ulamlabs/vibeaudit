# Rules
* Use `uv*` for all Python-related commands. **Without `uv run`, dependencies will not be found.**
	- Run tests: `uv run pytest ...`
	- Run management commands: `uv run python manage.py ...`
	- Run scripts: `uv run python script.py`
	Use `uvx` for command execution, and use `uv` script mode (with embedded dependencies) for one-off scripts.
* If you want to browse library capabilities, first check backend/.venv 
* For backend tests, write `pytest`-style tests instead of built-in unittest-style tests.
* Configure `settings.py` according to 12-factor principles, using environment variables.
	The project should still run locally with no env vars set.
	For example: if email config is missing, print emails to the backend terminal; if present, use the configured email backend.
	If Redis is not configured, use DB-backed sessions and no caching; if Redis is configured, use it.

* when updating Dockerfiles, remember to keep dev Dockerfiles in sync with production one