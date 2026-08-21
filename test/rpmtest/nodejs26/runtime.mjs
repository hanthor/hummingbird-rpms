import assert from "node:assert/strict";
import { createHash } from "node:crypto";

assert.equal([19, 23].reduce((sum, value) => sum + value, 0), 42);
assert.equal(createHash("sha256").update("rpmtest").digest("hex").length, 64);
console.log("rpmtest Node.js works: 42");
