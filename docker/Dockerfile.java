# =============================================================================
# Dockerfile.java — Multi-stage build for the Dimension Audit Engine
# Spring Boot 3.2.5 / Java 21 / Maven
# =============================================================================
# Build context: project root
#   docker build -f docker/Dockerfile.java -t dimension-audit:latest .
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: Build the JAR with Maven
# ---------------------------------------------------------------------------
FROM maven:3.9-eclipse-temurin-21 AS builder

WORKDIR /build

# Copy Maven descriptor first to leverage Docker layer caching for dependencies
COPY services/dimension-audit/pom.xml .

# Download dependencies (cached layer unless pom.xml changes)
RUN mvn dependency:go-offline -B

# Copy source code
COPY services/dimension-audit/src ./src

# Build the executable JAR, skipping tests
RUN mvn clean package -DskipTests -B

# ---------------------------------------------------------------------------
# Stage 2: Slim runtime image
# ---------------------------------------------------------------------------
FROM eclipse-temurin:21-jre-alpine

LABEL maintainer="ArchIntel DevOps"
LABEL description="Dimension Audit Engine — Spring Boot Math Audit Service"
LABEL version="1.0.0"

# Install curl for health checks
RUN apk add --no-cache curl

# Create non-root user for security
RUN addgroup -S appgroup && adduser -S appuser -G appgroup

WORKDIR /app

# Copy the built JAR from the builder stage
COPY --from=builder /build/target/*.jar app.jar

# Create directory for temporary files
RUN mkdir -p /tmp/app && chown -R appuser:appgroup /app /tmp/app

USER appuser

# Expose the Spring Boot port
EXPOSE 8080

# Health check via Spring Boot Actuator
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -f http://localhost:8080/actuator/health || exit 1

# JVM options optimised for containers
ENV JAVA_OPTS="-XX:+UseContainerSupport \
               -XX:MaxRAMPercentage=75.0 \
               -XX:+UseG1GC \
               -Djava.security.egd=file:/dev/./urandom"

ENTRYPOINT ["sh", "-c", "java ${JAVA_OPTS} -jar app.jar"]