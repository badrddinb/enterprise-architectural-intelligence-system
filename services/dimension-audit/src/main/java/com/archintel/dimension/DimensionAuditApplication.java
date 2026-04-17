package com.archintel.dimension;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * Entry point for the Dimension Audit Engine Spring Boot application.
 *
 * <p>This microservice audits extracted coordinates against OCR'd text
 * to find dimensional conflicts in architectural plans. It exposes a
 * REST endpoint at {@code POST /api/v1/audit/dimensions} that accepts
 * a JSON payload with scale factor, raw coordinates, and dimension
 * annotations, and returns a detailed conflict report.</p>
 */
@SpringBootApplication
public class DimensionAuditApplication {

    public static void main(String[] args) {
        SpringApplication.run(DimensionAuditApplication.class, args);
    }
}