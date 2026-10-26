"""Generative UI 工具 prompt 指令.

为 agent 提供 ``emit_generative_ui`` 工具的使用说明, 与 A2UI 并存.
"""


def build_generative_ui_tool_instruction() -> str:
    """构建 generative_ui 工具的 prompt 指令文本.

    Returns:
        包含 6 参数契约 / 安全约束 / 与 A2UI 分工 / 客户端事件回传 的完整指令.
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

### Client Event Callback

Inside the iframe, you can call `window.hostBridge.emit(event_type, payload)` to send user interactions back to the agent:
- The parent page listens to postMessage, validates the source, and packages it as a `generative_ui.client_event` user message back to the backend
- The backend converts the event into a prompt, triggering a new agent turn
- Suitable for scenarios where user interaction should update the artifact (e.g., calculators, configuration panels)
"""
