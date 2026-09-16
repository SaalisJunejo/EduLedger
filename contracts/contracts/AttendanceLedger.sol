// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";

/**
 * @title AttendanceLedger
 * @notice Module 1 of EduLedger - on-chain attendance anchor.
 *
 * @dev The backend (the only holder of BACKEND_ROLE) anchors one immutable
 * "PRESENT" record per student+session pair. Once written, the entry can
 * never be overwritten or removed, so any later resubmission attempt for
 * the same pair is rejected on-chain (`AttendanceAlreadyLocked`). Off-chain
 * systems enforce their own policies; this contract guarantees the
 * no-tamper, no-resubmission core of the Zero-Proxy Attendance Engine.
 *
 * Role administration uses OpenZeppelin `AccessControl`:
 *   - BACKEND_ROLE       - may anchor attendance records (the trusted
 *                          backend signer; the deployer for local dev)
 *   - DEFAULT_ADMIN_ROLE - manages BACKEND_ROLE (granted to `admin`)
 */
contract AttendanceLedger is AccessControl {
    // ------------------------------------------------------------------
    // Roles
    // ------------------------------------------------------------------

    /// @notice Granted to the trusted backend signer that anchors attendance.
    bytes32 public constant BACKEND_ROLE = keccak256("BACKEND_ROLE");

    // ------------------------------------------------------------------
    // Storage
    // ------------------------------------------------------------------

    /// @notice studentId => sessionId => attendance locked (student PRESENT).
    mapping(uint256 => mapping(uint256 => bool)) private _locked;

    // ------------------------------------------------------------------
    // Events
    // ------------------------------------------------------------------

    /// @notice Emitted exactly once per student+session pair, when the
    /// student's PRESENT record is anchored on-chain.
    event AttendanceLocked(uint256 indexed studentId, uint256 indexed sessionId, uint256 timestamp);

    // ------------------------------------------------------------------
    // Errors
    // ------------------------------------------------------------------

    /// @notice Raised when attendance for the student+session pair was
    /// already locked - resubmission is not allowed.
    error AttendanceAlreadyLocked(uint256 studentId, uint256 sessionId);

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
     * @notice Anchors the student as PRESENT for the session, permanently.
     * @dev Only BACKEND_ROLE addresses may call this. Each student+session
     * pair can be locked exactly once; a second call for the same pair
     * reverts with `AttendanceAlreadyLocked`, which is how the contract
     * enforces the "no resubmission" rule.
     * @param studentId Student whose attendance is being anchored.
     * @param sessionId Class session the attendance belongs to.
     */
    function lockAttendance(uint256 studentId, uint256 sessionId) external onlyRole(BACKEND_ROLE) {
        if (_locked[studentId][sessionId]) {
            revert AttendanceAlreadyLocked(studentId, sessionId);
        }

        _locked[studentId][sessionId] = true;

        emit AttendanceLocked(studentId, sessionId, block.timestamp);
    }

    // ------------------------------------------------------------------
    // Views
    // ------------------------------------------------------------------

    /**
     * @notice Returns whether attendance for the student+session pair is
     * already locked on-chain.
     * @dev Used by clients and the backend to enforce "no resubmission"
     * before sending a transaction (and to verify an anchored record).
     * @param studentId Student to check.
     * @param sessionId Session to check.
     * @return locked True if the student is recorded PRESENT for the session.
     */
    function isLocked(uint256 studentId, uint256 sessionId) external view returns (bool locked) {
        locked = _locked[studentId][sessionId];
    }
}
