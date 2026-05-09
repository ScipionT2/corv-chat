"""
Vision prompt templates and selection logic for EP Agent.

Provides context-aware prompts that dramatically improve analysis quality
by tailoring instructions to what's actually on screen.
"""

from __future__ import annotations

# ─── Prompt Templates ─────────────────────────────────────────────────

GENERAL_ANALYSIS_PROMPT: str = (
    "Look at this screen and provide 2-3 specific, actionable suggestions "
    "for what the user could do next. Focus on productivity improvements, "
    "errors to fix, or things that seem incomplete. Be direct and specific — "
    "reference actual text/elements you see on screen."
)

CONTEXTUAL_PROMPT_TEMPLATE: str = (
    "The user is looking at their screen and asking: '{question}'. "
    "Analyze the screenshot and give a direct, helpful answer. "
    "Reference specific things you see on screen."
)

CODE_ANALYSIS_PROMPT: str = (
    "This appears to be a code editor. Identify: what language, what file, "
    "any visible bugs/issues, suggestions for improvement. Be specific about "
    "line numbers or function names you can see."
)

BROWSER_ANALYSIS_PROMPT: str = (
    "This shows a web browser. Summarize the page content, note any important "
    "information, and suggest relevant actions the user could take."
)

FILE_MANAGER_PROMPT: str = (
    "This shows a file manager. Describe the visible files/folders, note any "
    "organization issues, and suggest actions like cleanup or next steps."
)

TERMINAL_PROMPT: str = (
    "This shows a terminal/command line. Read the visible commands and output. "
    "Identify any errors, suggest fixes, or recommend the logical next command "
    "to run. Be specific about what you see."
)

WRITING_PROMPT: str = (
    "This shows a document or text editor. Summarize what's being written, "
    "note any spelling/grammar issues you can spot, and suggest improvements "
    "or next steps for the document."
)

# ─── App-to-Prompt Mapping ────────────────────────────────────────────

_CODE_APPS = frozenset({
    "code", "visual studio code", "xcode", "pycharm", "intellij idea",
    "webstorm", "sublime text", "atom", "neovim", "vim", "emacs",
    "android studio", "fleet", "zed", "cursor",
})

_BROWSER_APPS = frozenset({
    "safari", "google chrome", "firefox", "arc", "brave browser",
    "microsoft edge", "opera", "vivaldi", "chromium",
})

_FILE_MANAGER_APPS = frozenset({
    "finder", "path finder", "forklift",
})

_TERMINAL_APPS = frozenset({
    "terminal", "iterm2", "iterm", "warp", "hyper", "alacritty",
    "kitty", "wezterm",
})

_WRITING_APPS = frozenset({
    "pages", "microsoft word", "google docs", "notion", "obsidian",
    "bear", "ulysses", "ia writer", "textedit", "typora",
})


def select_prompt(context: str = "general") -> str:
    """Pick the right prompt based on a context string.

    Parameters
    ----------
    context : str
        One of: "general", "code", "browser", "files", "terminal", "writing"
    """
    mapping = {
        "general": GENERAL_ANALYSIS_PROMPT,
        "code": CODE_ANALYSIS_PROMPT,
        "browser": BROWSER_ANALYSIS_PROMPT,
        "files": FILE_MANAGER_PROMPT,
        "terminal": TERMINAL_PROMPT,
        "writing": WRITING_PROMPT,
    }
    return mapping.get(context, GENERAL_ANALYSIS_PROMPT)


def select_prompt_for_app(app_name: str) -> str:
    """Select the best prompt based on the active application name.

    Parameters
    ----------
    app_name : str
        The frontmost application name (e.g., "Visual Studio Code").

    Returns
    -------
    str
        The appropriate analysis prompt for this app type.
    """
    if not app_name:
        return GENERAL_ANALYSIS_PROMPT

    normalized = app_name.lower().strip()

    if normalized in _CODE_APPS:
        return CODE_ANALYSIS_PROMPT
    if normalized in _BROWSER_APPS:
        return BROWSER_ANALYSIS_PROMPT
    if normalized in _FILE_MANAGER_APPS:
        return FILE_MANAGER_PROMPT
    if normalized in _TERMINAL_APPS:
        return TERMINAL_PROMPT
    if normalized in _WRITING_APPS:
        return WRITING_PROMPT

    return GENERAL_ANALYSIS_PROMPT


def build_contextual_prompt(question: str) -> str:
    """Build a contextual prompt wrapping the user's question.

    Parameters
    ----------
    question : str
        The user's question about what's on screen.
    """
    return CONTEXTUAL_PROMPT_TEMPLATE.format(question=question)


# ─── Suggestion Categories ────────────────────────────────────────────

_CATEGORIES = {
    "💻 Code": [
        "code", "function", "variable", "class", "method", "import", "syntax",
        "compile", "build", "debug", "refactor", "lint", "def ", "return",
        "error", "exception", "traceback", "line ",
    ],
    "🌐 Web": [
        "browser", "webpage", "url", "link", "http", "website", "tab",
        "bookmark", "search", "download", "page", "click",
    ],
    "📁 Files": [
        "file", "folder", "directory", "rename", "move", "copy", "delete",
        "organize", "finder", "path", "extension",
    ],
    "🐛 Bug": [
        "bug", "error", "crash", "fail", "broken", "issue", "fix",
        "traceback", "exception", "warning", "undefined", "null",
    ],
    "💡 Tip": [
        "shortcut", "tip", "faster", "efficient", "instead", "better",
        "recommend", "suggest", "try", "consider", "optimization",
    ],
    "📝 Writing": [
        "document", "text", "write", "spell", "grammar", "paragraph",
        "sentence", "draft", "edit", "proofread", "word",
    ],
    "⚡ Productivity": [
        "productivity", "workflow", "automate", "schedule", "task",
        "time", "focus", "organize", "priority", "efficiency",
    ],
}

# App-based category overrides
_APP_CATEGORY_MAP = {
    "code": "💻 Code",
    "browser": "🌐 Web",
    "files": "📁 Files",
    "terminal": "💻 Code",
    "writing": "📝 Writing",
}


def categorize_suggestion(analysis_text: str, app_name: str = "") -> str:
    """Categorize an analysis result based on content and active app.

    Parameters
    ----------
    analysis_text : str
        The vision model's analysis output.
    app_name : str
        The frontmost application name.

    Returns
    -------
    str
        Emoji + category name (e.g., "💻 Code").
    """
    # First try app-based categorization
    if app_name:
        normalized = app_name.lower().strip()
        if normalized in _CODE_APPS:
            return "💻 Code"
        if normalized in _BROWSER_APPS:
            return "🌐 Web"
        if normalized in _FILE_MANAGER_APPS:
            return "📁 Files"
        if normalized in _TERMINAL_APPS:
            return "💻 Code"
        if normalized in _WRITING_APPS:
            return "📝 Writing"

    # Fall back to keyword matching in analysis text
    text_lower = analysis_text.lower()
    scores: dict[str, int] = {}

    for category, keywords in _CATEGORIES.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[category] = score

    if scores:
        return max(scores, key=scores.get)

    return "⚡ Productivity"
