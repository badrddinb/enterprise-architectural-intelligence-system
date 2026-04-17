// =============================================================================
// CanvasControls — Zoom/fit/reset overlay buttons for the canvas
// =============================================================================

import React from 'react';
import { ZoomIn, ZoomOut, Maximize2, AlertTriangle } from 'lucide-react';

interface CanvasControlsProps {
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFitToView: () => void;
  zoomLevel: number;
  lineCount: number;
  conflictCount: number;
  triageMode: boolean;
}

export const CanvasControls: React.FC<CanvasControlsProps> = ({
  onZoomIn,
  onZoomOut,
  onFitToView,
  zoomLevel,
  lineCount,
  conflictCount,
  triageMode,
}) => {
  return (
    <div className="absolute top-3 right-3 z-10 flex flex-col gap-2">
      {/* Triage mode banner */}
      {triageMode && (
        <div className="bg-red-50 border border-red-200 rounded-lg px-3 py-2 flex items-center gap-2 shadow-sm animate-pulse">
          <AlertTriangle className="w-4 h-4 text-red-500" />
          <span className="text-xs font-semibold text-red-700 uppercase tracking-wide">
            Triage Mode — {conflictCount} Conflicts
          </span>
        </div>
      )}

      {/* Zoom controls */}
      <div className="bg-white/90 backdrop-blur-sm rounded-lg shadow-sm border border-slate-200 flex flex-col overflow-hidden">
        <button
          onClick={onZoomIn}
          className="p-2 hover:bg-slate-100 transition-colors border-b border-slate-100"
          title="Zoom In"
        >
          <ZoomIn className="w-4 h-4 text-slate-600" />
        </button>
        <div className="px-2 py-1 text-xs text-center font-mono text-slate-500 border-b border-slate-100 min-w-[60px]">
        {zoomLevel.toFixed(0)}% · {lineCount} lines
        </div>
        <button
          onClick={onZoomOut}
          className="p-2 hover:bg-slate-100 transition-colors border-b border-slate-100"
          title="Zoom Out"
        >
          <ZoomOut className="w-4 h-4 text-slate-600" />
        </button>
        <button
          onClick={onFitToView}
          className="p-2 hover:bg-slate-100 transition-colors"
          title="Fit to View"
        >
          <Maximize2 className="w-4 h-4 text-slate-600" />
        </button>
      </div>
    </div>
  );
};

CanvasControls.displayName = 'CanvasControls';