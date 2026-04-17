// =============================================================================
// PipelineProgress — Visual tracker for Step Functions processing stages
// =============================================================================

import React from 'react';
import { CheckCircle, Loader2, Circle, XCircle, ArrowRight } from 'lucide-react';
import type { PipelineStage, PipelineStageStatus } from '../../types';

interface PipelineProgressProps {
  stages: Record<PipelineStage, PipelineStageStatus>;
  currentStage: PipelineStage | null;
}

const STAGE_CONFIG: Array<{ key: PipelineStage; label: string; description: string }> = [
  { key: 'ingestion', label: 'Ingestion', description: 'File validation & S3 upload' },
  { key: 'vector-raster-extraction', label: 'Vector/Raster Extraction', description: 'OpenCV + Textract processing' },
  { key: 'spatial-linking', label: 'Spatial Linking', description: 'Coordinate normalization & graph construction' },
  { key: 'math-audit', label: 'Math Audit', description: 'Dimensional conflict detection' },
  { key: 'langgraph-analysis', label: 'LangGraph Analysis', description: 'Compliance evaluation' },
];

const statusIcon: Record<PipelineStageStatus, React.ReactNode> = {
  pending: <Circle className="w-4 h-4 text-slate-300" />,
  running: <Loader2 className="w-4 h-4 text-blue-500 animate-spin" />,
  complete: <CheckCircle className="w-4 h-4 text-green-500" />,
  failed: <XCircle className="w-4 h-4 text-red-500" />,
};

const statusColor: Record<PipelineStageStatus, string> = {
  pending: 'text-slate-400',
  running: 'text-blue-600 font-medium',
  complete: 'text-green-600',
  failed: 'text-red-600',
};

const statusBg: Record<PipelineStageStatus, string> = {
  pending: 'bg-slate-50',
  running: 'bg-blue-50 border-blue-200',
  complete: 'bg-green-50',
  failed: 'bg-red-50',
};

export const PipelineProgress: React.FC<PipelineProgressProps> = ({ stages }) => {
  return (
    <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
      <h3 className="text-sm font-semibold text-slate-700 uppercase tracking-wide mb-4">
        Processing Pipeline
      </h3>

      <div className="space-y-0">
        {STAGE_CONFIG.map((stage, idx) => {
          const status = stages[stage.key];
          const isLast = idx === STAGE_CONFIG.length - 1;

          return (
            <div key={stage.key}>
              <div className={`flex items-start gap-3 p-3 rounded-lg ${statusBg[status]} border border-transparent transition-all`}>
                <div className="mt-0.5">{statusIcon[status]}</div>
                <div className="flex-1 min-w-0">
                  <div className={`text-sm ${statusColor[status]}`}>
                    {stage.label}
                  </div>
                  <div className="text-xs text-slate-400 mt-0.5">
                    {stage.description}
                  </div>
                </div>
                {status === 'running' && (
                  <span className="text-xs text-blue-500 font-medium animate-pulse">
                    RUNNING
                  </span>
                )}
              </div>
              {!isLast && (
                <div className="flex justify-center py-1">
                  <ArrowRight className="w-3 h-3 text-slate-300 rotate-90" />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};

PipelineProgress.displayName = 'PipelineProgress';