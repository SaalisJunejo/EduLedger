// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";

/**
 * @title RecordAuditTrail
 * @notice Module 2 of EduLedger - multi-sig anti-tamper audit trail.
 *
 * @dev Academic record changes (grades, attendance, etc.) cannot be applied
 * unilaterally. An instructor proposes a change together with an IPFS CID
 * pointing at the supporting evidence; the proposal is then co-signed by the
 * HOD or the Exam Controller. As soon as REQUIRED_SIGNATURES (2) valid
 * signatures are collected - the instructor's automatic signature on proposal
 * creation plus one approval - the change auto-executes and the immutable
 * `RecordUpdated` event anchors it on-chain.
 *
 * Role administration uses OpenZeppelin `AccessControl`:
 *   - INSTRUCTOR_ROLE      - may propose changes (auto-signs own proposals)
 *   - HOD_ROLE             - may approve pending proposals
 *   - EXAM_CONTROLLER_ROLE - may approve pending proposals
 *   - DEFAULT_ADMIN_ROLE   - manages the roles above (granted to `admin`)
 */
contract RecordAuditTrail is AccessControl {
    // ------------------------------------------------------------------
    // Roles
    // ------------------------------------------------------------------

    /// @notice Granted to faculty that may create record-change proposals.
    bytes32 public constant INSTRUCTOR_ROLE = keccak256("INSTRUCTOR_ROLE");

    /// @notice Granted to the Head of Department; may approve proposals.
    bytes32 public constant HOD_ROLE = keccak256("HOD_ROLE");

    /// @notice Granted to the Exam Controller; may approve proposals.
    bytes32 public constant EXAM_CONTROLLER_ROLE = keccak256("EXAM_CONTROLLER_ROLE");

    // ------------------------------------------------------------------
    // Constants and types
    // ------------------------------------------------------------------

    /// @notice Number of valid signatures required to execute a proposal.
    /// @dev The proposer's signature counts as the first one, so exactly one
    /// approval from an HOD or Exam Controller address is needed.
    uint256 public constant REQUIRED_SIGNATURES = 2;

    /// @notice Lifecycle state of a record-change proposal.
    enum ProposalStatus {
        Pending, // collecting signatures
        Executed // threshold reached, change applied on-chain
    }

    /// @notice A record-change proposal and its signature/execution state.
    struct Proposal {
        uint256 id; // proposal id (1-based, assigned on creation)
        uint256 studentId; // student whose record is being changed
        string field; // record field, e.g. "attendance"
        string oldValue; // current value, e.g. "70"
        string newValue; // proposed value, e.g. "75"
        string ipfsCid; // IPFS CID of the justification evidence
        address proposer; // instructor address that created it
        ProposalStatus status; // Pending or Executed
        uint8 signatureCount; // valid signatures collected so far
        uint256 createdAt; // block timestamp of proposal creation
        uint256 executedAt; // block timestamp of execution (0 while pending)
    }

    // ------------------------------------------------------------------
    // Storage
    // ------------------------------------------------------------------

    /// @notice Proposal id => proposal data.
    mapping(uint256 => Proposal) private _proposals;

    /// @notice proposalId => signer => whether the address already signed it.
    mapping(uint256 => mapping(address => bool)) private _hasSigned;

    /// @dev Incrementing counter that assigns proposal ids; starts at 1.
    uint256 private _nextProposalId = 1;

    // ------------------------------------------------------------------
    // Events
    // ------------------------------------------------------------------

    /// @notice Emitted when an instructor creates a new proposal.
    event ProposalCreated(
        uint256 indexed proposalId,
        uint256 indexed studentId,
        address indexed proposer,
        string field,
        string oldValue,
        string newValue,
        string ipfsCid
    );

    /// @notice Emitted for every accepted signature (including the proposer's).
    event ProposalSigned(uint256 indexed proposalId, address indexed signer, uint256 signatureCount);

    /// @notice Emitted when a proposal reaches the threshold and executes.
    /// @dev This is the immutable audit anchor: it cannot be edited or
    /// deleted, and any off-chain record that disagrees with it is suspect.
    event RecordUpdated(
        uint256 indexed studentId,
        string field,
        string oldValue,
        string newValue,
        uint256 timestamp,
        string ipfsCid
    );

    // ------------------------------------------------------------------
    // Errors
    // ------------------------------------------------------------------

    /// @notice Raised when the caller holds neither HOD nor Exam Controller role.
    error NotAnApprover(address caller);

    /// @notice Raised when acting on a proposal id that does not exist.
    error ProposalNotFound(uint256 proposalId);

    /// @notice Raised when approving a proposal that already executed.
    error ProposalNotPending(uint256 proposalId);

    /// @notice Raised when an address approves the same proposal twice.
    error AlreadySigned(uint256 proposalId, address signer);

    /// @notice Raised when the record field is empty.
    error EmptyField();

    /// @notice Raised when no IPFS evidence CID is provided.
    error EmptyIpfsCid();

    // ------------------------------------------------------------------
    // Modifiers
    // ------------------------------------------------------------------

    /// @dev Restricts a function to HOD or Exam Controller addresses.
    modifier onlyApprover() {
        if (!hasRole(HOD_ROLE, msg.sender) && !hasRole(EXAM_CONTROLLER_ROLE, msg.sender)) {
            revert NotAnApprover(msg.sender);
        }
        _;
    }

    // ------------------------------------------------------------------
    // Deployment
    // ------------------------------------------------------------------

    /// @param admin Address that receives DEFAULT_ADMIN_ROLE and can grant
    /// or revoke the roles above (normally the platform admin account).
    constructor(address admin) {
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
    }

    // ------------------------------------------------------------------
    // Instructor actions
    // ------------------------------------------------------------------

    /**
     * @notice Creates a new record-change proposal with status `Pending`.
     * @dev Only INSTRUCTOR_ROLE addresses may call this. The caller's own
     * signature is recorded immediately, so the proposal starts at
     * 1 of REQUIRED_SIGNATURES; `ipfsCid` must reference the justification
     * evidence (e.g. a medical certificate) pinned to IPFS.
     * @param studentId Student whose record is being changed.
     * @param field Record field being changed, e.g. "attendance".
     * @param oldValue Current value of the field.
     * @param newValue Proposed new value of the field.
     * @param ipfsCid IPFS CID of the supporting evidence document.
     * @return proposalId Id of the newly created proposal.
     */
    function proposeChange(
        uint256 studentId,
        string memory field,
        string memory oldValue,
        string memory newValue,
        string memory ipfsCid
    ) external onlyRole(INSTRUCTOR_ROLE) returns (uint256 proposalId) {
        if (bytes(field).length == 0) revert EmptyField();
        if (bytes(ipfsCid).length == 0) revert EmptyIpfsCid();

        proposalId = _nextProposalId++;

        Proposal storage proposal = _proposals[proposalId];
        proposal.id = proposalId;
        proposal.studentId = studentId;
        proposal.field = field;
        proposal.oldValue = oldValue;
        proposal.newValue = newValue;
        proposal.ipfsCid = ipfsCid;
        proposal.proposer = msg.sender;
        proposal.status = ProposalStatus.Pending;
        proposal.signatureCount = 1; // the instructor's own signature
        proposal.createdAt = block.timestamp;
        proposal.executedAt = 0;

        _hasSigned[proposalId][msg.sender] = true;

        emit ProposalCreated(proposalId, studentId, msg.sender, field, oldValue, newValue, ipfsCid);
        emit ProposalSigned(proposalId, msg.sender, 1);
    }

    // ------------------------------------------------------------------
    // Approver actions
    // ------------------------------------------------------------------

    /**
     * @notice Co-signs a pending proposal; auto-executes it once
     * REQUIRED_SIGNATURES total signatures are recorded.
     * @dev Callable only by HOD_ROLE or EXAM_CONTROLLER_ROLE addresses, and
     * each address may approve a given proposal only once. When this call
     * brings the signature count to REQUIRED_SIGNATURES, the proposal status
     * flips to `Executed` and `RecordUpdated` is emitted with the execution
     * timestamp and the evidence CID.
     * @param proposalId Id of the proposal to approve.
     */
    function approve(uint256 proposalId) external onlyApprover {
        Proposal storage proposal = _proposals[proposalId];

        if (proposal.proposer == address(0)) revert ProposalNotFound(proposalId);
        if (proposal.status != ProposalStatus.Pending) revert ProposalNotPending(proposalId);
        if (_hasSigned[proposalId][msg.sender]) revert AlreadySigned(proposalId, msg.sender);

        _hasSigned[proposalId][msg.sender] = true;
        proposal.signatureCount += 1;

        emit ProposalSigned(proposalId, msg.sender, proposal.signatureCount);

        if (proposal.signatureCount >= REQUIRED_SIGNATURES) {
            proposal.status = ProposalStatus.Executed;
            proposal.executedAt = block.timestamp;

            emit RecordUpdated(
                proposal.studentId,
                proposal.field,
                proposal.oldValue,
                proposal.newValue,
                block.timestamp,
                proposal.ipfsCid
            );
        }
    }

    // ------------------------------------------------------------------
    // Views
    // ------------------------------------------------------------------

    /**
     * @notice Returns all details of a proposal, including the current
     * signature count and status.
     * @param proposalId Id of the proposal to read.
     * @return proposal The full Proposal struct.
     */
    function getProposal(uint256 proposalId) external view returns (Proposal memory proposal) {
        proposal = _proposals[proposalId];
        if (proposal.proposer == address(0)) revert ProposalNotFound(proposalId);
    }

    /**
     * @notice True if `account` has already signed `proposalId`.
     * @dev Lets clients (e.g. the HOD dashboard) disable the approve action
     * for addresses that signed already.
     * @param proposalId Id of the proposal to check.
     * @param account Address to check.
     */
    function hasSigned(uint256 proposalId, address account) external view returns (bool) {
        return _hasSigned[proposalId][account];
    }

    /**
     * @notice Total number of proposals created so far.
     * @dev Also equals the id of the most recent proposal; ids are 1-based.
     */
    function proposalCount() external view returns (uint256) {
        return _nextProposalId - 1;
    }
}
