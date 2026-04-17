package com.archintel.dimension.dto;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;

/**
 * Top-level request payload for the dimensional conflict audit endpoint.
 *
 * <p>Contains the scale factor, the full raw coordinates payload (with points,
 * lines, and dimension annotations), and an optional explicit dimensions override
 * array for cases where OCR'd text is provided separately from the coordinate
 * extraction pipeline.</p>
 *
 * <p>The service will iterate every line in rawCoordinates.lines, compute the
 * Euclidean distance between its endpoints, scale it by scaleFactor, and compare
 * against any matching explicit dimension (from dimensionAnnotations or the
 * explicitDimensions override) using a 0.5% tolerance threshold.</p>
 *
 * @param scaleFactor         Multiplier to convert pixel-space Euclidean distances
 *                            to real-world units. Must be positive.
 * @param rawCoordinates      The raw coordinates payload from the extraction stage.
 * @param tolerancePercentage Optional per-request tolerance override as a percentage
 *                            (e.g., 0.5 means 0.5%). Defaults to 0.5 if not provided.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record AuditRequest(
        @NotNull(message = "scaleFactor is required")
        @Positive(message = "scaleFactor must be positive")
        Double scaleFactor,

        @NotNull(message = "rawCoordinates is required")
        @Valid
        RawCoordinatesDto rawCoordinates,

        Double tolerancePercentage
) {
    /**
     * Returns the effective tolerance percentage, defaulting to 0.5% if not specified.
     */
    public double effectiveTolerancePercentage() {
        return tolerancePercentage != null ? tolerancePercentage : 0.5;
    }
}