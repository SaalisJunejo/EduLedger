// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";

/**
 * @title WorkloadLedger
 * @notice Module 3 of EduLedger - on-chain faculty workload anchor.
 *
 * @dev The backend (the only holder of BACKEND_ROLE) anchors exactly one
 * immutable workload entry per scheduled class, recording whether the class
 * was CONDUCTED (at least one student scanned the live QR), CONDUCTED with
 * zero students (professor's manual declaration), or UNCONDUCTED (the
 * background auto-flag). Once written, the entry can never be overwritten
 * or removed, so any later resubmission attempt for the same class is
 * rejected on-chain (`WorkloadAlreadyLogged`) - the same no-tamper,
 * no-resubmission core as AttendanceLedger. Off-chain systems enforce
 * their own policies; this contract guarantees the immutable workload
 * record that credit / audit disputes are settled against.
 *
 * Role administration uses OpenZeppelin `AccessControl`:
 *   - BACKEND_ROLE       - may anchor workload entries (the trusted backend
 *                          signer; the deployer for local dev)
 *   - DEFAULT_ADMIN_ROLE - manages BACKEND_ROLE (granted to `admin`)
 */
contract WorkloadLedger is AccessControl {
    // ------------------------------------------------------------------
    // Roles
    // ------------------------------------------------------------------

    /// @notice Granted to the trusted backend signer that anchors workload.
    bytes32 public constant BACKEND_ROLE = keccak256("BACKEND_ROLE");

    // ------------------------------------------------------------------
    // Types
    // ------------------------------------------------------------------

    /// @notice Final workload outcome of a scheduled class.
    enum WorkloadStatus {
        CONDUCTED, // at least one student scanned the live QR
        CONDUCTED_ZERO_STUDENTS, // professor declared zero attendance
        UNCONDUCTED // auto-flagged: no session activity before the deadline
    }

    /// @notice An anchored workload entry (one per scheduled class).
    struct WorkloadLog {
        uint256 professorId; // professor the workload is credited to
        WorkloadStatus status; // final outcome of the class
        uint256 timestamp; // block timestamp of the anchor
    }

    // ------------------------------------------------------------------
    // Storage
    // ------------------------------------------------------------------

    /// @notice classId => anchored workload entry.
    mapping(uint256 => WorkloadLog) private _logs;

    /// @notice classId => whether a workload entry was already anchored.
    mapping(uint256 => bool) private _logged;

    // ------------------------------------------------------------------
    // Events
    // ------------------------------------------------------------------

    /// @notice Emitted exactly once per scheduled class, when its final
    /// workload status is anchored on-chain.
    event WorkloadLogged(uint256 indexed classId, uint256 indexed professorId, WorkloadStatus status, uint256 timestamp);

    // ------------------------------------------------------------------
    // Errors
    // ------------------------------------------------------------------

    /// @notice Raised when the class already has an anchored workload
    /// entry - resubmission is not allowed.
    error WorkloadAlreadyLogged(uint256 classId);

    // ------------------------------------------------------------------
    // Deployment
    // ------------------------------------------------------------------

    /// @param admin Address that receives DEFAULT_ADMIN_ROLE and can grant
    /// or revoke BACKEND_ROLE (normally the platform admin account).
    constructor(address admin) {
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
    }

    // ------------------------------------------------------------------
    // Backend actions
    // ------------------------------------------------------------------

    /**
     * @notice Anchors the final workload status of a class, permanently.
     * @dev Only BACKEND_ROLE addresses may call this. Each class can be
     * logged exactly once; a second call for the same class reverts with
     * `WorkloadAlreadyLogged`, which is how the contract enforces the
     * "one immutable entry per class" rule.
     * @param classId Scheduled class the workload entry belongs to.
     * @param professorId Professor the workload is credited to (the
     * substitute, when one covered the class).
     * @param status Final outcome: CONDUCTED, CONDUCTED_ZERO_STUDENTS or
     * UNCONDUCTED.
     */
    function logWorkload(uint256 classId, uint256 professorId, WorkloadStatus status) external onlyRole(BACKEND_ROLE) {
        if (_logged[classId]) {
            revert WorkloadAlreadyLogged(classId);
        }

        _logged[classId] = true;
        _logs[classId] = WorkloadLog(professorId, status, block.timestamp);

        emit WorkloadLogged(classId, professorId, status, block.timestamp);
    }

    // ------------------------------------------------------------------
    // Views
    // ------------------------------------------------------------------

    /**
     * @notice Returns the anchored workload entry of a class, if any.
     * @dev Used by clients and the backend to enforce "one entry per
     * class" before sending a transaction (and to verify an anchored
     * record).
     * @param classId Class to check.
     * @return logged True if the class already has an on-chain entry.
     * @return status Anchored outcome (zero-valued CONDUCTED when
     * `logged` is false - always check `logged` first).
     * @return professorId Professor the workload is credited to.
     * @return timestamp Block timestamp of the anchor (0 when `logged`
     * is false).
     */
    function getWorkloadLog(uint256 classId)
        external
        view
        returns (bool logged, WorkloadStatus status, uint256 professorId, uint256 timestamp)
    {
        WorkloadLog storage entry = _logs[classId];
        logged = _logged[classId];
        status = entry.status;
        professorId = entry.professorId;
        timestamp = entry.timestamp;
    }
}
