// =============================================================================
// CanvasLine — Individual line segment with dimension label for react-konva
// =============================================================================

import React from 'react';
import { Line, Text, Group } from 'react-konva';
import { computeDimensionLabelPlacement } from '../../utils/coordinateScaling';

interface CanvasLineProps {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  lineType: string;
  isConflicting: boolean;
  severity?: 'critical' | 'warning' | 'info';
  dimensionLabel?: string;
  isSelected?: boolean;
  isHighlighted?: boolean;
  onClick?: () => void;
  onMouseEnter?: () => void;
  onMouseLeave?: () => void;
}

const LINE_COLORS: Record<string, string> = {
  wall: '#334155',
  'dimension-line': '#6366f1',
  'grid-line': '#94a3b8',
  'center-line': '#94a3b8',
  'hidden-line': '#cbd5e1',
  default: '#475569',
};

const CONFLICT_COLORS: Record<string, string> = {
  critical: '#dc2626',
  warning: '#f59e0b',
  info: '#3b82f6',
};

export const CanvasLine: React.FC<CanvasLineProps> = React.memo(({
  x1,
  y1,
  x2,
  y2,
  lineType,
  isConflicting,
  severity,
  dimensionLabel,
  isSelected,
  isHighlighted,
  onClick,
  onMouseEnter,
  onMouseLeave,
}) => {
  const strokeColor = isConflicting
    ? CONFLICT_COLORS[severity ?? 'critical']
    : LINE_COLORS[lineType] ?? LINE_COLORS.default;

  const strokeWidth = isConflicting
    ? 3
    : lineType === 'wall'
      ? 2
      : 1;

  const dashPattern = lineType === 'hidden-line' ? [8, 4] : undefined;

  // Glow effect for selected/highlighted lines
  const showGlow = isSelected || isHighlighted;

  // Compute dimension label placement
  const labelPlacement = dimensionLabel
    ? computeDimensionLabelPlacement(x1, y1, x2, y2, -18)
    : null;

  return (
    <Group>
      {/* Shadow/glow for highlighted lines */}
      {showGlow && (
        <Line
          points={[x1, y1, x2, y2]}
          stroke={isConflicting ? '#fca5a5' : '#60a5fa'}
          strokeWidth={strokeWidth + 6}
          opacity={0.3}
          listening={false}
        />
      )}
      {/* Main line */}
      <Line
        points={[x1, y1, x2, y2]}
        stroke={strokeColor}
        strokeWidth={strokeWidth}
        dash={dashPattern}
        lineCap="round"
        lineJoin="round"
        hitStrokeWidth={12}
        onClick={onClick}
        onTap={onClick}
        onMouseEnter={onMouseEnter}
        onMouseLeave={onMouseLeave}
      />
      {/* Dimension label */}
      {labelPlacement && dimensionLabel && (
        <Text
          x={labelPlacement.midX - 50}
          y={labelPlacement.midY - 8}
          width={100}
          text={dimensionLabel}
          fontSize={11}
          fontFamily="monospace"
          fill={isConflicting ? CONFLICT_COLORS[severity ?? 'critical'] : '#475569'}
          align="center"
          verticalAlign="middle"
          rotation={labelPlacement.rotation}
          offsetX={0}
          offsetY={0}
          padding={2}
          listening={false}
        />
      )}
    </Group>
  );
});

CanvasLine.displayName = 'CanvasLine';