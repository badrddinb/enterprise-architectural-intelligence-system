package com.archintel.dimension.dto;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.time.OffsetDateTime;
import java.util.List;

/**
 * Top-level response payload for the dimensional conflict audit.
 *
 * <p>Contains the audit identifier, source lineage, processing statistics,
 * and the full list of detected dimensional conflicts.</p>
 *
 * @param auditId             Unique identifier for this audit run (UUID).
 * @param timestamp           ISO 8601 timestamp of when the audit was performed.
 * @param sourceCoordinatesId Reference to the RawCoordinates.coordinatesId that was audited.
 * @param sourceFileId        Reference to the UploadedFile.fileId (transitive lineage).
 * @param scaleFactor         The scale factor used in this audit.
 * @param tolerancePercentage The tolerance threshold percentage used.
 * @param status              Overall audit status: "CLEAN" (no conflicts), "CONFLICTS_DETECTED", or "NO_ANNOTATIONS".
 * @param statistics          Summary statistics of the audit run.
 * @param conflicts           Array of detected dimensional conflicts (empty if status is "CLEAN").
 */
@JsonInclude(JsonInclude.Include.ALWAYS)
public record AuditResponse(
        @JsonProperty("auditId") String auditId,
        @JsonProperty("timestamp") OffsetDateTime timestamp,
        @JsonProperty("sourceCoordinatesId") String sourceCoordinatesId,
        @JsonProperty("sourceFileId") String sourceFileId,
        @JsonProperty("scaleFactor") double scaleFactor,
        @JsonProperty("tolerancePercentage") double tolerancePercentage,
        @JsonProperty("status") String status,
        @JsonProperty("statistics") AuditStatistics statistics,
        @JsonProperty("conflicts") List<ConflictRecord> conflicts
) {
}