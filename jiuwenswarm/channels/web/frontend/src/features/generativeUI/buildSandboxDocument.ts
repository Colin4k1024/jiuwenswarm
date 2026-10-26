import { GENERATIVE_UI_CSP } from './csp';
import { HOST_BRIDGE_CLIENT_SCRIPT } from './hostBridgeClient';
import type { GenerativeUIArtifactPayload } from './types';

function escapeClosingSequences(source: string, tag: 'style' | 'script'): string {
  const closing = `</${tag}`;
  const escaped = `<\\/${tag}`;
  return source.replace(new RegExp(closing, 'gi'), escaped);
}

export function buildSandboxDocument(payload: GenerativeUIArtifactPayload): string {
  const {
    artifact_id,
    css,
    html,
    js_functions,
    js_expressions,
  } = payload;

  const hostBridgeScript = HOST_BRIDGE_CLIENT_SCRIPT.replace('__ARTIFACT_ID__', artifact_id);

  // Only escape closing sequences in raw-text contexts (style/script).
  // html is inserted into <body> and parsed as HTML, not raw text, so
  // escaping </script> would corrupt valid payloads like
  // <textarea></script></textarea> or inline <script>...</script> blocks.
  const safeCss = escapeClosingSequences(css, 'style');
  const safeJsFunctions = escapeClosingSequences(js_functions, 'script');
  const safeJsExpressions = escapeClosingSequences(js_expressions, 'script');

  return `<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <meta http-equiv="Content-Security-Policy" content="${GENERATIVE_UI_CSP}">
    <style>${safeCss}</style>
  </head>
  <body>${html}</body>
  <script>${hostBridgeScript}</script>
  <script>${safeJsFunctions}</script>
  <script>${safeJsExpressions}</script>
</html>`;
}

