# Playwright browser session management
#
# Responsibilities:
#   - Initialize persistent Chromium profile (data/chromium_profile)
#   - Launch browser on first use, reuse session for subsequent apps (30/day)
#   - Handle session timeout: detect logout page, pause, ask user to re-login
#   - Navigate to job application URL
#   - Provide page context to autofill module
#   - Take screenshot for gap report review (optional)
#
# Classes:
#   - BrowserManager: Context manager for persistent browser session
#
# Methods:
#   - launch_browser(): Start Chromium with persistent profile
#   - goto(url): Navigate to URL, detect if redirected to login
#   - handle_timeout(): Detect logout, pause, wait for user to re-login
#   - get_page(): Return current page object for autofill operations
#   - close(): Close browser session
