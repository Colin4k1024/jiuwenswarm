import { GENERATIVE_UI_CSP } from './csp';
import { HOST_BRIDGE_CLIENT_SCRIPT } from './hostBridgeClient';
import type { GenerativeUIArtifactPayload } from './types';

export function buildSandboxDocument(payload: GenerativeUIArtifactPayload): string {
  const {
    artifact_id,
    css,
    html,
    js_functions,
    js_expressions,
  } = payload;

  const hostBridgeScript = HOST_BRIDGE_CLIENT_SCRIPT.replace('__ARTIFACT_ID__', artifact_id);

  return `<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <meta http-equiv="Content-Security-Policy" content="${GENERATIVE_UI_CSP}">
    <style>${css}</style>
  </head>
  <body>${html}</body>
  <script>${hostBridgeScript}</script>
  <script>${js_functions}</script>
  <script>${js_expressions}</script>
</html>`;
}
