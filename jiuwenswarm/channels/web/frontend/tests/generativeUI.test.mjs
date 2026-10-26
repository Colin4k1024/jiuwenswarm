import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { createRoot } from 'react-dom/client';
import { JSDOM, VirtualConsole } from 'jsdom';

import { GenerativeUIArtifact } from '../node_modules/.cache/generative-ui/GenerativeUIArtifact.js';
import {
  GENERATIVE_UI_CSP,
  GENERATIVE_UI_SANDBOX,
  buildSandboxDocument,
  HOST_BRIDGE_CLIENT_SCRIPT,
} from '../node_modules/.cache/generative-ui/index.js';

function installDom() {
  const dom = new JSDOM('<!doctype html><div id="root"></div>', {
    virtualConsole: new VirtualConsole(),
    pretendToBeVisual: true,
  });
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  globalThis.HTMLElement = dom.window.HTMLElement;
  globalThis.HTMLIFrameElement = dom.window.HTMLIFrameElement;
  globalThis.requestAnimationFrame = (cb) => setTimeout(cb, 0);
  globalThis.cancelAnimationFrame = (id) => clearTimeout(id);
  return dom;
}

function cleanupDom(dom) {
  dom.window.close();
  delete globalThis.window;
  delete globalThis.document;
  delete globalThis.HTMLElement;
  delete globalThis.HTMLIFrameElement;
}

const basePayload = {
  artifact_id: 'test-artifact-1',
  initial_height: 320,
  placeholder_messages: ['Loading...'],
  css: 'body { margin: 0; }',
  html: '<div id="app">Hello</div>',
  js_functions: 'function add(a, b) { return a + b; }',
  js_expressions: 'document.getElementById("app").textContent = add(1, 2);',
  title: 'Calculator',
  description: 'A simple calculator',
};

test('CSP blocks network, forms, and localStorage', () => {
  assert.ok(GENERATIVE_UI_CSP.includes("default-src 'none'"));
  assert.ok(GENERATIVE_UI_CSP.includes("script-src 'unsafe-inline'"));
  assert.ok(GENERATIVE_UI_CSP.includes("style-src 'unsafe-inline'"));
  assert.ok(GENERATIVE_UI_CSP.includes("form-action 'none'"));
  assert.ok(GENERATIVE_UI_CSP.includes("base-uri 'none'"));
  assert.equal(GENERATIVE_UI_SANDBOX, 'allow-scripts');
  assert.ok(!GENERATIVE_UI_SANDBOX.includes('allow-same-origin'));
});

test('buildSandboxDocument assembles document in correct order', () => {
  const doc = buildSandboxDocument(basePayload);
  // CSP meta must come before style
  assert.ok(doc.indexOf('Content-Security-Policy') < doc.indexOf('<style>'));
  // CSS before HTML
  assert.ok(doc.indexOf('<style>') < doc.indexOf('<body>'));
  // hostBridgeClient before js_functions
  assert.ok(doc.indexOf('hostBridge') < doc.indexOf('function add'));
  // js_functions before js_expressions
  assert.ok(doc.indexOf('function add') < doc.indexOf('document.getElementById'));
});

test('buildSandboxDocument includes all 6 fixed parameters', () => {
  const doc = buildSandboxDocument(basePayload);
  assert.ok(doc.includes(basePayload.css));
  assert.ok(doc.includes(basePayload.html));
  assert.ok(doc.includes(basePayload.js_functions));
  assert.ok(doc.includes(basePayload.js_expressions));
  assert.ok(doc.includes('hostBridge'));
  assert.ok(doc.includes('test-artifact-1'));
});

test('buildSandboxDocument escapes closing script/style sequences', () => {
  const payload = {
    ...basePayload,
    css: "body::after { content: '</style>'; }",
    js_functions: "function make() { return '</script>'; }",
    js_expressions: "console.log('</script>');",
  };
  const doc = buildSandboxDocument(payload);
  // The literal closing sequences must be escaped so the HTML parser
  // does not terminate the style/script elements early.
  assert.ok(!doc.includes("content: '</style>'"));
  assert.ok(!doc.includes("return '</script>'"));
  assert.ok(!doc.includes("console.log('</script>')"));
  // Escaped versions should be present
  assert.ok(doc.includes('<\\/style>'));
  assert.ok(doc.includes('<\\/script>'));
});

test('hostBridgeClient exposes window.hostBridge.emit', () => {
  assert.ok(HOST_BRIDGE_CLIENT_SCRIPT.includes('window.hostBridge'));
  assert.ok(HOST_BRIDGE_CLIENT_SCRIPT.includes('emit'));
  assert.ok(HOST_BRIDGE_CLIENT_SCRIPT.includes('postMessage'));
  assert.ok(HOST_BRIDGE_CLIENT_SCRIPT.includes('generative_ui.host_event'));
});

test('GenerativeUIArtifact renders iframe with correct sandbox and srcDoc', async () => {
  const dom = installDom();
  try {
    const container = document.getElementById('root');
    const root = createRoot(container);
    root.render(createElement(GenerativeUIArtifact, basePayload));

    await new Promise((resolve) => setTimeout(resolve, 0));

    const iframe = container.querySelector('iframe');
    assert.ok(iframe);
    assert.equal(iframe.getAttribute('sandbox'), GENERATIVE_UI_SANDBOX);
    assert.ok(iframe.getAttribute('srcdoc').includes('Hello'));
    assert.equal(iframe.style.height, '320px');
    assert.equal(iframe.title, 'Calculator');

    root.unmount();
  } finally {
    cleanupDom(dom);
  }
});

test('GenerativeUIArtifact uses default title when title is not provided', async () => {
  const dom = installDom();
  try {
    const container = document.getElementById('root');
    const root = createRoot(container);
    const payload = { ...basePayload, title: null };
    root.render(createElement(GenerativeUIArtifact, payload));

    await new Promise((resolve) => setTimeout(resolve, 0));

    const iframe = container.querySelector('iframe');
    assert.equal(iframe.title, 'generative-ui-artifact');

    root.unmount();
  } finally {
    cleanupDom(dom);
  }
});

test('GenerativeUIArtifact shows placeholder while iframe is loading', async () => {
  const dom = installDom();
  try {
    const container = document.getElementById('root');
    const root = createRoot(container);
    root.render(createElement(GenerativeUIArtifact, basePayload));

    await new Promise((resolve) => setTimeout(resolve, 0));

    const placeholder = container.querySelector('.generative-ui-artifact__placeholder');
    assert.ok(placeholder);
    assert.equal(placeholder.textContent, 'Loading...');

    root.unmount();
  } finally {
    cleanupDom(dom);
  }
});
