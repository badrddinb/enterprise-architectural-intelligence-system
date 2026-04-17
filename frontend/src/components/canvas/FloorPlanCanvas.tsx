// =============================================================================
// FloorPlanCanvas — Main react-konva Stage for 2D floor plan visualization
// =============================================================================
// Renders lines from backend JSON, handles coordinate normalization,
// pan/zoom, and highlights conflicting lines in triage mode.
// =============================================================================

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Stage, Layer, Rect } from 'react-konva';
import { CanvasLine } from './CanvasLine';
import { CanvasControls } from './CanvasControls';
import { useCanvasInteraction } from '../../hooks/useCanvasInteraction';
import {
  computeViewportTransform,
  transformLines,
} from '../../utils/coordinateScaling';
import { formatDimension } from '../../utils/dimensionParser';
import type { RenderLine } from '../../types';

interface FloorPlanCanvasProps {
  lines: RenderLine[];
  triageMode: boolean;
  highlightedLineId?: string | null;
  onLineClick?: (lineId: string) => void;
  className?: string;
}

export const FloorPlanCanvas: React.FC<FloorPlanCanvasProps> = ({
  lines,
  triageMode,
  highlightedLineId,
  onLineClick,
  className,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 });
  const [hoveredLineId, setHoveredLineId] = useState<string | null>(null);

  const interaction = useCanvasInteraction();

  // Observe container size changes
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        if (width > 0 && height > 0) {
          setDimensions({ width: Math.floor(width), height: Math.floor(height) });
        }
      }
    });

    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  // Compute viewport transform whenever lines or canvas dimensions change
  const viewportTransform = useMemo(
    () => computeViewportTransform(lines, dimensions.width, dimensions.height, 40, true),
    [lines, dimensions.width, dimensions.height]
  );

  // Update the interaction hook with the base transform
  useEffect(() => {
    interaction.setBaseTransform({
      scale: viewportTransform.scale,
      offsetX: viewportTransform.offsetX,
      offsetY: viewportTransform.offsetY,
    });
  }, [viewportTransform, interaction.setBaseTransform]);

  // Transform all lines to canvas coordinates
  const transformedLines = useMemo(
    () => transformLines(lines, viewportTransform, true),
    [lines, viewportTransform]
  );

  const handleLineClick = useCallback(
    (lineId: string) => {
      if (onLineClick) onLineClick(lineId);
    },
    [onLineClick]
  );

  return (
    <div ref={containerRef} className={`relative bg-slate-50 rounded-lg overflow-hidden ${className ?? ''}`}>
      {/* Canvas controls overlay */}
      <CanvasControls
        onZoomIn={interaction.zoomIn}
        onZoomOut={interaction.zoomOut}
        onFitToView={interaction.resetView}
        zoomLevel={interaction.scale * 100}
        lineCount={lines.length}
        conflictCount={lines.filter((l) => l.isConflicting).length}
        triageMode={triageMode}
      />

      {/* Info overlay */}
      <div className="absolute bottom-3 left-3 bg-white/90 backdrop-blur-sm rounded-md px-3 py-1.5 text-xs text-slate-500 font-mono z-10 shadow-sm">
        {lines.length} lines · {viewportTransform.scale.toFixed(4)} scale · {dimensions.width}×{dimensions.height}
      </div>

      {/* Konva Stage */}
      <Stage
        ref={interaction.stageRef}
        width={dimensions.width}
        height={dimensions.height}
        scaleX={interaction.scale}
        scaleY={interaction.scale}
        x={interaction.panX}
        y={interaction.panY}
        onWheel={interaction.handleWheel}
        draggable
        className="cursor-grab active:cursor-grabbing"
      >
        {/* Background */}
        <Layer>
          <Rect
            x={-10000}
            y={-10000}
            width={20000}
            height={20000}
            fill="#f8fafc"
            listening={false}
          />
        </Layer>

        {/* Lines layer */}
        <Layer>
          {transformedLines.map((line) => (
            <CanvasLine
              key={line.id}
              x1={line.canvasX1}
              y1={line.canvasY1}
              x2={line.canvasX2}
              y2={line.canvasY2}
              lineType={line.lineType}
              isConflicting={line.isConflicting}
              severity={line.severity}
              dimensionLabel={
                line.explicitDimension
                  ? line.explicitDimension
                  : line.dimensionValue !== undefined
                    ? formatDimension(line.dimensionValue, line.dimensionUnit)
                    : undefined
              }
              isSelected={hoveredLineId === line.lineId}
              isHighlighted={
                highlightedLineId === line.lineId ||
                (triageMode && line.isConflicting && highlightedLineId === null)
              }
              onClick={() => handleLineClick(line.lineId)}
              onMouseEnter={() => setHoveredLineId(line.lineId)}
              onMouseLeave={() => setHoveredLineId(null)}
            />
          ))}
        </Layer>
      </Stage>
    </div>
  );
};

FloorPlanCanvas.displayName = 'FloorPlanCanvas';