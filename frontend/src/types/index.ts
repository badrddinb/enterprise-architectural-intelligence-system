// =============================================================================
// TypeScript types derived from backend JSON schemas and Java DTOs
// Enterprise Architectural Intelligence System
// =============================================================================

// ---- Raw Coordinates Schema ----

export interface Point {
  pointId: string;
  x: number;
  y: number;
  z?: number;
  sourceZoneId: string;
  pointType: string;
  confidence?: number;
  tolerance?: number;
  pixelOrigin?: { px: number; py: number };
}

export interface ExtractedLine {
  lineId: string;
  startPointId: string;
  endPointId: string;
  lineType: string;
  measuredLength?: number;
  confidence?: number;
}

export interface DimensionAnnotation {
  annotationId: string;
  value: number;
  unit: 'mm' | 'cm' | 'm' | 'in' | 'ft';
  startPointId: string;
  endPointId: string;
  sourceZoneId: string;
  tolerance?: string;
  confidence?: number;
}

export interface CoordinateSystem {
  type: 'cartesian-2d' | 'cartesian-3d' | 'polar';
  units: 'millimeters' | 'centimeters' | 'meters' | 'inches' | 'feet';
  originX: number;
  originY: number;
  originZ?: number;
  rotationDegrees?: number;
}

export interface RawCoordinates {
  coordinatesId: string;
  sourceZonesId: string;
  sourceFileId: string;
  createdAt: string;
  extractedBy?: {
    method: string;
    version: string;
    modelId?: string;
  };
  coordinateSystem: CoordinateSystem;
  points: Point[];
  lines: ExtractedLine[];
  dimensionAnnotations: DimensionAnnotation[];
}

// ---- Dimension Audit (Java Spring Boot) DTOs ----

export interface AuditRequest {
  scaleFactor: number;
  tolerancePercentage: number;
  rawCoordinates: RawCoordinates;
}

export interface AuditResponse {
  auditId: string;
  timestamp: string;
  sourceCoordinatesId: string;
  sourceFileId: string;
  scaleFactor: number;
  tolerancePercentage: number;
  status: 'CLEAN' | 'CONFLICTS_DETECTED' | 'NO_ANNOTATIONS';
  statistics: AuditStatistics;
  conflicts: ConflictRecord[];
}

export interface AuditStatistics {
  totalLines: number;
  linesWithAnnotations: number;
  cleanLines: number;
  conflictingLines: number;
  maxDeviationPercentage: number;
  avgDeviationPercentage: number;
}

export interface ConflictRecord {
  conflictId: string;
  lineId: string;
  lineType: string;
  computedDistance: number;
  explicitDimension: number;
  scaleFactor: number;
  deviationAbsolute: number;
  deviationPercentage: number;
  toleranceThreshold: number;
  severity: 'critical' | 'warning' | 'info';
  annotationId: string;
  startPointId: string;
  endPointId: string;
  unit: string;
}

// ---- HITL Resolution ----

export type ConflictResolution = 'FORCE_GEOMETRY' | 'FORCE_TEXT';

export interface ResolvedConflict {
  conflictId: string;
  lineId: string;
  resolution: ConflictResolution;
  resolvedValue: number;
}

export interface ResolutionPayload {
  auditId: string;
  resolutions: ResolvedConflict[];
}

// ---- Certified Math Graph Schema ----

export interface GraphNode {
  nodeId: string;
  pointId: string;
  x: number;
  y: number;
  z?: number;
  nodeType: string;
  degree?: number;
  properties?: {
    isStructural?: boolean;
    isLoadBearing?: boolean;
    material?: string;
  };
}

export interface GraphEdge {
  edgeId: string;
  fromNodeId: string;
  toNodeId: string;
  edgeType: string;
  weight?: number;
  isDirected?: boolean;
  thickness?: number;
  properties?: {
    isStructural?: boolean;
    isLoadBearing?: boolean;
    fireRating?: number;
    material?: string;
  };
}

export interface GraphFace {
  faceId: string;
  boundaryEdgeIds: string[];
  area: number;
  perimeter?: number;
  faceType?: string;
  properties?: {
    occupancyType?: string;
    isFireRated?: boolean;
    isMeansOfEgress?: boolean;
  };
}

export interface CertifiedMathGraph {
  graphId: string;
  sourceCoordinatesId: string;
  sourceFileId: string;
  createdAt: string;
  topologyType?: 'planar' | 'non-planar';
  nodeCount: number;
  edgeCount: number;
  faceCount: number;
  nodes: GraphNode[];
  edges: GraphEdge[];
  faces: GraphFace[];
  certification: {
    isCertified: boolean;
    certifiedAt: string;
    certifiedBy: {
      userId: string;
      email: string;
      role?: string;
      licenseNumber?: string;
    };
    algorithmVersion: string;
    checksum?: string;
    checks: Array<{
      checkId: string;
      checkName: string;
      passed: boolean;
      description: string;
      severity?: 'critical' | 'warning' | 'info';
    }>;
  };
}

// ---- Compliance Report Schema ----

export interface ComplianceReport {
  reportId: string;
  graphId: string;
  sourceCoordinatesId: string;
  sourceFileId: string;
  createdAt: string;
  overallStatus: 'compliant' | 'non-compliant' | 'conditionally-compliant' | 'pending-review';
  complianceScore?: number;
  ruleSets: RuleSet[];
  violations: Violation[];
  statistics: ComplianceStatistics;
  approvedBy: {
    userId: string;
    email: string;
    role?: string;
    licenseNumber?: string;
    decision: 'approved' | 'rejected' | 'conditionally-approved' | 'requires-revision';
    reviewedAt?: string;
    comments?: string;
  };
  reportStorageUri?: string;
}

export interface RuleSet {
  ruleSetId: string;
  ruleSetName: string;
  version: string;
  jurisdiction?: string;
  status: 'compliant' | 'non-compliant' | 'conditionally-compliant' | 'not-applicable';
  rules: Rule[];
}

export interface Rule {
  ruleId: string;
  ruleCode: string;
  description: string;
  status: 'pass' | 'fail' | 'warning' | 'not-applicable' | 'manual-review-required';
  severity: 'critical' | 'major' | 'minor' | 'info';
  affectedElements?: Array<{
    elementType: 'node' | 'edge' | 'face';
    elementId: string;
    detail?: string;
  }>;
  measuredValue?: { value: number; unit: string };
  requiredValue?: { value: number; unit: string; comparison: string };
  remediation?: string;
}

export interface Violation {
  violationId: string;
  ruleCode: string;
  severity: 'critical' | 'major' | 'minor';
  category: string;
  description: string;
  location: {
    elementType: 'node' | 'edge' | 'face' | 'zone';
    elementId: string;
    faceIds?: string[];
  };
  remediation?: string;
}

export interface ComplianceStatistics {
  totalRulesEvaluated: number;
  rulesPassed: number;
  rulesFailed: number;
  rulesWarning: number;
  rulesNotApplicable: number;
  totalViolations: number;
  criticalViolations: number;
  majorViolations: number;
  minorViolations: number;
  totalSpaces?: number;
  totalArea?: number;
  areaUnit?: 'm2' | 'ft2';
}

// ---- Pipeline State ----

export type PipelineStage =
  | 'ingestion'
  | 'vector-raster-extraction'
  | 'spatial-linking'
  | 'math-audit'
  | 'langgraph-analysis';

export type PipelineStageStatus = 'pending' | 'running' | 'complete' | 'failed';

export interface PipelineState {
  stages: Record<PipelineStage, PipelineStageStatus>;
  currentStage: PipelineStage | null;
  executionArn: string | null;
  error: string | null;
}

// ---- Canvas Render Line (resolved for rendering) ----

export interface RenderLine {
  id: string;
  startX: number;
  startY: number;
  endX: number;
  endY: number;
  lineType: string;
  measuredLength?: number;
  explicitDimension?: string;
  dimensionValue?: number;
  dimensionUnit?: string;
  isConflicting: boolean;
  severity?: 'critical' | 'warning' | 'info';
  lineId: string;
  conflictId?: string;
}