"""AI agent apply: a second, opt-in application path alongside app.auto_apply.

Where app.auto_apply drives a headless Playwright browser through hand-coded
per-ATS selectors (Greenhouse, Lever), this package spawns the `claude` CLI
as an autonomous agent that drives a real, visible Chrome window through
Playwright MCP tools — works on any site generically, at the cost of a real
API call per job and a slower, less predictable run.
"""
