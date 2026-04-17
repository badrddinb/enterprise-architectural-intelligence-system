package com.archintel.dimension.service;

import com.archintel.dimension.dto.*;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.time.OffsetDateTime;
import java.util.*;
import java.util.stream.Collectors;

/**
 * Core dimension audit engine that detects dimensional conflicts between
 * computed Euclidean distances and architect-specified explicit dimensions.
 *
 * <h3>Processing Algorithm</h3>
 * <ol>
 *   <li>Build a lookup map of all points by pointId for O(1) resolution.</li>
 *   <li>Build a lookup map linking dimension annotations to line point-pairs.</li>
 *   <li>Iterate every line in rawCoordinates.lines.</li>
 *   <li>Compute Euclidean distance: √((x2−x1)² + (y2−y1)²) using {@link Math#hypot}.</li>
 *   <li>Multiply by scaleFactor to convert to real-world units.</li>
 *   <li>If a matching explicit dimension exists, compare using relative tolerance.</li>
 *   <li>If deviation exceeds tolerance, emit a {@link ConflictRecord}.</li>
 * </ol>
 *
 * <h3>Tolerance Rule</h3>
 * <p>A conflict is flagged when:<br>
 * {@code |computedDistance − explicitDimension| / explicitDimension > tolerancePercentage / 100.0}
 * </p>
 *
 * <h3>Severity Classification</h3>
 * <ul>
 *   <li>{@code minor} — deviation between 0.5% and 2%</li>
 *   <li>{@code major} — deviation between 2% and 5%</li>
 *   <li>{@code critical} — deviation above 5%</li>
 * </ul>
 */
@Service
public class DimensionAuditService {

    private static final Logger log = LoggerFactory.getLogger(DimensionAuditService.class);

    private static final double DEFAULT_TOLERANCE_PERCENTAGE = 0.5;
    private static final double MAJOR_SEVERITY_THRESHOLD = 2.0;
    private static final double CRITICAL_SEVERITY_THRESHOLD = 5.0;

    /**
     * Executes the full dimensional audit against the provided request payload.
     *
     * @param request The audit request containing scaleFactor, rawCoordinates,
     *                and optional tolerance override.
     * @return An {@link AuditResponse} with statistics and any detected conflicts.
     * @throws IllegalArgumentException if point references in lines cannot be resolved.
     */
    public AuditResponse audit(AuditRequest request) {
        RawCoordinatesDto rawCoords = request.rawCoordinates();
        double scaleFactor = request.scaleFactor();
        double tolerancePct = request.effectiveTolerancePercentage();

        log.info("Starting dimensional audit: coordinatesId={}, lines={}, annotations={}, scaleFactor={}, tolerance={}%",
                rawCoords.coordinatesId(),
                rawCoords.lines().size(),
                rawCoords.dimensionAnnotations().size(),
                scaleFactor,
                tolerancePct);

        // ── Stage 1: Build point lookup map ──────────────────────────────────
        Map<String, PointDto> pointMap = rawCoords.points().stream()
                .collect(Collectors.toMap(
                        PointDto::pointId,
                        p -> p,
                        (existing, replacement) -> existing // deduplicate on first occurrence
                ));

        // ── Stage 2: Build annotation lookup by point-pair key ───────────────
        // Key: sorted(pointA, pointB) → allows matching regardless of direction
        Map<String, DimensionAnnotationDto> annotationMap = rawCoords.dimensionAnnotations().stream()
                .collect(Collectors.toMap(
                        ann -> pointPairKey(ann.startPointId(), ann.endPointId()),
                        ann -> ann,
                        (existing, replacement) -> existing // if multiple annotations match, keep first
                ));

        // ── Stage 3: Iterate every line and audit ───────────────────────────
        List<ConflictRecord> conflicts = new ArrayList<>();
        int linesWithAnnotations = 0;
        int linesWithoutAnnotations = 0;
        int passCount = 0;
        int criticalCount = 0;
        int majorCount = 0;
        int minorCount = 0;

        for (LineDto line : rawCoords.lines()) {
            // Resolve start and end points
            PointDto startPoint = pointMap.get(line.startPointId());
            PointDto endPoint = pointMap.get(line.endPointId());

            if (startPoint == null || endPoint == null) {
                log.warn("Line {} references unresolved points: start={}, end={} — skipping",
                        line.lineId(), line.startPointId(), line.endPointId());
                linesWithoutAnnotations++;
                continue;
            }

            // ── Stage 4: Compute Euclidean distance ──────────────────────────
            double euclideanDistance = computeEuclideanDistance(
                    startPoint.x(), startPoint.y(),
                    endPoint.x(), endPoint.y()
            );

            // ── Stage 5: Scale by scaleFactor ────────────────────────────────
            double scaledDistance = euclideanDistance * scaleFactor;

            // ── Stage 6: Look up matching explicit dimension ─────────────────
            String pairKey = pointPairKey(line.startPointId(), line.endPointId());
            DimensionAnnotationDto annotation = annotationMap.get(pairKey);

            if (annotation == null) {
                // No explicit dimension for this line — not an error, just unannotated
                linesWithoutAnnotations++;
                continue;
            }

            linesWithAnnotations++;

            // ── Stage 7: Compare scaled distance against explicit dimension ──
            double explicitValue = annotation.value();
            double deviationAbsolute = Math.abs(scaledDistance - explicitValue);
            double deviationPercentage = (explicitValue != 0.0)
                    ? (deviationAbsolute / explicitValue) * 100.0
                    : (scaledDistance != 0.0 ? Double.POSITIVE_INFINITY : 0.0);

            double toleranceThreshold = tolerancePct;

            if (deviationPercentage > toleranceThreshold) {
                // ── CONFLICT DETECTED ────────────────────────────────────────
                String severity = classifySeverity(deviationPercentage);

                ConflictRecord conflict = new ConflictRecord(
                        UUID.randomUUID().toString(),
                        line.lineId(),
                        line.lineType(),
                        scaledDistance,
                        explicitValue,
                        scaleFactor,
                        deviationAbsolute,
                        deviationPercentage,
                        toleranceThreshold,
                        severity,
                        annotation.annotationId(),
                        line.startPointId(),
                        line.endPointId(),
                        annotation.unit()
                );

                conflicts.add(conflict);

                switch (severity) {
                    case "critical" -> criticalCount++;
                    case "major" -> majorCount++;
                    case "minor" -> minorCount++;
                }

                log.debug("Conflict detected: lineId={}, computed={}, explicit={}, deviation={}%",
                        line.lineId(), scaledDistance, explicitValue,
                        String.format("%.4f", deviationPercentage));
            } else {
                passCount++;
            }
        }

        // ── Stage 8: Build statistics and response ───────────────────────────
        int totalLinesAudited = rawCoords.lines().size();
        double passRate = linesWithAnnotations > 0
                ? (double) passCount / linesWithAnnotations
                : 1.0;

        String status;
        if (linesWithAnnotations == 0) {
            status = "NO_ANNOTATIONS";
        } else if (conflicts.isEmpty()) {
            status = "CLEAN";
        } else {
            status = "CONFLICTS_DETECTED";
        }

        AuditStatistics statistics = new AuditStatistics(
                totalLinesAudited,
                linesWithAnnotations,
                linesWithoutAnnotations,
                conflicts.size(),
                passCount,
                passRate,
                criticalCount,
                majorCount,
                minorCount
        );

        log.info("Audit complete: status={}, totalLines={}, annotated={}, conflicts={} (critical={}, major={}, minor={})",
                status, totalLinesAudited, linesWithAnnotations, conflicts.size(),
                criticalCount, majorCount, minorCount);

        return new AuditResponse(
                UUID.randomUUID().toString(),
                OffsetDateTime.now(),
                rawCoords.coordinatesId(),
                rawCoords.sourceFileId(),
                scaleFactor,
                tolerancePct,
                status,
                statistics,
                conflicts
        );
    }

    /**
     * Computes the Euclidean distance between two 2D points using
     * {@link Math#hypot(double, double)} for numerical stability against
     * catastrophic cancellation.
     *
     * @param x1 X coordinate of the first point.
     * @param y1 Y coordinate of the first point.
     * @param x2 X coordinate of the second point.
     * @param y2 Y coordinate of the second point.
     * @return The Euclidean distance √((x2−x1)² + (y2−y1)²).
     */
    static double computeEuclideanDistance(double x1, double y1, double x2, double y2) {
        return Math.hypot(x2 - x1, y2 - y1);
    }

    /**
     * Creates a canonical key for a pair of point IDs by sorting them.
     * This ensures that (A, B) and (B, A) produce the same key, allowing
     * bidirectional matching between lines and annotations.
     *
     * @param pointA First point ID.
     * @param pointB Second point ID.
     * @return A canonical string key for the point pair.
     */
    static String pointPairKey(String pointA, String pointB) {
        return pointA.compareTo(pointB) <= 0
                ? pointA + "::" + pointB
                : pointB + "::" + pointA;
    }

    /**
     * Classifies the severity of a dimensional conflict based on the
     * deviation percentage.
     *
     * @param deviationPercentage The deviation as a percentage of the explicit value.
     * @return "critical" (>5%), "major" (2-5%), or "minor" (threshold to 2%).
     */
    static String classifySeverity(double deviationPercentage) {
        if (deviationPercentage > CRITICAL_SEVERITY_THRESHOLD) {
            return "critical";
        } else if (deviationPercentage > MAJOR_SEVERITY_THRESHOLD) {
            return "major";
        } else {
            return "minor";
        }
    }
}