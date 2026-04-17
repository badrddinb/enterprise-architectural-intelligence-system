package com.archintel.dimension.dto;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * Summary statistics of the dimensional audit run.
 *
 * @param totalLinesAudited    Total number of lines processed from rawCoordinates.
 * @param linesWithAnnotations Number of lines that had a matching explicit dimension annotation.
 * @param linesWithoutAnnotations Number of lines with no matching annotation (skipped from conflict check).
 * @param conflictCount        Number of conflicts detected.
 * @param passCount            Number of annotated lines that passed (within tolerance).
 * @param passRate             Ratio of passCount / linesWithAnnotations (0.0 to 1.0).
 * @param criticalCount        Number of conflicts with "critical" severity (>5% deviation).
 * @param majorCount           Number of conflicts with "major" severity (2-5% deviation).
 * @param minorCount           Number of conflicts with "minor" severity (0.5-2% deviation).
 */
@JsonInclude(JsonInclude.Include.ALWAYS)
public record AuditStatistics(
        @JsonProperty("totalLinesAudited") int totalLinesAudited,
        @JsonProperty("linesWithAnnotations") int linesWithAnnotations,
        @JsonProperty("linesWithoutAnnotations") int linesWithoutAnnotations,
        @JsonProperty("conflictCount") int conflictCount,
        @JsonProperty("passCount") int passCount,
        @JsonProperty("passRate") double passRate,
        @JsonProperty("criticalCount") int criticalCount,
        @JsonProperty("majorCount") int majorCount,
        @JsonProperty("minorCount") int minorCount
) {
}