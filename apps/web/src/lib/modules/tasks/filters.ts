/**
 * Sentinel written to the `assignee_user_id` query param when the user explicitly clears the
 * assignee filter — distinct from the param being *absent*, which the tasks list load
 * defaults to the signed-in user (opening /tasks shows "my tasks" first, not everyone's).
 * Never a real value: real assignees are UUIDs.
 */
export const ALL_ASSIGNEES = "all";
