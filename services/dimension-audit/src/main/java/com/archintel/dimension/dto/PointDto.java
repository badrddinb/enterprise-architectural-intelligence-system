package com.archintel.dimension.dto;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;

/**
 * Represents a single coordinate point extracted from an architectural plan.
 * Maps to the "points" array items in the raw-coordinates.schema.json data contract.
 *
 * @param pointId Unique identifier for this point (UUID).
 * @param x       X coordinate value.
 * @param y       Y coordinate value.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record PointDto(
        @NotBlank(message = "pointId is required")
        String pointId,

        @NotNull(message = "x coordinate is required")
        Double x,

        @NotNull(message = "y coordinate is required")
        Double y
) {
}