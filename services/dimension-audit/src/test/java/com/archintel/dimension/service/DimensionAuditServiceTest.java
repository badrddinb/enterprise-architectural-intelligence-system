package com.archintel.dimension.service;

import com.archintel.dimension.dto.*;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import java.util.Collections;
import java.util.List;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.*;

/**
 * Comprehensive unit tests for {@link DimensionAuditService}.
 *
 * <p>Tests are organized into nested groups by scenario:</p>
 * <ul>
 *   <li>Euclidean distance computation</li>
 *   <li>Point-pair key canonicalization</li>
 *   <li>Severity classification</li>
 *   <li>Full audit pipeline — clean, conflicts, edge cases</li>
 * </ul>
 */
class DimensionAuditServiceTest {

    private DimensionAuditService service;

    // ── Shared test fixture IDs ──────────────────────────────────────────────
    private static final String COORDS_ID = "coord-001";
    private static final String FILE_ID = "file-001";

    private static final String POINT_A = "point-a";
    private static final String POINT_B = "point-b";
    private static final String POINT_C = "point-c";
    private static final String POINT_D = "point-d";

    @BeforeEach
    void setUp() {
        service = new DimensionAuditService();
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // Euclidean Distance Tests
    // ═══════════════════════════════════════════════════════════════════════════

    @Nested
    @DisplayName("Euclidean Distance Computation")
    class EuclideanDistanceTests {

        @Test
        @DisplayName("Horizontal line: distance equals delta-x")
        void horizontalLine() {
            double dist = DimensionAuditService.computeEuclideanDistance(0, 0, 10, 0);
            assertEquals(10.0, dist, 1e-9);
        }

        @Test
        @DisplayName("Vertical line: distance equals delta-y")
        void verticalLine() {
            double dist = DimensionAuditService.computeEuclideanDistance(0, 0, 0, 15);
            assertEquals(15.0, dist, 1e-9);
        }

        @Test
        @DisplayName("3-4-5 right triangle gives distance 5")
        void rightTriangle() {
            double dist = DimensionAuditService.computeEuclideanDistance(0, 0, 3, 4);
            assertEquals(5.0, dist, 1e-9);
        }

        @Test
        @DisplayName("Same point gives distance 0")
        void samePoint() {
            double dist = DimensionAuditService.computeEuclideanDistance(5, 5, 5, 5);
            assertEquals(0.0, dist, 1e-9);
        }

        @Test
        @DisplayName("Negative coordinates computed correctly")
        void negativeCoordinates() {
            double dist = DimensionAuditService.computeEuclideanDistance(-3, -4, 0, 0);
            assertEquals(5.0, dist, 1e-9);
        }

        @Test
        @DisplayName("Large coordinates do not overflow (hypot stability)")
        void largeCoordinates() {
            double large = 1e15;
            double dist = DimensionAuditService.computeEuclideanDistance(0, 0, large, 0);
            assertEquals(large, dist, large * 1e-9);
        }
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // Point-Pair Key Tests
    // ═══════════════════════════════════════════════════════════════════════════

    @Nested
    @DisplayName("Point-Pair Key Canonicalization")
    class PointPairKeyTests {

        @Test
        @DisplayName("(A, B) and (B, A) produce the same key")
        void bidirectionalEquality() {
            String key1 = DimensionAuditService.pointPairKey("aaa", "bbb");
            String key2 = DimensionAuditService.pointPairKey("bbb", "aaa");
            assertEquals(key1, key2);
        }

        @Test
        @DisplayName("Same point pair is idempotent")
        void samePoints() {
            String key = DimensionAuditService.pointPairKey("xxx", "xxx");
            assertEquals("xxx::xxx", key);
        }
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // Severity Classification Tests
    // ═══════════════════════════════════════════════════════════════════════════

    @Nested
    @DisplayName("Severity Classification")
    class SeverityTests {

        @Test
        @DisplayName("1% deviation → minor")
        void minorSeverity() {
            assertEquals("minor", DimensionAuditService.classifySeverity(1.0));
        }

        @Test
        @DisplayName("0.6% deviation → minor")
        void justAboveTolerance() {
            assertEquals("minor", DimensionAuditService.classifySeverity(0.6));
        }

        @Test
        @DisplayName("3% deviation → major")
        void majorSeverity() {
            assertEquals("major", DimensionAuditService.classifySeverity(3.0));
        }

        @Test
        @DisplayName("10% deviation → critical")
        void criticalSeverity() {
            assertEquals("critical", DimensionAuditService.classifySeverity(10.0));
        }

        @Test
        @DisplayName("5.001% deviation → critical")
        void justAboveCriticalThreshold() {
            assertEquals("critical", DimensionAuditService.classifySeverity(5.001));
        }

        @Test
        @DisplayName("2.001% deviation → major")
        void justAboveMajorThreshold() {
            assertEquals("major", DimensionAuditService.classifySeverity(2.001));
        }
    }

    // ═══════════════════════════════════════════════════════════════════════════
    // Full Audit Pipeline Tests
    // ═══════════════════════════════════════════════════════════════════════════

    @Nested
    @DisplayName("Full Audit Pipeline")
    class FullAuditTests {

        @Test
        @DisplayName("CLEAN: annotated line within 0.5% tolerance passes")
        void cleanAudit_withinTolerance() {
            // Points at (0,0) and (10,0) → distance = 10.0
            // Scale factor = 1.55, so scaled = 15.5
            // Explicit dimension = 15.5 → should pass
            var points = List.of(
                    new PointDto(POINT_A, 0.0, 0.0),
                    new PointDto(POINT_B, 10.0, 0.0)
            );
            var lines = List.of(
                    new LineDto("line-1", POINT_A, POINT_B, "wall")
            );
            var annotations = List.of(
                    new DimensionAnnotationDto("ann-1", 15.5, "m", POINT_A, POINT_B)
            );

            var rawCoords = new RawCoordinatesDto(COORDS_ID, FILE_ID, points, lines, annotations);
            var request = new AuditRequest(1.55, rawCoords, null);

            AuditResponse response = service.audit(request);

            assertEquals("CLEAN", response.status());
            assertEquals(0, response.conflicts().size());
            assertEquals(1, response.statistics().totalLinesAudited());
            assertEquals(1, response.statistics().linesWithAnnotations());
            assertEquals(0, response.statistics().linesWithoutAnnotations());
            assertEquals(1, response.statistics().passCount());
            assertEquals(1.0, response.statistics().passRate(), 1e-9);
        }

        @Test
        @DisplayName("CONFLICTS_DETECTED: annotated line exceeds 0.5% tolerance")
        void conflictDetected_exceedsTolerance() {
            // Points at (0,0) and (10,0) → distance = 10.0
            // Scale factor = 1.0, so scaled = 10.0
            // Explicit dimension = 15.5 → deviation = |10.0 - 15.5| / 15.5 = 35.48%
            var points = List.of(
                    new PointDto(POINT_A, 0.0, 0.0),
                    new PointDto(POINT_B, 10.0, 0.0)
            );
            var lines = List.of(
                    new LineDto("line-1", POINT_A, POINT_B, "wall")
            );
            var annotations = List.of(
                    new DimensionAnnotationDto("ann-1", 15.5, "m", POINT_A, POINT_B)
            );

            var rawCoords = new RawCoordinatesDto(COORDS_ID, FILE_ID, points, lines, annotations);
            var request = new AuditRequest(1.0, rawCoords, null);

            AuditResponse response = service.audit(request);

            assertEquals("CONFLICTS_DETECTED", response.status());
            assertEquals(1, response.conflicts().size());

            ConflictRecord conflict = response.conflicts().get(0);
            assertEquals("line-1", conflict.lineId());
            assertEquals("wall", conflict.lineType());
            assertEquals(10.0, conflict.computedDistance(), 1e-9);
            assertEquals(15.5, conflict.explicitDimension(), 1e-9);
            assertEquals(1.0, conflict.scaleFactor(), 1e-9);
            assertEquals(5.5, conflict.deviationAbsolute(), 1e-9);
            assertTrue(conflict.deviationPercentage() > 0.5);
            assertEquals("critical", conflict.severity());
            assertEquals("ann-1", conflict.annotationId());
            assertEquals("m", conflict.unit());
        }

        @Test
        @DisplayName("Minor conflict: deviation between 0.5% and 2%")
        void minorConflict() {
            // Points at (0,0) and (10,0) → distance = 10.0
            // Scale factor = 1.0, so scaled = 10.0
            // Explicit dimension = 10.1 → deviation = |10.0 - 10.1| / 10.1 = 0.99%
            var points = List.of(
                    new PointDto(POINT_A, 0.0, 0.0),
                    new PointDto(POINT_B, 10.0, 0.0)
            );
            var lines = List.of(
                    new LineDto("line-1", POINT_A, POINT_B, "wall")
            );
            var annotations = List.of(
                    new DimensionAnnotationDto("ann-1", 10.1, "m", POINT_A, POINT_B)
            );

            var rawCoords = new RawCoordinatesDto(COORDS_ID, FILE_ID, points, lines, annotations);
            var request = new AuditRequest(1.0, rawCoords, null);

            AuditResponse response = service.audit(request);

            assertEquals("CONFLICTS_DETECTED", response.status());
            assertEquals(1, response.conflicts().size());
            assertEquals("minor", response.conflicts().get(0).severity());
            assertEquals(1, response.statistics().minorCount());
        }

        @Test
        @DisplayName("NO_ANNOTATIONS: lines with no matching annotations")
        void noAnnotations() {
            var points = List.of(
                    new PointDto(POINT_A, 0.0, 0.0),
                    new PointDto(POINT_B, 10.0, 0.0)
            );
            var lines = List.of(
                    new LineDto("line-1", POINT_A, POINT_B, "wall")
            );

            var rawCoords = new RawCoordinatesDto(COORDS_ID, FILE_ID, points, lines, Collections.emptyList());
            var request = new AuditRequest(1.0, rawCoords, null);

            AuditResponse response = service.audit(request);

            assertEquals("NO_ANNOTATIONS", response.status());
            assertEquals(0, response.conflicts().size());
            assertEquals(0, response.statistics().linesWithAnnotations());
            assertEquals(1, response.statistics().linesWithoutAnnotations());
        }

        @Test
        @DisplayName("Mixed: some lines annotated, some not, some conflicting")
        void mixedScenario() {
            // Point A(0,0), B(10,0), C(0,10), D(10,10)
            var points = List.of(
                    new PointDto(POINT_A, 0.0, 0.0),
                    new PointDto(POINT_B, 10.0, 0.0),
                    new PointDto(POINT_C, 0.0, 10.0),
                    new PointDto(POINT_D, 10.0, 10.0)
            );

            // Line A→B = 10.0, Line B→D = 10.0, Line A→D = √200 ≈ 14.142
            var lines = List.of(
                    new LineDto("line-1", POINT_A, POINT_B, "wall"),       // annotated, matches
                    new LineDto("line-2", POINT_B, POINT_D, "wall"),       // annotated, conflict
                    new LineDto("line-3", POINT_A, POINT_D, "dimension-line") // no annotation
            );

            var annotations = List.of(
                    new DimensionAnnotationDto("ann-1", 10.0, "m", POINT_A, POINT_B), // exact match
                    new DimensionAnnotationDto("ann-2", 15.5, "m", POINT_B, POINT_D)  // conflict: 10.0 vs 15.5
            );

            var rawCoords = new RawCoordinatesDto(COORDS_ID, FILE_ID, points, lines, annotations);
            var request = new AuditRequest(1.0, rawCoords, null);

            AuditResponse response = service.audit(request);

            assertEquals("CONFLICTS_DETECTED", response.status());
            assertEquals(1, response.conflicts().size());
            assertEquals(3, response.statistics().totalLinesAudited());
            assertEquals(2, response.statistics().linesWithAnnotations());
            assertEquals(1, response.statistics().linesWithoutAnnotations());
            assertEquals(1, response.statistics().passCount());
            assertEquals(0.5, response.statistics().passRate(), 1e-9);
            assertEquals("line-2", response.conflicts().get(0).lineId());
        }

        @Test
        @DisplayName("Annotation matched bidirectionally (B→A matches annotation A→B)")
        void bidirectionalAnnotationMatch() {
            var points = List.of(
                    new PointDto(POINT_A, 0.0, 0.0),
                    new PointDto(POINT_B, 10.0, 0.0)
            );
            // Line goes B→A but annotation references A→B — should still match
            var lines = List.of(
                    new LineDto("line-1", POINT_B, POINT_A, "wall")
            );
            var annotations = List.of(
                    new DimensionAnnotationDto("ann-1", 10.0, "m", POINT_A, POINT_B)
            );

            var rawCoords = new RawCoordinatesDto(COORDS_ID, FILE_ID, points, lines, annotations);
            var request = new AuditRequest(1.0, rawCoords, null);

            AuditResponse response = service.audit(request);

            assertEquals("CLEAN", response.status());
            assertEquals(1, response.statistics().linesWithAnnotations());
        }

        @Test
        @DisplayName("Unresolved point reference → line skipped gracefully")
        void unresolvedPointReference() {
            var points = List.of(
                    new PointDto(POINT_A, 0.0, 0.0)
                    // POINT_B is missing
            );
            var lines = List.of(
                    new LineDto("line-1", POINT_A, POINT_B, "wall")
            );
            var annotations = List.of(
                    new DimensionAnnotationDto("ann-1", 10.0, "m", POINT_A, POINT_B)
            );

            var rawCoords = new RawCoordinatesDto(COORDS_ID, FILE_ID, points, lines, annotations);
            var request = new AuditRequest(1.0, rawCoords, null);

            AuditResponse response = service.audit(request);

            // Should not throw — line with unresolved points is skipped
            assertEquals(0, response.statistics().linesWithAnnotations());
            assertEquals(0, response.conflicts().size());
        }

        @Test
        @DisplayName("Custom tolerance override: 2% allows a 1% deviation")
        void customTolerance_passWithinWiderTolerance() {
            // Distance = 10.0, explicit = 10.1 → deviation ≈ 0.99%
            // With 2% tolerance, this should pass
            var points = List.of(
                    new PointDto(POINT_A, 0.0, 0.0),
                    new PointDto(POINT_B, 10.0, 0.0)
            );
            var lines = List.of(
                    new LineDto("line-1", POINT_A, POINT_B, "wall")
            );
            var annotations = List.of(
                    new DimensionAnnotationDto("ann-1", 10.1, "m", POINT_A, POINT_B)
            );

            var rawCoords = new RawCoordinatesDto(COORDS_ID, FILE_ID, points, lines, annotations);
            var request = new AuditRequest(1.0, rawCoords, 2.0); // 2% tolerance

            AuditResponse response = service.audit(request);

            assertEquals("CLEAN", response.status());
            assertEquals(2.0, response.tolerancePercentage());
            assertEquals(0, response.conflicts().size());
        }

        @Test
        @DisplayName("Empty lines array → clean audit with zero counts")
        void emptyLinesArray() {
            var points = List.of(new PointDto(POINT_A, 0.0, 0.0));

            var rawCoords = new RawCoordinatesDto(COORDS_ID, FILE_ID, points, Collections.emptyList(), Collections.emptyList());
            var request = new AuditRequest(1.0, rawCoords, null);

            AuditResponse response = service.audit(request);

            assertEquals("NO_ANNOTATIONS", response.status());
            assertEquals(0, response.statistics().totalLinesAudited());
            assertEquals(0, response.conflicts().size());
        }

        @Test
        @DisplayName("Response contains correct lineage identifiers")
        void correctLineageIdentifiers() {
            String expectedCoordsId = UUID.randomUUID().toString();
            String expectedFileId = UUID.randomUUID().toString();

            var points = List.of(new PointDto(POINT_A, 0.0, 0.0));
            var rawCoords = new RawCoordinatesDto(expectedCoordsId, expectedFileId, points, Collections.emptyList(), Collections.emptyList());
            var request = new AuditRequest(2.5, rawCoords, null);

            AuditResponse response = service.audit(request);

            assertEquals(expectedCoordsId, response.sourceCoordinatesId());
            assertEquals(expectedFileId, response.sourceFileId());
            assertEquals(2.5, response.scaleFactor(), 1e-9);
            assertNotNull(response.auditId());
            assertNotNull(response.timestamp());
        }

        @Test
        @DisplayName("Diagonal line: 3-4-5 triangle scaled correctly")
        void diagonalLineScaled() {
            // Points at (0,0) and (3,4) → Euclidean distance = 5.0
            // Scale factor = 2.0 → scaled distance = 10.0
            // Explicit = 10.0 → should match
            var points = List.of(
                    new PointDto(POINT_A, 0.0, 0.0),
                    new PointDto(POINT_B, 3.0, 4.0)
            );
            var lines = List.of(
                    new LineDto("line-1", POINT_A, POINT_B, "wall")
            );
            var annotations = List.of(
                    new DimensionAnnotationDto("ann-1", 10.0, "m", POINT_A, POINT_B)
            );

            var rawCoords = new RawCoordinatesDto(COORDS_ID, FILE_ID, points, lines, annotations);
            var request = new AuditRequest(2.0, rawCoords, null);

            AuditResponse response = service.audit(request);

            assertEquals("CLEAN", response.status());
            assertEquals(1, response.statistics().linesWithAnnotations());
            assertEquals(1, response.statistics().passCount());
        }

        @Test
        @DisplayName("Multiple conflicts produce correct severity counts")
        void multipleConflicts_severityCounts() {
            // Line 1: A→B distance=10.0, explicit=10.0 → PASS
            // Line 2: B→C distance=10.0, explicit=10.2 → deviation=1.96% → minor
            // Line 3: C→D distance=10.0, explicit=10.5 → deviation=4.76% → major
            // Line 4: A→D distance=sqrt(200)=14.142, explicit=10.0 → deviation=41.4% → critical
            var points = List.of(
                    new PointDto(POINT_A, 0.0, 0.0),
                    new PointDto(POINT_B, 10.0, 0.0),
                    new PointDto(POINT_C, 20.0, 0.0),
                    new PointDto(POINT_D, 20.0, 10.0)
            );
            var lines = List.of(
                    new LineDto("line-1", POINT_A, POINT_B, "wall"),
                    new LineDto("line-2", POINT_B, POINT_C, "wall"),
                    new LineDto("line-3", POINT_C, POINT_D, "wall"),
                    new LineDto("line-4", POINT_A, POINT_D, "dimension-line")
            );
            var annotations = List.of(
                    new DimensionAnnotationDto("ann-1", 10.0, "m", POINT_A, POINT_B),
                    new DimensionAnnotationDto("ann-2", 10.2, "m", POINT_B, POINT_C),
                    new DimensionAnnotationDto("ann-3", 10.5, "m", POINT_C, POINT_D),
                    new DimensionAnnotationDto("ann-4", 10.0, "m", POINT_A, POINT_D)
            );

            var rawCoords = new RawCoordinatesDto(COORDS_ID, FILE_ID, points, lines, annotations);
            var request = new AuditRequest(1.0, rawCoords, null);

            AuditResponse response = service.audit(request);

            assertEquals("CONFLICTS_DETECTED", response.status());
            assertEquals(3, response.conflicts().size());
            assertEquals(4, response.statistics().linesWithAnnotations());
            assertEquals(1, response.statistics().passCount());
            assertEquals(1, response.statistics().minorCount());
            assertEquals(1, response.statistics().majorCount());
            assertEquals(1, response.statistics().criticalCount());
        }
    }
}