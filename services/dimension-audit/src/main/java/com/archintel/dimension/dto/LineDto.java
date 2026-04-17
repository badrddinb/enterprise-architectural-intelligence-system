package com.archintel.dimension.dto;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;

/**
 * Represents a line segment connecting two points in the architectural plan.
 * Maps to the "lines" array items in the raw-coordinates.schema.json data contract.
 *
 * @param lineId       Unique identifier for this line (UUID).
 * @param startPointId Reference to the starting point's pointId.
 * @param endPointId   Reference to the ending point's pointId.
 * @param lineType     Classification of the line (e.g., "wall", "dimension-line").
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record LineDto(
        @NotBlank(message = "lineId is required")
        String lineId,

        @NotBlank(message = "startPointId is required")
        String startPointId,

        @NotBlank(message = "endPointId is required")
        String endPointId,

        @NotNull(message = "lineType is required")
        String lineType
) {
}