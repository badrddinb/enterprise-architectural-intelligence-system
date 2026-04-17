// =============================================================================
// Dimension Parser Utility
// =============================================================================
// Parses architectural dimension strings like "15'-0\"", "3.5m", "12 1/2\"" 
// into numeric values. Handles feet-inches, metric, and fractional formats.
// =============================================================================

/**
 * Parse an architectural dimension string to a numeric value.
 * Supports:
 *   - Feet-inches: "15'-0\"", "15' 6\"", "15'-6 1/2\""
 *   - Decimal feet: "15.5'"
 *   - Metric: "3.5m", "350cm", "3500mm"
 *   - Inches: "18\"", "18.5\""
 *   - Plain numbers: "15.0"
 *
 * @param dim - The dimension string to parse
 * @returns Numeric value (in the original unit) or null if unparseable
 */
export function parseDimension(dim: string | number | undefined | null): number | null {
  if (dim === undefined || dim === null) return null;
  if (typeof dim === 'number') return dim;
  if (dim.trim() === '') return null;

  const str = dim.trim();

  // Try plain number first
  const plainNum = parseFloat(str);
  if (!isNaN(plainNum) && str === String(plainNum)) {
    return plainNum;
  }

  // Feet-inches pattern: "15'-0\"", "15' 6\"", "15'-6 1/2\"", "15'-6.5\""
  const feetInchesRegex = /^(\d+(?:\.\d+)?)'\s*[-–]?\s*(?:(\d+(?:\.\d+)?)\s*(?:(\d+)\/(\d+))?\s*")?$/;
  const feetInchesMatch = str.match(feetInchesRegex);
  if (feetInchesMatch) {
    const feet = parseFloat(feetInchesMatch[1]);
    const inches = feetInchesMatch[2] ? parseFloat(feetInchesMatch[2]) : 0;
    const fracNum = feetInchesMatch[3] ? parseInt(feetInchesMatch[3]) : 0;
    const fracDen = feetInchesMatch[4] ? parseInt(feetInchesMatch[4]) : 1;
    return feet + (inches + fracNum / fracDen) / 12;
  }

  // Decimal feet: "15.5'"
  const decimalFeetRegex = /^(\d+(?:\.\d+)?)'\s*$/;
  const decimalFeetMatch = str.match(decimalFeetRegex);
  if (decimalFeetMatch) {
    return parseFloat(decimalFeetMatch[1]);
  }

  // Inches only: "18\"", "18.5\""
  const inchesRegex = /^(\d+(?:\.\d+)?)\s*"\s*$/;
  const inchesMatch = str.match(inchesRegex);
  if (inchesMatch) {
    return parseFloat(inchesMatch[1]);
  }

  // Metric: "3.5m", "350cm", "3500mm"
  const metricRegex = /^(\d+(?:\.\d+)?)\s*(mm|cm|m)\s*$/;
  const metricMatch = str.match(metricRegex);
  if (metricMatch) {
    return parseFloat(metricMatch[1]);
  }

  // Fallback: try to extract any number from the string
  const fallbackMatch = str.match(/(\d+(?:\.\d+)?)/);
  if (fallbackMatch) {
    return parseFloat(fallbackMatch[1]);
  }

  return null;
}

/**
 * Format a numeric dimension value as a human-readable string.
 * 
 * @param value - The numeric value
 * @param unit  - The unit ('mm', 'cm', 'm', 'in', 'ft')
 * @returns Formatted string like "15.0 m" or "15'-0\""
 */
export function formatDimension(value: number, unit: string = 'm'): string {
  switch (unit) {
    case 'ft':
    case 'feet': {
      const feet = Math.floor(value);
      const inches = Math.round((value - feet) * 12 * 10) / 10;
      if (inches === 0) return `${feet}'-0"`;
      return `${feet}'-${inches}"`;
    }
    case 'in':
    case 'inches':
      return `${value.toFixed(2)}"`;
    case 'mm':
      return `${value.toFixed(1)} mm`;
    case 'cm':
      return `${value.toFixed(2)} cm`;
    case 'm':
    case 'meters':
    default:
      return `${value.toFixed(3)} m`;
  }
}