package com.archintel.dimension.dto;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;

/**
 * Represents a dimension annotation extracted via OCR from the architectural plan.
 * Maps to the "dimensionAnnotations" array items in the raw-coordinates.schema.json
 * data contract. These are the explicit dimensions written by the architect (e.g., "15.5").
 *
 * @param annotationId Unique identifier for this dimension annotation (UUID).
 * @param value        The numeric dimension value (e.g., 15.5).
 * @param unit         Unit of the dimension value (e.g., "m", "ft", "cm").
 * @param startPointId Reference to the starting point of the dimension.
 * @param endPointId   Reference to the ending point of the dimension.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record DimensionAnnotationDto(
        @NotBlank(message = "annotationId is required")
        String annotationId,

        @NotNull(message = "value is required")
        @Positive(message = "value must be positive")
        Double value,

        @NotBlank(message = "unit is required")
        String unit,

        @NotBlank(message = "startPointId is required")
        String startPointId,

        @NotBlank(message = "endPointId is required")
        String endPointId
) {
}