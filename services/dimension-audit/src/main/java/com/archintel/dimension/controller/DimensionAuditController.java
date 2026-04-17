package com.archintel.dimension.controller;

import com.archintel.dimension.dto.AuditRequest;
import com.archintel.dimension.dto.AuditResponse;
import com.archintel.dimension.service.DimensionAuditService;
import jakarta.validation.Valid;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

/**
 * REST controller exposing the dimensional conflict audit endpoint.
 *
 * <p>Provides a single POST endpoint that accepts the full audit request
 * payload (scale factor, raw coordinates with points/lines/annotations)
 * and returns a detailed conflict report.</p>
 *
 * <h3>Endpoint</h3>
 * <pre>
 * POST /api/v1/audit/dimensions
 * Content-Type: application/json
 * </pre>
 */
@RestController
@RequestMapping("/api/v1/audit")
public class DimensionAuditController {

    private static final Logger log = LoggerFactory.getLogger(DimensionAuditController.class);

    private final DimensionAuditService auditService;

    public DimensionAuditController(DimensionAuditService auditService) {
        this.auditService = auditService;
    }

    /**
     * Executes a dimensional conflict audit against the provided payload.
     *
     * <p>Iterates every line in the raw coordinates, computes Euclidean distances,
     * scales them, and compares against any matching explicit dimension annotations.
     * Returns a detailed report with conflict records and summary statistics.</p>
     *
     * @param request The audit request payload (validated via Jakarta Bean Validation).
     * @return {@link ResponseEntity} containing the {@link AuditResponse}.
     */
    @PostMapping("/dimensions")
    public ResponseEntity<AuditResponse> auditDimensions(
            @Valid @RequestBody AuditRequest request) {

        log.info("Received dimension audit request: coordinatesId={}, scaleFactor={}",
                request.rawCoordinates().coordinatesId(),
                request.scaleFactor());

        AuditResponse response = auditService.audit(request);

        log.info("Dimension audit completed: auditId={}, status={}, conflicts={}",
                response.auditId(),
                response.status(),
                response.conflicts().size());

        return ResponseEntity.ok(response);
    }

    /**
     * Health check endpoint for the dimension audit service.
     *
     * @return A simple status message.
     */
    @GetMapping("/dimensions/health")
    public ResponseEntity<HealthStatus> health() {
        return ResponseEntity.ok(new HealthStatus("UP", "dimension-audit", "1.0.0"));
    }

    /**
     * Simple health status record.
     */
    record HealthStatus(String status, String service, String version) {
    }
}