import { describe, expect, test } from "bun:test";
import { existsSync, mkdtempSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  buildSelfEntry,
  entryPath,
  readEntry,
  registryDir,
  removeEntry,
  sweep,
  writeEntry,
  type RegistryEntry,
} from "./registry.ts";

function tempBase(): string {
  return mkdtempSync(join(tmpdir(), "claude-voice-test-"));
}

function entry(sessionId: string, claudePid: number): RegistryEntry {
  return {
    session_id: sessionId,
    claude_pid: claudePid,
    server_pid: 1000,
    port: 49321,
    cwd: "/tmp",
    started_at: new Date().toISOString(),
    name: null,
  };
}

describe("registry", () => {
  test("write then read round-trips", () => {
    const base = tempBase();
    writeEntry(entry("abc", 42), base);
    expect(readEntry("abc", base)?.port).toBe(49321);
  });

  test("readEntry returns null for missing session", () => {
    expect(readEntry("nope", tempBase())).toBeNull();
  });

  test("removeEntry is idempotent", () => {
    const base = tempBase();
    writeEntry(entry("abc", 42), base);
    removeEntry("abc", base);
    removeEntry("abc", base);
    expect(existsSync(entryPath("abc", base))).toBe(false);
  });

  test("sweep keeps live entries and removes dead ones", () => {
    const base = tempBase();
    writeEntry(entry("live", 100), base);
    writeEntry(entry("dead", 200), base);
    const removed = sweep(base, (pid) => pid === 100);
    expect(removed).toEqual(["dead.json"]);
    expect(readEntry("live", base)).not.toBeNull();
    expect(readEntry("dead", base)).toBeNull();
  });

  test("sweep removes unreadable entries", () => {
    const base = tempBase();
    mkdirSync(registryDir(base), { recursive: true });
    writeFileSync(join(registryDir(base), "corrupt.json"), "{not json");
    expect(sweep(base, () => true)).toEqual(["corrupt.json"]);
  });

  test("sweep on missing dir returns empty", () => {
    expect(sweep(join(tempBase(), "absent"))).toEqual([]);
  });

  test("sweep ignores non-json files", () => {
    const base = tempBase();
    mkdirSync(registryDir(base), { recursive: true });
    writeFileSync(join(registryDir(base), "README.txt"), "hello");
    expect(sweep(base, () => false)).toEqual([]);
  });
});

describe("identity handling", () => {
  test("no CLAUDE_CODE_SESSION_ID in env means no registration", () => {
    const entry = buildSelfEntry({
      env: {},
      port: 1234,
      cwd: "/somewhere",
      serverPid: 10,
      claudePid: 9,
    });
    expect(entry).toBeNull();
  });

  test("session id in env builds a complete entry", () => {
    const entry = buildSelfEntry({
      env: { CLAUDE_CODE_SESSION_ID: "abc-123" },
      port: 1234,
      cwd: "/somewhere",
      serverPid: 10,
      claudePid: 9,
    });
    expect(entry).toEqual(
      expect.objectContaining({
        session_id: "abc-123",
        claude_pid: 9,
        server_pid: 10,
        port: 1234,
        cwd: "/somewhere",
        name: null,
      }),
    );
  });

  test("empty-string session id is treated as absent", () => {
    expect(
      buildSelfEntry({
        env: { CLAUDE_CODE_SESSION_ID: "" },
        port: 1,
        cwd: "/",
        serverPid: 2,
        claudePid: 3,
      }),
    ).toBeNull();
  });
});
