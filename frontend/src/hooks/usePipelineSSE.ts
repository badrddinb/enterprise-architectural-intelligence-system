// =============================================================================
// usePipelineSSE — Server-Sent Events hook for real-time pipeline tracking
// =============================================================================
// Opens an SSE connection to /api/v1/jobs/{fileId}/stream and exposes
// live pipeline events as React state. No polling. No simulation.
// =============================================================================

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  createPipelineStream,
  type SSEStageUpdate,
  type SSECoordinates,
  type SSEAuditResult,
  type SSECompliance,
  type SSEError,
} from '../services/api';

// ---- Hook state ----

export interface PipelineSSEState {
  /** Whether the SSE connection is active */
  isConnected: boolean;
  /** Current pipeline stage from the backend */
  currentStage: string | null;
  /** Current stage status (PROCESSING / COMPLETED / FAILED) */
  currentStatus: string | null;
  /** File name from backend */
  fileName: string | null;
  /** Detected format from backend */
  detectedFormat: string | null;
  /** Raw coordinate data from backend — for canvas rendering */
  coordinates: SSECoordinates | null;
  /** Audit result from backend — for triage mode */
  auditResult: SSEAuditResult | null;
  /** Compliance report from backend */
  compliance: SSECompliance | null;
  /** Error message if pipeline failed */
  error: string | null;
  /** Whether pipeline has completed (success or failure) */
  isDone: boolean;
  /** All stage updates received so far */
  stageHistory: SSEStageUpdate[];
}

const INITIAL_STATE: PipelineSSEState = {
  isConnected: false,
  currentStage: null,
  currentStatus: null,
  fileName: null,
  detectedFormat: null,
  coordinates: null,
  auditResult: null,
  compliance: null,
  error: null,
  isDone: false,
  stageHistory: [],
};

/**
 * usePipelineSSE
 *
 * @param fileId — The fileId returned from upload. Null to disconnect/reset.
 *
 * Opens an SSE connection and tracks real-time pipeline events.
 * Automatically closes on unmount or when fileId becomes null.
 */
export function usePipelineSSE(fileId: string | null): PipelineSSEState {
  const [state, setState] = useState<PipelineSSEState>(INITIAL_STATE);
  const eventSourceRef = useRef<EventSource | null>(null);
  const fileIdRef = useRef<string | null>(null);

  // Cleanup function
  const closeConnection = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
  }, []);

  // Track fileId changes
  useEffect(() => {
    fileIdRef.current = fileId;

    // Close previous connection
    closeConnection();

    if (!fileId) {
      setState(INITIAL_STATE);
      return;
    }

    // Reset state for new fileId
    setState({ ...INITIAL_STATE });

    // Open SSE connection
    const es = createPipelineStream(fileId);
    eventSourceRef.current = es;

    // --- Event listeners ---

    es.addEventListener('connected', (e: MessageEvent) => {
      if (fileIdRef.current !== fileId) return;
      const data = JSON.parse(e.data);
      setState((prev) => ({
        ...prev,
        isConnected: true,
        fileName: data.fileName || prev.fileName,
      }));
    });

    es.addEventListener('stage-update', (e: MessageEvent) => {
      if (fileIdRef.current !== fileId) return;
      const data: SSEStageUpdate = JSON.parse(e.data);
      setState((prev) => ({
        ...prev,
        isConnected: true,
        currentStage: data.stage,
        currentStatus: data.status,
        fileName: data.fileName || prev.fileName,
        detectedFormat: data.detectedFormat || prev.detectedFormat,
        stageHistory: [...prev.stageHistory, data],
      }));
    });

    es.addEventListener('coordinates', (e: MessageEvent) => {
      if (fileIdRef.current !== fileId) return;
      const data: SSECoordinates = JSON.parse(e.data);
      setState((prev) => ({
        ...prev,
        coordinates: data,
      }));
    });

    es.addEventListener('audit-result', (e: MessageEvent) => {
      if (fileIdRef.current !== fileId) return;
      const data: SSEAuditResult = JSON.parse(e.data);
      setState((prev) => ({
        ...prev,
        auditResult: data,
      }));
    });

    es.addEventListener('compliance', (e: MessageEvent) => {
      if (fileIdRef.current !== fileId) return;
      const data: SSECompliance = JSON.parse(e.data);
      setState((prev) => ({
        ...prev,
        compliance: data,
      }));
    });

    es.addEventListener('done', () => {
      if (fileIdRef.current !== fileId) return;
      setState((prev) => ({
        ...prev,
        isDone: true,
        isConnected: false,
      }));
      closeConnection();
    });

    es.addEventListener('error', (e: MessageEvent) => {
      if (fileIdRef.current !== fileId) return;
      try {
        const data: SSEError = JSON.parse(e.data);
        setState((prev) => ({
          ...prev,
          error: data.message || 'Pipeline error',
          isDone: true,
          isConnected: false,
        }));
      } catch {
        // SSE error event (not our custom event)
        setState((prev) => ({
          ...prev,
          error: 'Connection to pipeline lost',
          isDone: true,
          isConnected: false,
        }));
      }
      closeConnection();
    });

    // Native error handler (connection failure)
    es.onerror = () => {
      if (fileIdRef.current !== fileId) return;
      setState((prev) => {
        if (prev.isDone) return prev;
        return {
          ...prev,
          error: 'SSE connection failed — backend may be unreachable',
          isDone: true,
          isConnected: false,
        };
      });
      closeConnection();
    };

    // Cleanup on unmount or fileId change
    return () => {
      closeConnection();
    };
  }, [fileId, closeConnection]);

  return state;
}