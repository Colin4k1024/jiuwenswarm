export interface GenerativeUIArtifactPayload {
  artifact_id: string;
  initial_height: number;
  placeholder_messages: string[];
  css: string;
  html: string;
  js_functions: string;
  js_expressions: string;
  title: string | null;
  description: string | null;
}
