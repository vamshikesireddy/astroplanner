# backend/github.py
"""GitHub integration helpers — no Streamlit dependency."""

try:
    from github import Github
except ImportError:
    Github = None  # PyGithub optional


def create_issue(token, repo_name, title, body):
    """Create a GitHub Issue.

    Args:
        token:     GitHub personal access token string.
        repo_name: Full repo name e.g. "owner/repo".
        title:     Issue title.
        body:      Issue body (markdown).

    Does nothing if token/repo_name/Github are falsy.
    Raises RuntimeError if the API call fails.
    """
    if not (token and repo_name and Github):
        return
    g = Github(token)
    repo = g.get_repo(repo_name)
    me = g.get_user()
    repo.create_issue(title=title, body=body, assignee=me.login)
