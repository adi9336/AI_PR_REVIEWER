"""Vulnerable example — used ONLY by the PR review demo (do not copy).

This is the exact SQL-injection pattern the security agent is trained to
catch. It is NOT part of the application.
"""


def get_user(username: str) -> str:
    # VULNERABLE: string-concatenated SQL (SQL injection)
    return db_query("SELECT * FROM users WHERE username = '" + username + "'")


def db_query(sql: str) -> str:
    return f"<result of: {sql}>"
