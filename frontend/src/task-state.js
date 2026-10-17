/** Normalize a newly-started dashboard task for the poller. */
export function normalizeStartedTask(result, label) {
  const id = result?.task_id ?? result?.id;
  if (id === undefined || id === null || id === "") {
    throw new TypeError("A started task must include task_id or id.");
  }

  return {
    ...result,
    id,
    label: label || result.label || "Task",
    output: "Starting task…",
  };
}
