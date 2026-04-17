// =============================================================================
// Coordinate Scaling & Viewport Transform Utility
// =============================================================================
// Takes raw architectural coordinates (potentially in any coordinate system)
// and computes the affine transform needed to fit them within a Konva canvas
// viewport while preserving aspect ratio.
//
// Mathematical approach:
//   1. Compute bounding box of all points: { minX, minY, maxX, maxY }
//   2. Determine data extent: dataWidth = maxX - minX, dataHeight = maxY - minY
//   3. Compute uniform scale: scale = min(viewportW/dataW, viewportH/dataH)
//   4. Center the drawing: compute offsets to center within the padded viewport
//   5. Handle degenerate cases (single point, horizontal-only, vertical-only lines)
//   6. Flip Y axis: architectural drawings use Y-up, canvas uses Y-down
// =============================================================================

import type { RenderLine } from '../types';

export interface BoundingBox {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
  width: number;
  height: number;
}

export interface ViewportTransform {
  /** Uniform scale factor to fit data in viewport */
  scale: number;
  /** X offset to center the drawing in the viewport */
  offsetX: number;
  /** Y offset to center the drawing in the viewport */
  offsetY: number;
  /** The computed bounding box of the input data */
  boundingBox: BoundingBox;
}

/**
 * Compute the axis-aligned bounding box of a set of render lines.
 * Examines all start/end coordinates to find the extremes.
 *
 * @param lines - Array of RenderLine objects with startX/Y and endX/Y
 * @returns BoundingBox with min/max extents and dimensions
 */
export function computeBoundingBox(lines: RenderLine[]): BoundingBox {
  if (lines.length === 0) {
    return { minX: 0, minY: 0, maxX: 1, maxY: 1, width: 1, height: 1 };
  }

  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;

  for (const line of lines) {
    // Track extremes across all line endpoints
    minX = Math.min(minX, line.startX, line.endX);
    minY = Math.min(minY, line.startY, line.endY);
    maxX = Math.max(maxX, line.startX, line.endX);
    maxY = Math.max(maxY, line.startY, line.endY);
  }

  // Handle degenerate cases where all points are collinear
  const width = Math.max(maxX - minX, 0.001);
  const height = Math.max(maxY - minY, 0.001);

  return { minX, minY, maxX, maxY, width, height };
}

/**
 * Compute the viewport transform to fit all lines within a canvas of given
 * dimensions, with optional padding and Y-axis flip.
 *
 * The algorithm:
 *   1. Calculate bounding box of all coordinates
 *   2. Compute independent X and Y scale factors
 *   3. Choose the MINIMUM scale (uniform) to preserve aspect ratio
 *   4. Calculate centering offsets within the padded viewport area
 *   5. If flipY is true, invert Y coordinates so architectural Y-up maps to
 *      canvas Y-down (offsetY accounts for the reflection)
 *
 * @param lines        - Array of RenderLine to display
 * @param canvasWidth  - Width of the Konva stage in pixels
 * @param canvasHeight - Height of the Konva stage in pixels
 * @param padding      - Margin around the drawing in pixels (default: 40)
 * @param flipY        - Whether to flip Y axis (default: true, architectural convention)
 * @returns ViewportTransform with scale, offsets, and bounding box
 */
export function computeViewportTransform(
  lines: RenderLine[],
  canvasWidth: number,
  canvasHeight: number,
  padding: number = 40,
  flipY: boolean = true
): ViewportTransform {
  const boundingBox = computeBoundingBox(lines);

  const availableWidth = canvasWidth - 2 * padding;
  const availableHeight = canvasHeight - 2 * padding;

  // Guard against zero-dimension viewports
  const safeAvailW = Math.max(availableWidth, 1);
  const safeAvailH = Math.max(availableHeight, 1);

  // Compute scale factors independently, then take the minimum for uniform scaling
  const scaleX = safeAvailW / boundingBox.width;
  const scaleY = safeAvailH / boundingBox.height;
  const scale = Math.min(scaleX, scaleY);

  // Center the drawing within the available area
  // After scaling, the drawing may not fill the full available space in one dimension
  const scaledWidth = boundingBox.width * scale;
  const scaledHeight = boundingBox.height * scale;
  const centerOffsetX = (safeAvailW - scaledWidth) / 2;
  const centerOffsetY = (safeAvailH - scaledHeight) / 2;

  let offsetX: number;
  let offsetY: number;

  if (flipY) {
    // For Y-flip: we negate Y in the transform function, so the data Y range
    // needs to be reflected. The offset maps the data's maxY to the top of the
    // viewport area, and minY to the bottom.
    offsetX = padding + centerOffsetX - boundingBox.minX * scale;
    offsetY = padding + centerOffsetY + boundingBox.maxY * scale;
  } else {
    // No flip: standard mapping
    offsetX = padding + centerOffsetX - boundingBox.minX * scale;
    offsetY = padding + centerOffsetY - boundingBox.minY * scale;
  }

  return { scale, offsetX, offsetY, boundingBox };
}

/**
 * Transform a single point from data coordinates to canvas (screen) coordinates.
 *
 * @param pointX    - X coordinate in data space
 * @param pointY    - Y coordinate in data space
 * @param transform - The viewport transform computed by computeViewportTransform
 * @param flipY     - Whether Y axis is flipped (must match the transform's flipY)
 * @returns [canvasX, canvasY] tuple in pixel coordinates
 */
export function transformPoint(
  pointX: number,
  pointY: number,
  transform: ViewportTransform,
  flipY: boolean = true
): [number, number] {
  const canvasX = pointX * transform.scale + transform.offsetX;
  const canvasY = flipY
    ? -pointY * transform.scale + transform.offsetY
    : pointY * transform.scale + transform.offsetY;

  return [canvasX, canvasY];
}

/**
 * Transform all render lines into canvas-ready coordinate arrays.
 * Returns a new array where each line's start/end have been converted to pixel coords.
 *
 * @param lines     - Array of RenderLine in data coordinates
 * @param transform - Viewport transform
 * @param flipY     - Whether to flip Y axis
 * @returns Array of objects with canvasX1/Y1/X2/Y2 plus original line metadata
 */
export function transformLines(
  lines: RenderLine[],
  transform: ViewportTransform,
  flipY: boolean = true
): Array<RenderLine & { canvasX1: number; canvasY1: number; canvasX2: number; canvasY2: number }> {
  return lines.map((line) => {
    const [canvasX1, canvasY1] = transformPoint(line.startX, line.startY, transform, flipY);
    const [canvasX2, canvasY2] = transformPoint(line.endX, line.endY, transform, flipY);
    return { ...line, canvasX1, canvasY1, canvasX2, canvasY2 };
  });
}

/**
 * Compute the midpoint and perpendicular offset for placing dimension text
 * near a line segment. The text is positioned above/below the line at a
 * fixed offset, rotated to align with the line direction.
 *
 * @param x1 - Canvas X of line start
 * @param y1 - Canvas Y of line start
 * @param x2 - Canvas X of line end
 * @param y2 - Canvas Y of line end
 * @param offsetDistance - Perpendicular offset from line in pixels (default: -15 for above)
 * @returns { midX, midY, rotation } for text placement
 */
export function computeDimensionLabelPlacement(
  x1: number,
  y1: number,
  x2: number,
  y2: number,
  offsetDistance: number = -15
): { midX: number; midY: number; rotation: number } {
  const midX = (x1 + x2) / 2;
  const midY = (y1 + y2) / 2;

  // Line direction angle in radians (canvas coordinates, Y-down)
  const dx = x2 - x1;
  const dy = y2 - y1;
  let angle = Math.atan2(dy, dx);

  // Keep text readable (don't render upside down)
  if (angle > Math.PI / 2) angle -= Math.PI;
  if (angle < -Math.PI / 2) angle += Math.PI;

  // Perpendicular direction (rotated -90° for "above" the line)
  const perpX = -Math.sin(angle);
  const perpY = Math.cos(angle);

  return {
    midX: midX + perpX * offsetDistance,
    midY: midY + perpY * offsetDistance,
    rotation: (angle * 180) / Math.PI,
  };
}

/**
 * Compute the Euclidean distance between two points (in data coordinates).
 * Useful for displaying the "drawn" length on canvas.
 */
export function euclideanDistance(
  x1: number,
  y1: number,
  x2: number,
  y2: number
): number {
  const dx = x2 - x1;
  const dy = y2 - y1;
  return Math.sqrt(dx * dx + dy * dy);
}