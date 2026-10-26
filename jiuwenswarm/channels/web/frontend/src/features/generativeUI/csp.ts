export const GENERATIVE_UI_CSP = [
  "default-src 'none'",
  "script-src 'unsafe-inline'",
  "style-src 'unsafe-inline'",
  'img-src data: blob:',
  "form-action 'none'",
  "base-uri 'none'",
].join('; ');

export const GENERATIVE_UI_SANDBOX = 'allow-scripts';
