// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";

/**
 * @title EduLedgerRoles
 * @notice Shared on-chain role registry for the EduLedger platform.
 *
 * Every EduLedger actor (student, instructor, HOD, exam controller, admin) is
 * represented by a wallet address that holds the matching role. Feature
 * contracts (attendance engine, multi-sig audit trail, faculty workload
 * ledger) can read this registry or reuse the same role identifiers.
 *
 * This contract is intentionally small: it seeds the contract layer described
 * in docs/PRD.md so the toolchain, deployment script, and seed script can be
 * exercised end to end before the module contracts are implemented.
 */
contract EduLedgerRoles is AccessControl {
    /// @dev Alias for OpenZeppelin's DEFAULT_ADMIN_ROLE (role management).
    bytes32 public constant ADMIN_ROLE = DEFAULT_ADMIN_ROLE;

    bytes32 public constant STUDENT_ROLE = keccak256("STUDENT_ROLE");
    bytes32 public constant INSTRUCTOR_ROLE = keccak256("INSTRUCTOR_ROLE");
    bytes32 public constant HOD_ROLE = keccak256("HOD_ROLE");
    bytes32 public constant EXAM_CONTROLLER_ROLE = keccak256("EXAM_CONTROLLER_ROLE");

    event RoleRegistered(address indexed account, bytes32 indexed role);

    /// @param admin Address that receives DEFAULT_ADMIN_ROLE (role management).
    constructor(address admin) {
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
    }

    /// @notice Grants `role` to `account`. Callable only by an admin.
    function registerRole(address account, bytes32 role) external onlyRole(DEFAULT_ADMIN_ROLE) {
        _grantRole(role, account);
        emit RoleRegistered(account, role);
    }

    /// @notice Revokes `role` from `account`. Callable only by an admin.
    function revokeRoleFrom(address account, bytes32 role) external onlyRole(DEFAULT_ADMIN_ROLE) {
        _revokeRole(role, account);
    }

    /// @notice True when `account` holds any EduLedger platform role.
    function isKnownActor(address account) external view returns (bool) {
        return
            hasRole(STUDENT_ROLE, account) ||
            hasRole(INSTRUCTOR_ROLE, account) ||
            hasRole(HOD_ROLE, account) ||
            hasRole(EXAM_CONTROLLER_ROLE, account);
    }
}
