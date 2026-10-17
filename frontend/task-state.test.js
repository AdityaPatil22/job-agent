import assert from "node:assert/strict";
import test from "node:test";

import { normalizeStartedTask } from "./src/task-state.js";

test("maps API task_id to id used by the polling endpoint", () => {
  const task = normalizeStartedTask(
    { task_id: "apply-123", status: "running", label: "Apply" },
    "Assist with application",
  );

  assert.equal(task.id, "apply-123");
  assert.equal(task.status, "running");
  assert.equal(task.label, "Assist with application");
  assert.equal(task.output, "Starting task…");
});

test("accepts an already-normalized task response", () => {
  const task = normalizeStartedTask({ id: "task-456", status: "running" }, "");

  assert.equal(task.id, "task-456");
  assert.equal(task.label, "Task");
});
