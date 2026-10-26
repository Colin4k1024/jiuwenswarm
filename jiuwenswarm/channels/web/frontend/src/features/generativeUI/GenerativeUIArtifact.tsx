import { forwardRef, useMemo, useState } from 'react';
import { buildSandboxDocument } from './buildSandboxDocument';
import { GENERATIVE_UI_SANDBOX } from './csp';
import type { GenerativeUIArtifactPayload } from './types';

export interface GenerativeUIArtifactProps extends GenerativeUIArtifactPayload {
  onLoad?: () => void;
}

export const GenerativeUIArtifact = forwardRef<HTMLIFrameElement, GenerativeUIArtifactProps>(
  (props, ref) => {
    const {
      artifact_id,
      initial_height,
      placeholder_messages,
      css,
      html,
      js_functions,
      js_expressions,
      title,
      description,
      onLoad,
    } = props;

    const [isLoaded, setIsLoaded] = useState(false);

    const srcDoc = useMemo(
      () =>
        buildSandboxDocument({
          artifact_id,
          initial_height,
          placeholder_messages,
          css,
          html,
          js_functions,
          js_expressions,
          title,
          description,
        }),
      [artifact_id, initial_height, placeholder_messages, css, html, js_functions, js_expressions, title, description],
    );

    const handleLoad = () => {
      setIsLoaded(true);
      onLoad?.();
    };

    return (
      <div
        className="generative-ui-artifact"
        data-artifact-id={artifact_id}
        data-testid="generative-ui-artifact"
      >
        {!isLoaded && placeholder_messages.length > 0 && (
          <div
            className="generative-ui-artifact__placeholder"
            data-testid="generative-ui-placeholder"
          >
            {placeholder_messages[0]}
          </div>
        )}
        <iframe
          ref={ref}
          sandbox={GENERATIVE_UI_SANDBOX}
          srcDoc={srcDoc}
          title={title ?? 'generative-ui-artifact'}
          data-testid="generative-ui-frame"
          style={{
            width: '100%',
            height: `${initial_height}px`,
            border: 'none',
            display: isLoaded ? 'block' : 'none',
          }}
          onLoad={handleLoad}
        />
      </div>
    );
  },
);

GenerativeUIArtifact.displayName = 'GenerativeUIArtifact';
