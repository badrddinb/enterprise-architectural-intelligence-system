// =============================================================================
// App.tsx — Main Application Shell — SSE-Driven, Real Backend Only
// =============================================================================
// Enterprise Architectural Intelligence System — Control Tower
//
// Flow: Upload → fileId → SSE stream → stage updates → canvas data → triage
// No simulation. No demo data. 100% real backend integration.
// =============================================================================

import { useCallback, useEffect, useState } from 'react';
import { FloorPlanCanvas } from './components/canvas/FloorPlanCanvas';
import { UploadPanel } from './components/upload/UploadPanel';
import { PipelineProgress } from './components/upload/PipelineProgress';
import { TriageSidebar } from './components/hitl/TriageSidebar';
import { usePipelineSSE } from './hooks/usePipelineSSE';
import { submitResolutions } from './services/api';
import type {
  AuditResponse,
  ConflictRecord,
  PipelineStage,
  PipelineState,
  RenderLine,
  ResolutionPayload,
} from './types';
import {
  Building2,
  Activity,
  Layers,
  Brain,
  CheckCircle2,
  XCircle,
  Wifi,
} from 'lucide-react';

// ---- Pipeline stage mapping ----

const STAGE_ORDER: PipelineStage[] = [
  'ingestion',
  'vector-raster-extraction',
  'spatial-linking',
  'math-audit',
  'langgraph-analysis',
];

// Map backend stage names to UI pipeline stages
const BACKEND_STAGE_MAP: Record<string, PipelineStage> = {
  ingestion: 'ingestion',
  extraction: 'vector-raster-extraction',
  'vector-raster-extraction': 'vector-raster-extraction',
  'raster-extraction': 'vector-raster-extraction',
  'spatial-linking': 'spatial-linking',
  'dimension-linking': 'spatial-linking',
  'math-audit': 'math-audit',
  'dimension-audit': 'math-audit',
  compliance: 'langgraph-analysis',
  'langgraph-analysis': 'langgraph-analysis',
};

const INITIAL_PIPELINE: PipelineState = {
  stages: {
    ingestion: 'pending',
    'vector-raster-extraction': 'pending',
    'spatial-linking': 'pending',
    'math-audit': 'pending',
    'langgraph-analysis': 'pending',
  },
  currentStage: null,
  executionArn: null,
  error: null,
};

type AppView = 'upload' | 'processing' | 'canvas' | 'triage';

// ---- Convert raw backend lines to RenderLines ----

function rawLinesToRenderLines(
  rawLines: Array<{ id: string; start: number[]; end: number[]; explicit_dimension?: string; layer?: string; [k: string]: any }>,
  conflicts?: Array<{ lineId: string; conflictId: string; [k: string]: any }>
): RenderLine[] {
  const conflictMap = new Map<string, string>();
  if (conflicts) {
    conflicts.forEach((c) => conflictMap.set(c.lineId, c.conflictId));
  }

  return rawLines.map((line) => {
    const dx = line.end[0] - line.start[0];
    const dy = line.end[1] - line.start[1];
    const pixelLength = Math.sqrt(dx * dx + dy * dy);

    return {
      id: line.id,
      startX: line.start[0],
      startY: line.start[1],
      endX: line.end[0],
      endY: line.end[1],
      lineType: line.layer || line.lineType || 'wall',
      measuredLength: pixelLength,
      explicitDimension: line.explicit_dimension || undefined,
      isConflicting: conflictMap.has(line.id),
      severity: conflictMap.has(line.id) ? 'critical' as const : undefined,
      lineId: line.id,
      conflictId: conflictMap.get(line.id),
    };
  });
}

function App() {
  // ---- Core state ----
  const [view, setView] = useState<AppView>('upload');
  const [currentFileId, setCurrentFileId] = useState<string | null>(null);
  const [uploadedFileName, setUploadedFileName] = useState<string>('');

  // ---- Canvas & audit data (from SSE events) ----
  const [canvasData, setCanvasData] = useState<RenderLine[]>([]);
  const [auditResponse, setAuditResponse] = useState<AuditResponse | null>(null);
  const [highlightedConflictId, setHighlightedConflictId] = useState<string | null>(null);
  const [highlightedLineId, setHighlightedLineId] = useState<string | null>(null);

  // ---- Pipeline progress tracking ----
  const [pipeline, setPipeline] = useState<PipelineState>(INITIAL_PIPELINE);

  // ---- SSE connection ----
  const sse = usePipelineSSE(currentFileId);

  // ---- SSE → Pipeline stages mapping ----
  useEffect(() => {
    if (!sse.currentStage) return;

    const uiStage = BACKEND_STAGE_MAP[sse.currentStage] || 'ingestion';
    const stageIdx = STAGE_ORDER.indexOf(uiStage);

    setPipeline((prev) => {
      const newStages = { ...prev.stages };

      // Mark all stages before current as complete
      for (let i = 0; i < STAGE_ORDER.length; i++) {
        if (i < stageIdx) {
          newStages[STAGE_ORDER[i]] = 'complete';
        } else if (i === stageIdx) {
          newStages[STAGE_ORDER[i]] = sse.currentStatus === 'COMPLETED' ? 'complete' : 'running';
        }
      }

      return { ...prev, stages: newStages, currentStage: uiStage };
    });
  }, [sse.currentStage, sse.currentStatus]);

  // ---- SSE coordinates → Canvas ----
  useEffect(() => {
    if (!sse.coordinates?.data?.lines) return;

    const rawLines = sse.coordinates.data.lines;
    const conflicts = sse.auditResult?.data?.conflicts;

    const renderLines = rawLinesToRenderLines(rawLines, conflicts);
    setCanvasData(renderLines);

    // If we have lines but no audit yet, show canvas
    if (!sse.auditResult) {
      setView('canvas');
    }
  }, [sse.coordinates]);

  // ---- SSE audit result → Triage ----
  useEffect(() => {
    if (!sse.auditResult?.data) return;

    const auditData = sse.auditResult.data;
    const hasConflicts = auditData.status === 'CONFLICTS_DETECTED' ||
      (auditData.conflicts && auditData.conflicts.length > 0);

    // Build AuditResponse for TriageSidebar
    const audit: AuditResponse = {
      auditId: auditData.auditId || `audit-${Date.now()}`,
      timestamp: new Date().toISOString(),
      sourceCoordinatesId: sse.coordinates?.data?.coordinatesId || '',
      sourceFileId: currentFileId || '',
      scaleFactor: auditData.scaleFactor || 0.1,
      tolerancePercentage: auditData.tolerancePercentage || 5,
      status: hasConflicts ? 'CONFLICTS_DETECTED' as const : 'CLEAN' as const,
      statistics: auditData.statistics || {
        totalLines: canvasData.length,
        linesWithAnnotations: canvasData.length,
        cleanLines: canvasData.length - (auditData.conflicts?.length || 0),
        conflictingLines: auditData.conflicts?.length || 0,
        maxDeviationPercentage: 0,
        avgDeviationPercentage: 0,
      },
      conflicts: (auditData.conflicts || []).map((c: any): ConflictRecord => ({
        conflictId: c.conflictId,
        lineId: c.lineId,
        lineType: c.lineType || 'wall',
        computedDistance: c.measuredLength || 0,
        explicitDimension: c.dimensionedLength || 0,
        scaleFactor: c.scaleFactor || 1,
        deviationAbsolute: c.deviationAbsolute || Math.abs(c.deviationPercentage || 0),
        deviationPercentage: c.deviationPercentage || 0,
        toleranceThreshold: c.toleranceThreshold || 5,
        severity: c.severity || 'warning',
        annotationId: c.annotationId || '',
        startPointId: c.startPointId || '',
        endPointId: c.endPointId || '',
        unit: c.unit || 'ft',
      })),
    };

    setAuditResponse(audit);

    // Re-render canvas with conflict highlighting
    if (sse.coordinates?.data?.lines) {
      const renderLines = rawLinesToRenderLines(
        sse.coordinates.data.lines,
        auditData.conflicts
      );
      setCanvasData(renderLines);
    }

    if (hasConflicts) {
      setView('triage');
    } else {
      setView('canvas');
    }
  }, [sse.auditResult]);

  // ---- SSE compliance → Final stage ----
  useEffect(() => {
    if (!sse.compliance) return;

    // Mark all stages as complete
    setPipeline((prev) => ({
      ...prev,
      stages: {
        ingestion: 'complete',
        'vector-raster-extraction': 'complete',
        'spatial-linking': 'complete',
        'math-audit': 'complete',
        'langgraph-analysis': 'complete',
      },
      currentStage: 'langgraph-analysis',
    }));
  }, [sse.compliance]);

  // ---- SSE error ----
  useEffect(() => {
    if (!sse.error) return;

    setPipeline((prev) => ({
      ...prev,
      error: sse.error,
    }));
  }, [sse.error]);

  // ---- Handle upload success ----
  const handleJobStarted = useCallback((fileId: string, fileName: string) => {
    setCurrentFileId(fileId);
    setUploadedFileName(fileName);
    setCanvasData([]);
    setAuditResponse(null);
    setHighlightedConflictId(null);
    setHighlightedLineId(null);
    setView('processing');
    setPipeline({
      ...INITIAL_PIPELINE,
      stages: { ...INITIAL_PIPELINE.stages, ingestion: 'running' },
      currentStage: 'ingestion',
    });
  }, []);

  // ---- Conflict interaction handlers ----
  const handleConflictHover = useCallback(
    (conflictId: string | null) => {
      setHighlightedConflictId(conflictId);
      if (conflictId && auditResponse) {
        const conflict = auditResponse.conflicts.find(
          (c) => c.conflictId === conflictId
        );
        if (conflict) {
          setHighlightedLineId(conflict.lineId);
          return;
        }
      }
      setHighlightedLineId(null);
    },
    [auditResponse]
  );

  const handleConflictSelect = useCallback(
    (conflictId: string) => {
      if (auditResponse) {
        const conflict = auditResponse.conflicts.find(
          (c) => c.conflictId === conflictId
        );
        if (conflict) setHighlightedLineId(conflict.lineId);
      }
    },
    [auditResponse]
  );

  const handleResolutionsSubmit = useCallback(
    async (payload: ResolutionPayload) => {
      try {
        await submitResolutions({
          auditId: payload.auditId,
          resolutions: payload.resolutions.map((r) => ({
            conflictId: r.conflictId,
            action: r.resolution as 'FORCE_GEOMETRY' | 'FORCE_TEXT',
          })),
        });
        // On success, go back to canvas view
        setView('canvas');
      } catch (err) {
        console.error('[HITL] Resolution submission failed:', err);
        alert(`Resolution submission failed: ${err instanceof Error ? err.message : String(err)}`);
      }
    },
    []
  );

  const handleLineClick = useCallback((lineId: string) => {
    setHighlightedLineId(lineId);
  }, []);

  // ---- Determine current status ----
  const isProcessing = sse.isConnected || (currentFileId !== null && !sse.isDone && !sse.error);

  return (
    <div className="h-screen flex flex-col bg-slate-100 overflow-hidden">
      {/* ---- Top Bar ---- */}
      <header className="bg-white border-b border-slate-200 px-6 py-3 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-3">
          <Building2 className="w-6 h-6 text-blue-600" />
          <div>
            <h1 className="text-base font-bold text-slate-800">
              Architectural Intelligence System
            </h1>
            <p className="text-xs text-slate-400">Control Tower & HITL Gateway</p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {/* SSE connection indicator */}
          <div className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${
            sse.isConnected
              ? 'bg-green-50 text-green-600 border border-green-200'
              : currentFileId && sse.isDone && !sse.error
                ? 'bg-blue-50 text-blue-600 border border-blue-200'
                : 'bg-slate-50 text-slate-400 border border-slate-200'
          }`}>
            <Wifi className="w-3 h-3" />
            {sse.isConnected ? 'LIVE' : currentFileId ? (sse.isDone ? 'Complete' : 'Disconnected') : 'Idle'}
          </div>

          {/* View tabs */}
          <div className="flex bg-slate-100 rounded-lg p-0.5">
            <button
              onClick={() => setView('upload')}
              className={`px-3 py-1.5 text-xs font-medium rounded-md transition-all ${
                view === 'upload' || view === 'processing'
                  ? 'bg-white text-slate-700 shadow-sm'
                  : 'text-slate-500 hover:text-slate-700'
              }`}
            >
              <Layers className="w-3 h-3 inline mr-1" />
              Upload
            </button>
            <button
              onClick={() => canvasData.length > 0 && setView('canvas')}
              disabled={canvasData.length === 0}
              className={`px-3 py-1.5 text-xs font-medium rounded-md transition-all ${
                view === 'canvas'
                  ? 'bg-white text-slate-700 shadow-sm'
                  : canvasData.length === 0
                    ? 'text-slate-300 cursor-not-allowed'
                    : 'text-slate-500 hover:text-slate-700'
              }`}
            >
              <Layers className="w-3 h-3 inline mr-1" />
              Canvas
            </button>
            {auditResponse?.status === 'CONFLICTS_DETECTED' && (
              <button
                onClick={() => setView('triage')}
                className={`px-3 py-1.5 text-xs font-medium rounded-md transition-all ${
                  view === 'triage'
                    ? 'bg-red-100 text-red-700 shadow-sm'
                    : 'text-red-500 hover:text-red-700'
                }`}
              >
                <Activity className="w-3 h-3 inline mr-1" />
                Triage ({auditResponse.conflicts.length})
              </button>
            )}
          </div>
        </div>
      </header>

      {/* ---- Main Content ---- */}
      <div className="flex-1 flex overflow-hidden">
        {/* ---- Upload + Processing View ---- */}
        {(view === 'upload' || view === 'processing') && (
          <>
            {/* Left sidebar */}
            <div className="w-96 shrink-0 p-4 space-y-4 overflow-y-auto bg-slate-50 border-r border-slate-200">
              <UploadPanel
                onJobStarted={handleJobStarted}
                isProcessing={isProcessing}
              />
              <PipelineProgress
                stages={pipeline.stages}
                currentStage={pipeline.currentStage}
              />

              {/* System info */}
              <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
                <h3 className="text-sm font-semibold text-slate-700 uppercase tracking-wide mb-3">
                  System Architecture
                </h3>
                <div className="space-y-2 text-xs text-slate-500">
                  <div className="flex items-center gap-2">
                    <div className="w-2 h-2 rounded-full bg-blue-400" />
                    <span>Python (OpenCV/Textract) — Vector/Raster extraction</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="w-2 h-2 rounded-full bg-green-400" />
                    <span>Java (Spring Boot) — Dimensional Math Audit</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="w-2 h-2 rounded-full bg-purple-400" />
                    <span>LangGraph — Compliance analysis engine</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="w-2 h-2 rounded-full bg-amber-400" />
                    <span>AWS LocalStack — S3 + Step Functions</span>
                  </div>
                </div>
              </div>
            </div>

            {/* Center area */}
            <div className="flex-1 flex items-center justify-center">
              {isProcessing ? (
                /* ---- Processing Indicator ---- */
                <div className="text-center max-w-lg">
                  <div className="relative w-24 h-24 mx-auto mb-6">
                    <div className="absolute inset-0 rounded-full border-4 border-slate-200" />
                    <div className="absolute inset-0 rounded-full border-4 border-blue-500 border-t-transparent animate-spin" />
                    <Brain className="absolute inset-0 m-auto w-10 h-10 text-blue-500" />
                  </div>

                  <h2 className="text-xl font-bold text-slate-700 mb-2">
                    AI Pipeline Running
                  </h2>

                  <p className="text-sm text-slate-500 mb-1">
                    Stage: <span className="font-medium text-blue-600">{sse.currentStage || 'Initializing...'}</span>
                    {sse.currentStatus && (
                      <span className="text-slate-400 ml-2">({sse.currentStatus})</span>
                    )}
                  </p>

                  {sse.detectedFormat && (
                    <p className="text-xs text-slate-400 mb-3">
                      Format: {sse.detectedFormat}
                    </p>
                  )}

                  {/* Progress bar based on stage position */}
                  <div className="w-64 mx-auto bg-slate-200 rounded-full h-2 mb-3">
                    <div
                      className="bg-blue-500 rounded-full h-2 transition-all duration-700"
                      style={{
                        width: `${(() => {
                          if (!sse.currentStage) return 5;
                          const uiStage = BACKEND_STAGE_MAP[sse.currentStage] || 'ingestion';
                          const idx = STAGE_ORDER.indexOf(uiStage);
                          return Math.max(10, ((idx + 1) / STAGE_ORDER.length) * 100);
                        })()}%`,
                      }}
                    />
                  </div>

                  <p className="text-xs text-slate-400">
                    Job: <code className="bg-slate-100 px-1.5 py-0.5 rounded">{currentFileId}</code>
                  </p>

                  {uploadedFileName && (
                    <p className="text-xs text-slate-400 mt-1">
                      File: {uploadedFileName}
                    </p>
                  )}

                  <p className="text-xs text-slate-300 mt-3">
                    Connected via SSE — receiving real-time updates
                  </p>
                </div>
              ) : sse.error ? (
                /* ---- Error State ---- */
                <div className="text-center max-w-lg">
                  <XCircle className="w-16 h-16 text-red-400 mx-auto mb-4" />
                  <h2 className="text-xl font-semibold text-slate-600 mb-2">
                    Pipeline Error
                  </h2>
                  <p className="text-sm text-red-500 mb-4">{sse.error}</p>
                  <button
                    onClick={() => {
                      setCurrentFileId(null);
                      setView('upload');
                    }}
                    className="px-4 py-2 text-sm font-medium bg-slate-50 text-slate-600 rounded-lg hover:bg-slate-100 transition-colors border border-slate-200"
                  >
                    Try Again
                  </button>
                </div>
              ) : sse.isDone ? (
                /* ---- Done State ---- */
                <div className="text-center max-w-lg">
                  <CheckCircle2 className="w-16 h-16 text-green-400 mx-auto mb-4" />
                  <h2 className="text-xl font-semibold text-slate-600 mb-2">
                    Pipeline Complete
                  </h2>
                  <p className="text-sm text-slate-400 mb-4">
                    All stages processed successfully. View results on the Canvas tab.
                  </p>
                  <button
                    onClick={() => canvasData.length > 0 && setView('canvas')}
                    className="px-4 py-2 text-sm font-medium bg-blue-50 text-blue-600 rounded-lg hover:bg-blue-100 transition-colors border border-blue-200"
                  >
                    View Canvas
                  </button>
                </div>
              ) : (
                /* ---- Empty State ---- */
                <div className="text-center">
                  <Building2 className="w-16 h-16 text-slate-200 mx-auto mb-4" />
                  <h2 className="text-xl font-semibold text-slate-400 mb-2">
                    No Floor Plan Loaded
                  </h2>
                  <p className="text-sm text-slate-400 mb-4 max-w-md">
                    Upload a PDF or DXF file to begin processing.
                    The pipeline will run through ingestion, extraction,
                    spatial linking, math audit, and compliance analysis.
                  </p>
                </div>
              )}
            </div>
          </>
        )}

        {/* ---- Canvas View ---- */}
        {view === 'canvas' && canvasData.length > 0 && (
          <div className="flex-1 p-4">
            <FloorPlanCanvas
              lines={canvasData}
              triageMode={false}
              highlightedLineId={highlightedLineId}
              onLineClick={handleLineClick}
              className="w-full h-full"
            />
          </div>
        )}

        {/* ---- Triage View ---- */}
        {view === 'triage' && auditResponse && canvasData.length > 0 && (
          <>
            <div className="flex-1 p-4">
              <FloorPlanCanvas
                lines={canvasData}
                triageMode={true}
                highlightedLineId={highlightedLineId}
                onLineClick={handleLineClick}
                className="w-full h-full"
              />
            </div>

            <TriageSidebar
              auditResponse={auditResponse}
              highlightedConflictId={highlightedConflictId}
              onConflictHover={handleConflictHover}
              onConflictSelect={handleConflictSelect}
              onResolutionsSubmit={handleResolutionsSubmit}
            />
          </>
        )}
      </div>
    </div>
  );
}

export default App;