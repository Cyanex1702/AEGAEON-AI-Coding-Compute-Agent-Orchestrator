import assert from "node:assert/strict";
import test from "node:test";

import { exactTime, timeAgo } from "../lib/time.ts";

const NOW = Date.parse("2026-08-24T12:00:00.000Z");

test("relative time is derived from authoritative UTC timestamps", () => {
  assert.equal(timeAgo("2026-08-24T11:59:58.000Z", NOW), "just now");
  assert.equal(timeAgo("2026-08-24T11:58:00.000Z", NOW), "2m ago");
  assert.equal(timeAgo("2026-08-24T10:00:00.000Z", NOW), "2h ago");
  assert.equal(timeAgo("2026-08-23T10:00:00.000Z", NOW), "yesterday");
  assert.equal(timeAgo("2026-08-14T12:00:00.000Z", NOW), "10d ago");
});

test("future timestamps show bounded clock-skew language", () => {
  assert.equal(timeAgo("2026-08-24T12:02:00.000Z", NOW), "in 2m");
  assert.equal(timeAgo("2026-08-24T12:10:00.000Z", NOW), "clock mismatch detected");
});

test("exact time converts UTC into the requested browser-local timezone", () => {
  const displayed = exactTime("2026-08-24T12:00:00.000Z", "en-US", "Asia/Karachi");
  assert.match(displayed, /5:00:00 PM/);
  assert.match(displayed, /GMT\+5/);
});

test("invalid timestamps never produce misleading relative values", () => {
  assert.equal(timeAgo("not-a-date", NOW), "invalid timestamp");
  assert.equal(exactTime("not-a-date"), "Invalid timestamp");
});