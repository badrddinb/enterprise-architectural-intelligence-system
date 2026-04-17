package com.archintel.dimension.dto;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;

import java.util.List;

/**
 * Represents the full raw coordinates payload extracted from an architectural plan.
 * Maps to the raw-coordinates.schema.json data contract — the primary output of the
 * coordinate extraction stage and the core input for this dimension audit service.
 *
 * @param coordinatesId       Unique identifier for this coordinate extraction result.
 * @param sourceFileId        Reference to the UploadedFile.fileId.
 * @param points              Array of extracted coordinate points.
 * @param lines               Array of extracted lines connecting points.
 * @param dimensionAnnotations Array of dimension annotations extracted from the plan.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record RawCoordinatesDto(
        @NotBlank(message = "coordinatesId is required")
        String coordinatesId,

        @NotBlank(message = "sourceFileId is required")
        String sourceFileId,

        @NotEmpty(message = "points array must not be empty")
        @Valid
        List<PointDto> points,

        @NotNull(message = "lines array is required")
        @Valid
        List<LineDto> lines,

        @NotNull(message = "dimensionAnnotations array is required")
        @Valid
        List<DimensionAnnotationDto> dimensionAnnotations
) {
}