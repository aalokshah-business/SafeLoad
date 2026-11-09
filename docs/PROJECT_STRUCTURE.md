# Project Structure

<!-- dlw:shared-project-layout -->
## Shared Project Layout

| Purpose | Standard Location |
|---|---|
| Final named build, export or delivery package | out/<platform>/<version>/; local tools may use out/releases/ |
| Living project guides | docs/ |
| Tracked prompt status and categorized actions | status/ |
| Shared test plans, fixtures and sanitized evidence | test/ and test/evidence/ |
| Portable environment variable names/placeholders | out/variables.env |
| Actual local environment values | ignored root .env or required native secret files |

Keep public/, app/, src/, dist/ or native static-export inputs unchanged. Final site packages belong under out/web/<version>/; an existing framework static-export root out/ keeps the layout required by its host.

Native/legacy test locations: `tests`. Do not move framework-owned source/test paths merely to change spelling.

Use names such as <project-slug>-<version>-<platform>-<variant>.<ext>, and keep source revision/checksum evidence with final output. The variable handoff file contains no actual values, credentials, signing keys or service-account data. Existing builders are preserved in this layout-only pass; collect their final artifacts under out/ before release.

Existing action/status files remain authoritative until deliberately migrated. See status/legacy.md for links. Empty prompt checklists do not establish completed work or verified tests.
<!-- /dlw:shared-project-layout -->
