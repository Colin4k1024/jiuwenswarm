"""Generative UI tool prompt instructions.

Provides usage instructions for the ``emit_generative_ui`` tool, coexisting with A2UI.
"""


def build_generative_ui_tool_instruction() -> str:
    """Build the generative_ui tool prompt instruction text.

    Returns:
        Complete instruction covering the 6-parameter contract, security
        constraints, division of labor with A2UI, and client event callback.
    """
    return """## Generative UI Tool: emit_generative_ui

When you need to render arbitrary interactive UI that the A2UI controlled component catalog cannot express, call the `emit_generative_ui` tool.

### 6-Parameter Fixed Contract (order immutable)

1. `initial_height` (int): iframe initial height (px)
2. `placeholder_messages` (list[str]): placeholder text shown while iframe loads
3. `css` (str): CSS text injected into `<style>`
4. `html` (str): HTML text injected into `<body>`
5. `js_functions` (str): named function definitions (as client-side script library)
6. `js_expressions` (str): top-level expressions to execute

### Security Constraints (strict sandbox)

- iframe `sandbox="allow-scripts"` (no `allow-same-origin`), cross-origin isolation
- CSP `default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'`, no network / no forms / no localStorage
- Do not generate code that attempts `fetch()` / `XMLHttpRequest` / `localStorage` / `sessionStorage` / `indexedDB` — it will be blocked by CSP
- Each call generates an independent `artifact_id`; no cross-call patch support

### Division of Labor with A2UI

- **Use A2UI**: controlled forms, text, selectors, sliders, date inputs, and other structured components
- **Use Generative UI**: charts, maps, 3D, calculators, custom visualizations, and other arbitrary interactive artifacts that the A2UI catalog cannot cover

### Client Event Callback (Future)

Inside the iframe, `window.hostBridge.emit(event_type, payload)` is reserved for sending user interactions back to the agent:
- **Current version**: static rendering only; the callback bridge is not yet wired to the backend.
- **Future**: the parent page will listen to postMessage, validate the source, and package it as a `generative_ui.client_event` user message back to the backend.
- Do not generate artifacts that depend on the callback for core functionality until the callback bridge is implemented.
"""
