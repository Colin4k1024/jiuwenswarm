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

  const safeCss = escapeClosingSequences(css, 'style');
  const safeHtml = escapeClosingSequences(html, 'script');
  const safeJsFunctions = escapeClosingSequences(js_functions, 'script');
  const safeJsExpressions = escapeClosingSequences(js_expressions, 'script');

  return `<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <meta http-equiv="Content-Security-Policy" content="${GENERATIVE_UI_CSP}">
    <style>${safeCss}</style>
  </head>
  <body>${safeHtml}</body>
  <script>${hostBridgeScript}</script>
  <script>${safeJsFunctions}</script>
  <script>${safeJsExpressions}</script>
</html>`;
}

