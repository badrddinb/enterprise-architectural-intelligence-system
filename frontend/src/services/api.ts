// =============================================================================
// api.ts — Backend API client for the Architectural Intelligence System
// =============================================================================
// Real endpoints only — no simulation, no demo data.
//
// Endpoints:
//   POST /api/v1/upload              → Upload file, returns { fileId }
//   GET  /api/v1/jobs/{fileId}/stream → SSE stream for pipeline events
//   POST /api/v1/audit/dimensions    → Run/resolve dimension audit
// =============================================================================

// ---- Upload response ----

export interface UploadResponse {
  fileId: string;
  fileName: string;
  detectedFormat: string;
  category: string;
  mimeType: string;
  storageUri: string;
  status: string;
  message: string;
}

// ---- SSE event types ----

export interface SSEStageUpdate {
  fileId: string;
  stage: string;
  status: string;
  timestamp: string;
  fileName?: string;
  detectedFormat?: string;
}

export interface SSECoordinates {
  fileId: string;
  data: {
    coordinatesId?: string;
    lines?: RawLine[];
    points?: any[];
    annotations?: any[];
    [key: string]: any;
  };
}

export interface SSEAuditResult {
  fileId: string;
  data: {
    auditId?: string;
    status?: string;
    conflicts?: RawConflict[];
    statistics?: any;
    [key: string]: any;
  };
}

export interface SSECompliance {
  fileId: string;
  data: {
    reportId?: string;
    [key: string]: any;
  };
}

export interface SSEError {
  fileId?: string;
  stage?: string;
  message: string;
  timestamp?: string;
}

export interface SSEConnected {
  fileId: string;
  message: string;
}

export type SSEEvent =
  | { type: 'connected'; data: SSEConnected }
  | { type: 'stage-update'; data: SSEStageUpdate }
  | { type: 'coordinates'; data: SSECoordinates }
  | { type: 'audit-result'; data: SSEAuditResult }
  | { type: 'compliance'; data: SSECompliance }
  | { type: 'done'; data: { fileId: string; status: string; timestamp?: string } }
  | { type: 'error'; data: SSEError }
  | { type: 'heartbeat'; data: { iteration: number } };

// ---- Raw backend data types ----

export interface RawLine {
  id: string;
  lineId?: string;
  start: number[];
  end: number[];
  explicit_dimension?: string;
  layer?: string;
  lineType?: string;
  [key: string]: any;
}

export interface RawConflict {
  conflictId: string;
  lineId: string;
  annotationId?: string;
  measuredLength?: number;
  dimensionedLength?: number;
  deviationPercentage?: number;
  severity?: string;
  [key: string]: any;
}

// ---- Audit API ----

export interface AuditResolutionPayload {
  auditId: string;
  resolutions: Array<{
    conflictId: string;
    action: 'FORCE_GEOMETRY' | 'FORCE_TEXT';
  }>;
}

// ---- API functions ----

/**
 * Upload a file to the backend ingestion service.
 * Returns fileId for tracking via SSE.
 */
export async function uploadFile(file: File): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append('file', file);

  const response = await fetch('/api/v1/upload', {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    const errorBody = await response.json().catch(() => ({ detail: 'Upload failed' }));
    throw new Error(errorBody.detail || `Upload failed with status ${response.status}`);
  }

  return response.json();
}

/**
 * Create an SSE EventSource for real-time pipeline status updates.
 * The caller is responsible for adding event listeners and closing.
 */
export function createPipelineStream(fileId: string): EventSource {
  const url = `/api/v1/jobs/${fileId}/stream`;
  return new EventSource(url);
}

/**
 * Submit HITL conflict resolutions to the dimension audit service.
 */
export async function submitResolutions(payload: AuditResolutionPayload): Promise<any> {
  const response = await fetch('/api/v1/audit/dimensions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    throw new Error(`Audit resolution failed with status ${response.status}`);
  }

  return response.json();
}

/**
 * Fetch synchronous job status (fallback).
 */
export async function getJobStatus(fileId: string): Promise<any> {
  const response = await fetch(`/api/v1/jobs/${fileId}`);

  if (!response.ok) {
    throw new Error(`Job status fetch failed with status ${response.status}`);
  }

  return response.json();
}