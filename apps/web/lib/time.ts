export function timeAgo(value: string | null | undefined, now = Date.now()) {
  if (!value) return "—";
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) return "invalid timestamp";
  const seconds = Math.floor((now - timestamp) / 1000);
  if (Math.abs(seconds) < 5) return "just now";
  if (seconds < 0) {
    const future = Math.abs(seconds);
    if (future > 300) return "clock mismatch detected";
    if (future < 60) return `in ${future}s`;
    return `in ${Math.ceil(future / 60)}m`;
  }
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  if (seconds < 172800) return "yesterday";
  return `${Math.floor(seconds / 86400)}d ago`;
}

export function exactTime(
  value: string | null | undefined,
  locale?: Intl.LocalesArgument,
  timeZone?: string,
) {
  if (!value) return "Timestamp unavailable";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "Invalid timestamp";
  return new Intl.DateTimeFormat(locale, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
    timeZoneName: "short",
    timeZone,
  }).format(date);
}