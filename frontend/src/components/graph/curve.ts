import type { Point } from "./edges";

/**
 * Points at the given fractions of a route's drawn length (0.5 = halfway). Splines are measured
 * along the curve itself, not its control points, so a label sits on the line.
 */
export function alongCurve(
  p: Point[],
  spline: boolean,
  fractions: number[],
): Point[] {
  const line = spline && (p.length - 1) % 3 === 0 ? sampleSplines(p) : p;
  const lengths = line.slice(1).map((q, i) => dist(line[i], q));
  const total = lengths.reduce((a, b) => a + b, 0);
  return fractions.map((f) => {
    let left = total * f;
    for (let i = 0; i < lengths.length; i++) {
      if (left <= lengths[i]) return toward(line[i], line[i + 1], left);
      left -= lengths[i];
    }
    return line[line.length - 1];
  });
}

/** A cubic spline route as a dense polyline */
function sampleSplines(p: Point[], steps = 24): Point[] {
  const out = [p[0]];
  for (let i = 1; i + 2 < p.length; i += 3) {
    const [a, b, c, d] = [p[i - 1], p[i], p[i + 1], p[i + 2]];
    for (let k = 1; k <= steps; k++) {
      const t = k / steps;
      const u = 1 - t;
      out.push({
        x:
          u * u * u * a.x +
          3 * u * u * t * b.x +
          3 * u * t * t * c.x +
          t * t * t * d.x,
        y:
          u * u * u * a.y +
          3 * u * u * t * b.y +
          3 * u * t * t * c.y +
          t * t * t * d.y,
      });
    }
  }
  return out;
}

export const dist = (a: Point, b: Point) => Math.hypot(a.x - b.x, a.y - b.y);
export function toward(from: Point, to: Point, r: number): Point {
  const len = dist(from, to) || 1;
  return {
    x: from.x + ((to.x - from.x) / len) * r,
    y: from.y + ((to.y - from.y) / len) * r,
  };
}
