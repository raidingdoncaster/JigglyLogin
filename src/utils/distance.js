export function haversineDistance(start, target) {
  if (!start || !target) {
    return Number.POSITIVE_INFINITY;
  }
  const earthRadius = 6371000; // meters
  const startLat = toRadians(start.lat);
  const startLng = toRadians(start.lng);
  const targetLat = toRadians(target.lat);
  const targetLng = toRadians(target.lng);

  const deltaLat = targetLat - startLat;
  const deltaLng = targetLng - startLng;

  const a = Math.sin(deltaLat / 2) ** 2 +
    Math.cos(startLat) * Math.cos(targetLat) * Math.sin(deltaLng / 2) ** 2;

  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  return Math.round(earthRadius * c);
}

function toRadians(value) {
  return (value * Math.PI) / 180;
}
