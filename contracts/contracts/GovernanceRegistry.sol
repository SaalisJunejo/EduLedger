// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * @title GovernanceRegistry
 * @notice Phase 0.5 of EduLedger - the "master contract" that locks the
 * system root administrative address (PRD v2 sections 3, 4.0, 6).
 *
 * @dev The System Admin (PRD: VC) initializes the system root exactly once:
 * the deployer calls `lockRoot` with the root admin address, and from that
 * moment the chain itself refuses to accept another root. There is
 * deliberately no unlock, no transfer and no owner override - a permanent,
 * one-shot decision is the whole point, mirroring the no-resubmission core
 * of AttendanceLedger and WorkloadLedger.
 *
 * The backend's bootstrap endpoint relies on this: it writes the lock first
 * and only then creates the local ADMIN user, so a second bootstrap attempt
 * is rejected on-chain rather than by application logic.
 *
 * No AccessControl here on purpose. There is no role administrator to grant
 * anything before the root exists; the only authority is the deployer, and
 * only until the single lock has been placed.
 */
contract GovernanceRegistry {
    // ------------------------------------------------------------------
    // Storage
    // ------------------------------------------------------------------

    /// @notice Account that deployed the registry - the only caller allowed
    /// to place the one-time root lock.
    address public immutable deployer;

    /// @notice Locked root administrative address (zero until locked).
    address private _rootAdmin;

    /// @notice Whether the root address has already been locked.
    bool private _locked;

    // ------------------------------------------------------------------
    // Events
    // ------------------------------------------------------------------

    /// @notice Emitted exactly once, when the root admin address is locked.
    event RootLocked(address indexed rootAdmin);

    // ------------------------------------------------------------------
    // Errors
    // ------------------------------------------------------------------

    /// @notice Raised when anyone other than the deployer calls `lockRoot`.
    error NotDeployer(address caller);

    /// @notice Raised when the root address was already locked - the lock is
    /// permanent for the lifetime of the chain.
    error RootAlreadyLocked(address rootAdmin);

    /// @notice Raised when the proposed root address is the zero address.
    error ZeroRootAddress();

    // ------------------------------------------------------------------
    // Deployment
    // ------------------------------------------------------------------

    constructor() {
        deployer = msg.sender;
    }

    // ------------------------------------------------------------------
    // Root initialization
    // ------------------------------------------------------------------

    /**
     * @notice Locks the system root administrative address, permanently.
     * @dev Callable once, and only by the deployer. Reverts with
     * `RootAlreadyLocked` on any later attempt (even by the deployer, even
     * with the same address) and with `ZeroRootAddress` when `newRootAdmin`
     * is the zero address.
     * @param newRootAdmin Address that becomes the system root admin.
     */
    function lockRoot(address newRootAdmin) external {
        if (msg.sender != deployer) {
            revert NotDeployer(msg.sender);
        }
        if (_locked) {
            revert RootAlreadyLocked(_rootAdmin);
        }
        if (newRootAdmin == address(0)) {
            revert ZeroRootAddress();
        }

        _locked = true;
        _rootAdmin = newRootAdmin;

        emit RootLocked(newRootAdmin);
    }

    // ------------------------------------------------------------------
    // Views
    // ------------------------------------------------------------------

    /// @notice The locked root administrative address, or the zero address
    /// when the system root has not been initialized yet.
    function rootAdmin() external view returns (address) {
        return _rootAdmin;
    }

    /// @notice True once the root address has been locked (check this before
    /// trusting `rootAdmin`).
    function isLocked() external view returns (bool) {
        return _locked;
    }
}
