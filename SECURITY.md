# Security and credentials

Codex Duo is designed to keep account state and provider credentials on the
local Mac. The repository intentionally contains no login sessions, API keys,
quota caches, handoff packages, or service-provider credentials.

- Claude Code provider keys are saved in the macOS Keychain.
- Codex login state remains in each local `CODEX_HOME` directory.
- Local usage, history, and handoff files are excluded by `.gitignore`.
- Copy `dist/` only as a build output; never commit a personal app bundle with
  local resources or caches.

If a credential is ever pasted into an issue, pull request, screenshot, or
commit, revoke it at the provider immediately and remove it from Git history.
Do not report the credential itself in an issue.
