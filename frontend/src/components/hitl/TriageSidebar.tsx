// =============================================================================
// TriageSidebar — "The Guillotine" HITL Conflict Resolution Panel
// =============================================================================
// When the Java Math Audit returns CONFLICTS_DETECTED, this sidebar
// displays all ConflictObjects with Force Geometry / Force Text buttons.
// =============================================================================

import React, { useCallback, useState } from 'react';
import {
  AlertTriangle,
  Ruler,
  Type,
  CheckCircle2,
  Send,
  ShieldAlert,
} from 'lucide-react';
import type {
  AuditResponse,
  ConflictRecord,
  ConflictResolution,
  ResolvedConflict,
  ResolutionPayload,
} from '../../types';

interface TriageSidebarProps {
  auditResponse: AuditResponse;
  highlightedConflictId: string | null;
  onConflictHover: (conflictId: string | null) => void;
  onConflictSelect: (conflictId: string) => void;
  onResolutionsSubmit: (payload: ResolutionPayload) => void;
}

export const TriageSidebar: React.FC<TriageSidebarProps> = ({
  auditResponse,
  highlightedConflictId,
  onConflictHover,
  onConflictSelect,
  onResolutionsSubmit,
}) => {
  const [resolutions, setResolutions] = useState<Map<string, ConflictResolution>>(new Map());
  const [isSubmitting, setIsSubmitting] = useState(false);

  const conflicts = auditResponse.conflicts;
  const totalConflicts = conflicts.length;
  const resolvedCount = resolutions.size;
  const allResolved = resolvedCount === totalConflicts;

  const handleResolve = useCallback(
    (conflict: ConflictRecord, resolution: ConflictResolution) => {
      setResolutions((prev) => {
        const next = new Map(prev);
        next.set(conflict.conflictId, resolution);
        return next;
      });
    },
    []
  );

  const handleSubmit = useCallback(async () => {
    const payload: ResolutionPayload = {
      auditId: auditResponse.auditId,
      resolutions: conflicts
        .map((conflict): ResolvedConflict | null => {
          const resolution = resolutions.get(conflict.conflictId);
          if (!resolution) return null;
          return {
            conflictId: conflict.conflictId,
            lineId: conflict.lineId,
            resolution,
            resolvedValue:
              resolution === 'FORCE_GEOMETRY'
                ? conflict.computedDistance
                : conflict.explicitDimension,
          };
        })
        .filter(Boolean) as ResolvedConflict[],
    };

    setIsSubmitting(true);
    try {
      onResolutionsSubmit(payload);
    } finally {
      setIsSubmitting(false);
    }
  }, [auditResponse.auditId, conflicts, resolutions, onResolutionsSubmit]);

  return (
    <div className="w-96 bg-white border-l border-slate-200 shadow-lg flex flex-col h-full">
      {/* Header */}
      <div className="p-4 bg-red-50 border-b border-red-200">
        <div className="flex items-center gap-2 mb-2">
          <ShieldAlert className="w-5 h-5 text-red-600" />
          <h2 className="text-sm font-bold text-red-800 uppercase tracking-wide">
            The Guillotine — Triage Mode
          </h2>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-xs text-red-600">
            {resolvedCount}/{totalConflicts} resolved
          </span>
          <div className="w-32 h-2 bg-red-200 rounded-full overflow-hidden">
            <div
              className="h-full bg-green-500 transition-all duration-300 rounded-full"
              style={{ width: `${(resolvedCount / totalConflicts) * 100}%` }}
            />
          </div>
        </div>
      </div>

      {/* Audit Statistics */}
      <div className="px-4 py-3 bg-slate-50 border-b border-slate-200">
        <div className="grid grid-cols-3 gap-2 text-center">
          <div>
            <div className="text-lg font-bold text-slate-700">
              {auditResponse.statistics.totalLines}
            </div>
            <div className="text-xs text-slate-400">Lines</div>
          </div>
          <div>
            <div className="text-lg font-bold text-green-600">
              {auditResponse.statistics.cleanLines}
            </div>
            <div className="text-xs text-slate-400">Clean</div>
          </div>
          <div>
            <div className="text-lg font-bold text-red-600">
              {auditResponse.statistics.conflictingLines}
            </div>
            <div className="text-xs text-slate-400">Conflicts</div>
          </div>
        </div>
      </div>

      {/* Conflict List */}
      <div className="flex-1 overflow-y-auto">
        {conflicts.map((conflict, idx) => {
          const resolution = resolutions.get(conflict.conflictId);
          const isHighlighted = highlightedConflictId === conflict.conflictId;

          return (
            <div
              key={conflict.conflictId}
              className={`
                border-b border-slate-100 p-4 transition-all cursor-pointer
                ${isHighlighted ? 'bg-red-50 ring-1 ring-red-200' : 'hover:bg-slate-50'}
                ${resolution ? 'bg-green-50/50' : ''}
              `}
              onMouseEnter={() => onConflictHover(conflict.conflictId)}
              onMouseLeave={() => onConflictHover(null)}
              onClick={() => onConflictSelect(conflict.conflictId)}
            >
              {/* Conflict header */}
              <div className="flex items-start gap-2 mb-3">
                <span
                  className={`
                    inline-flex items-center justify-center w-5 h-5 rounded text-xs font-bold
                    ${conflict.severity === 'critical'
                      ? 'bg-red-500 text-white'
                      : 'bg-amber-400 text-white'
                    }
                  `}
                >
                  {idx + 1}
                </span>
                <div className="flex-1">
                  <div className="text-sm font-semibold text-slate-700">
                    {conflict.lineId}
                    <span className="ml-2 text-xs text-slate-400 font-normal">
                      ({conflict.lineType})
                    </span>
                  </div>
                  <span
                    className={`
                      inline-block mt-1 text-xs px-2 py-0.5 rounded-full font-medium
                      ${conflict.severity === 'critical'
                        ? 'bg-red-100 text-red-700'
                        : 'bg-amber-100 text-amber-700'
                      }
                    `}
                  >
                    {conflict.severity.toUpperCase()}
                  </span>
                </div>
                {resolution && (
                  <CheckCircle2 className="w-5 h-5 text-green-500 shrink-0" />
                )}
              </div>

              {/* Conflict details */}
              <div className="grid grid-cols-2 gap-2 mb-3 text-xs">
                <div className="bg-blue-50 rounded p-2">
                  <div className="text-blue-400 mb-1 flex items-center gap-1">
                    <Ruler className="w-3 h-3" /> Geometry
                  </div>
                  <div className="font-mono font-bold text-blue-700">
                    {conflict.computedDistance.toFixed(3)} {conflict.unit}
                  </div>
                </div>
                <div className="bg-purple-50 rounded p-2">
                  <div className="text-purple-400 mb-1 flex items-center gap-1">
                    <Type className="w-3 h-3" /> OCR Text
                  </div>
                  <div className="font-mono font-bold text-purple-700">
                    {conflict.explicitDimension.toFixed(3)} {conflict.unit}
                  </div>
                </div>
              </div>

              {/* Deviation */}
              <div className="text-xs text-slate-500 mb-3 flex items-center gap-2">
                <AlertTriangle className="w-3 h-3" />
                <span>
                  Δ = {conflict.deviationAbsolute.toFixed(3)} {conflict.unit} (
                  {conflict.deviationPercentage.toFixed(1)}% deviation)
                </span>
              </div>

              {/* Action buttons */}
              <div className="flex gap-2">
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handleResolve(conflict, 'FORCE_GEOMETRY');
                  }}
                  disabled={!!resolution}
                  className={`
                    flex-1 text-xs font-medium py-2 px-3 rounded-lg border transition-all
                    flex items-center justify-center gap-1
                    ${resolution === 'FORCE_GEOMETRY'
                      ? 'bg-blue-500 text-white border-blue-500'
                      : resolution
                        ? 'bg-slate-100 text-slate-400 border-slate-200 cursor-not-allowed'
                        : 'bg-white text-blue-600 border-blue-300 hover:bg-blue-50 hover:border-blue-400'
                    }
                  `}
                >
                  <Ruler className="w-3 h-3" />
                  Force Geometry
                </button>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handleResolve(conflict, 'FORCE_TEXT');
                  }}
                  disabled={!!resolution}
                  className={`
                    flex-1 text-xs font-medium py-2 px-3 rounded-lg border transition-all
                    flex items-center justify-center gap-1
                    ${resolution === 'FORCE_TEXT'
                      ? 'bg-purple-500 text-white border-purple-500'
                      : resolution
                        ? 'bg-slate-100 text-slate-400 border-slate-200 cursor-not-allowed'
                        : 'bg-white text-purple-600 border-purple-300 hover:bg-purple-50 hover:border-purple-400'
                    }
                  `}
                >
                  <Type className="w-3 h-3" />
                  Force Text
                </button>
              </div>
            </div>
          );
        })}
      </div>

      {/* Submit bar */}
      <div className="p-4 border-t border-slate-200 bg-slate-50">
        <button
          onClick={handleSubmit}
          disabled={!allResolved || isSubmitting}
          className={`
            w-full py-3 rounded-lg font-semibold text-sm transition-all
            flex items-center justify-center gap-2
            ${allResolved
              ? 'bg-green-500 text-white hover:bg-green-600 shadow-lg shadow-green-200'
              : 'bg-slate-200 text-slate-400 cursor-not-allowed'
            }
          `}
        >
          <Send className="w-4 h-4" />
          {isSubmitting
            ? 'Submitting...'
            : allResolved
              ? 'Submit Resolutions'
              : `Resolve ${totalConflicts - resolvedCount} remaining conflict${totalConflicts - resolvedCount !== 1 ? 's' : ''}`
          }
        </button>
        {!allResolved && (
          <p className="text-xs text-slate-400 text-center mt-2">
            Resolve all conflicts before submitting
          </p>
        )}
      </div>
    </div>
  );
};

TriageSidebar.displayName = 'TriageSidebar';