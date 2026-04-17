// =============================================================================
// Canvas Interaction Hook — Pan & Zoom State Management
// =============================================================================
// Manages the Konva Stage's zoom level and pan offset.
// Supports mouse wheel zoom (centered on cursor) and programmatic reset.
// =============================================================================

import { useCallback, useRef, useState } from 'react';
import type Konva from 'konva';

export interface CanvasInteractionState {
  /** Current scale (1.0 = fit-to-view) */
  scale: number;
  /** Current X pan offset */
  panX: number;
  /** Current Y pan offset */
  panY: number;
}

export interface CanvasInteractionActions {
  /** Handle wheel events for zoom */
  handleWheel: (e: Konva.KonvaEventObject<WheelEvent>) => void;
  /** Reset to fit-to-view transform */
  resetView: () => void;
  /** Zoom in by a factor */
  zoomIn: () => void;
  /** Zoom out by a factor */
  zoomOut: () => void;
  /** Update the base transform (from coordinate scaling) */
  setBaseTransform: (transform: { scale: number; offsetX: number; offsetY: number }) => void;
  /** Ref to the Konva stage */
  stageRef: React.RefObject<Konva.Stage | null>;
}

const ZOOM_SPEED = 1.08;
const MIN_ZOOM = 0.1;
const MAX_ZOOM = 50;

export function useCanvasInteraction(): CanvasInteractionState & CanvasInteractionActions {
  const stageRef = useRef<Konva.Stage | null>(null);
  const [scale, setScale] = useState(1);
  const [panX, setPanX] = useState(0);
  const [panY, setPanY] = useState(0);
  const baseTransformRef = useRef({ scale: 1, offsetX: 0, offsetY: 0 });

  const setBaseTransform = useCallback(
    (transform: { scale: number; offsetX: number; offsetY: number }) => {
      baseTransformRef.current = transform;
      // Reset the view to the new base transform
      setScale(1);
      setPanX(0);
      setPanY(0);
    },
    []
  );

  const handleWheel = useCallback(
    (e: Konva.KonvaEventObject<WheelEvent>) => {
      e.evt.preventDefault();

      const stage = stageRef.current;
      if (!stage) return;

      const oldScale = scale;
      const pointer = stage.getPointerPosition();
      if (!pointer) return;

      // Determine zoom direction
      const direction = e.evt.deltaY > 0 ? -1 : 1;
      const newScale = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, oldScale * ZOOM_SPEED ** direction));

      // Compute the new offset so zoom centers on the pointer position
      const mousePointTo = {
        x: (pointer.x - panX) / oldScale,
        y: (pointer.y - panY) / oldScale,
      };

      const newPanX = pointer.x - mousePointTo.x * newScale;
      const newPanY = pointer.y - mousePointTo.y * newScale;

      setScale(newScale);
      setPanX(newPanX);
      setPanY(newPanY);
    },
    [scale, panX, panY]
  );

  const resetView = useCallback(() => {
    setScale(1);
    setPanX(0);
    setPanY(0);
  }, []);

  const zoomIn = useCallback(() => {
    setScale((s) => Math.min(MAX_ZOOM, s * 1.2));
  }, []);

  const zoomOut = useCallback(() => {
    setScale((s) => Math.max(MIN_ZOOM, s / 1.2));
  }, []);

  return {
    scale,
    panX,
    panY,
    handleWheel,
    resetView,
    zoomIn,
    zoomOut,
    setBaseTransform,
    stageRef,
  };
}