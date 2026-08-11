"""Saved dashboards: tiles that keep their query, not their rows. Three invariants — the rows
are the truth and tool events carry intent only; a refresh is atomic; a refresh re-executes
rather than rewrites, so a moving window is a property of the stored query.
"""
