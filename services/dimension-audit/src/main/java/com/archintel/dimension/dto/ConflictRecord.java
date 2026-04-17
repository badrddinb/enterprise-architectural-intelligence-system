package com.archintel.dimension.dto;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * Represents a single dimensional conflict detected during the audit.
 *
 * <p>A conflict is created when the computed Euclidean distance (scaled by
 * scaleFactor) for a line does not match the architect's explicit dimension
 * annotation within the allowed tolerance threshold.</p>
 *
 * @param conflictId           Unique identifier for this conflict (UUID).
 * @param lineId               The lineId where the conflict was detected.
 * @param lineType             Classification of the conflicting line.
 * @param computedDistance      The Euclidean distance scaled by scaleFactor.
 * @param explicitDimension    The architect's explicit dimension value (from OCR).
 * @param scaleFactor          The scale factor applied during computation.
 * @param deviationAbsolute    Absolute difference |computed - explicit|.
 * @param deviationPercentage  Relative difference as a percentage of the explicit value.
 * @param toleranceThreshold   The tolerance threshold used (percentage).
 * @param severity             Severity classification based on deviation magnitude.
 * @param annotationId         Reference to the dimension annotation that conflicts.
 * @param startPointId         Start point of the conflicting line.
 * @param endPointId           End point of the conflicting line.
 * @param unit                 Unit of the dimension annotation.
 */
@JsonInclude(JsonInclude.Include.ALWAYS)
public record ConflictRecord(
        @JsonProperty("conflictId") String conflictId,
        @JsonProperty("lineId") String lineId,
        @JsonProperty("lineType") String lineType,
        @JsonProperty("computedDistance") double computedDistance,
        @JsonProperty("explicitDimension") double explicitDimension,
        @JsonProperty("scaleFactor") double scaleFactor,
        @JsonProperty("deviationAbsolute") double deviationAbsolute,
        @JsonProperty("deviationPercentage") double deviationPercentage,
        @JsonProperty("toleranceThreshold") double toleranceThreshold,
        @JsonProperty("severity") String severity,
        @JsonProperty("annotationId") String annotationId,
        @JsonProperty("startPointId") String startPointId,
        @JsonProperty("endPointId") String endPointId,
        @JsonProperty("unit") String unit
) {
}