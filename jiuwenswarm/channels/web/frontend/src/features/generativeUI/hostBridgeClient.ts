export const HOST_BRIDGE_CLIENT_SCRIPT = `
(function() {
  'use strict';
  var artifactId = '__ARTIFACT_ID__';
  window.hostBridge = {
    emit: function(eventType, payload) {
      window.parent.postMessage({
        type: 'generative_ui.host_event',
        artifact_id: artifactId,
        event_type: eventType,
        payload: payload
      }, '*');
    }
  };
})();
`;
