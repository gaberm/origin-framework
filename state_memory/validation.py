import re


def validate_run_id(run_id: str) -> None:
    if not re.match(r"^[a-z_][a-z0-9_]{0,62}$", run_id):
        raise ValueError(
            f"run_id '{run_id}' is not a valid PostgreSQL schema name. "
            f"Use lowercase letters, digits, and underscores only, "
            f"start with a letter or underscore, and keep it under 63 characters."
        )
