import { describeRecordsError } from "./records.js";

// Backend check/error identifiers stay unchanged; only display text is mapped.
const MESSAGES = {
  "row_lock/grade_locked": "A correction is already pending for this grade",
  "row_lock/stale_value":
    "The current grade has changed. Refresh it and review the correction before trying again.",
  "row_lock/lock_missing":
    "This correction no longer holds the grade lock. Refresh the list before taking another action.",
  "grade/grade_not_found": "No current grade was found for this student and course. Check both entries.",
  "request/missing_fields": "Complete the required fields, including a reason when rejecting a correction.",
  "request/justification_too_short": "Explain the correction in at least 10 characters, excluding leading and trailing spaces.",
  "request/grade_unchanged": "The new grade must differ from the current grade.",
  "request/invalid_course_code": "Enter a course code of no more than 64 characters.",
  "request/invalid_grade": "Enter a new grade of no more than 16 characters.",
  "proposal/correction_not_found": "This grade correction no longer exists. Refresh the list.",
  "proposal/correction_rejected": "This grade correction was rejected and cannot be approved. Refresh the list.",
  "proposal/proposal_not_pending": "This grade correction is no longer pending. Refresh the list.",
  "auth/insufficient_role": "Your account cannot perform this action. Only the HOD can approve or reject grade corrections.",
  "audit_trail/not_executed": "The signature was recorded, but the grade correction did not execute. The grade remains locked; ask an administrator to reconcile it.",
  "database/proposal_mirror_failed": "The grade correction was recorded on-chain, but could not be saved locally. Do not resubmit; ask an administrator to reconcile it.",
  "database/grade_update_failed": "The grade correction executed on-chain, but the current grade could not be updated locally. Ask an administrator to reconcile it.",
  "database/reject_failed": "The rejection could not be saved. Refresh the list before trying again.",
};

export function describeGradesError(error) {
  if (!error) return "Something went wrong.";
  return MESSAGES[`${error.check}/${error.code}`] || describeRecordsError(error);
}
