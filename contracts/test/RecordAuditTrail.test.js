/**
 * Mocha/Chai test suite for RecordAuditTrail (Module 2 - multi-sig
 * anti-tamper audit trail).
 *
 * Run with:
 *   npx hardhat test                      # or: npm test
 *   npx hardhat test test/RecordAuditTrail.test.js
 *
 * The suite runs against the in-process Hardhat network. Every test gets a
 * fresh deployment through `deployAuditTrailFixture()`, which mirrors the
 * deployment script: the admin deploys the contract, then grants
 * INSTRUCTOR_ROLE / HOD_ROLE / EXAM_CONTROLLER_ROLE to dedicated signers.
 */
const { expect } = require("chai");
const { ethers } = require("hardhat");

// Canonical example CID used as the IPFS evidence pointer.
const IPFS_CID = "bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi";

// Proposed change reused by the tests.
const STUDENT_ID = 2024001n;
const FIELD = "attendance";
const OLD_VALUE = "70";
const NEW_VALUE = "75";

// ProposalStatus enum values, kept in sync with the contract.
const STATUS_PENDING = 0n;
const STATUS_EXECUTED = 1n;

/**
 * Deploys a fresh RecordAuditTrail and grants each module role to a dedicated
 * signer, exactly like scripts/deploy.js does for the local network.
 * @returns {Promise<object>} The contract, the admin and the role signers.
 */
async function deployAuditTrailFixture() {
  const [admin, instructor, hod, examController, student] = await ethers.getSigners();

  const auditTrail = await ethers.deployContract("RecordAuditTrail", [admin.address]);
  await auditTrail.waitForDeployment();

  await auditTrail.grantRole(await auditTrail.INSTRUCTOR_ROLE(), instructor.address);
  await auditTrail.grantRole(await auditTrail.HOD_ROLE(), hod.address);
  await auditTrail.grantRole(await auditTrail.EXAM_CONTROLLER_ROLE(), examController.address);

  return { auditTrail, admin, instructor, hod, examController, student };
}

describe("RecordAuditTrail", function () {
  let auditTrail;
  let admin;
  let instructor;
  let hod;
  let examController;
  let student;

  beforeEach(async function () {
    ({ auditTrail, admin, instructor, hod, examController, student } =
      await deployAuditTrailFixture());
  });

  describe("deployment", function () {
    it("should assign DEFAULT_ADMIN_ROLE to the admin and each module role to the expected address", async function () {
      expect(await auditTrail.hasRole(await auditTrail.DEFAULT_ADMIN_ROLE(), admin.address)).to.equal(true);
      expect(await auditTrail.hasRole(await auditTrail.INSTRUCTOR_ROLE(), instructor.address)).to.equal(true);
      expect(await auditTrail.hasRole(await auditTrail.HOD_ROLE(), hod.address)).to.equal(true);
      expect(await auditTrail.hasRole(await auditTrail.EXAM_CONTROLLER_ROLE(), examController.address)).to.equal(true);
    });

    it("should leave addresses without an explicit grant holding no module roles", async function () {
      expect(await auditTrail.hasRole(await auditTrail.INSTRUCTOR_ROLE(), student.address)).to.equal(false);
      expect(await auditTrail.hasRole(await auditTrail.HOD_ROLE(), student.address)).to.equal(false);
      expect(await auditTrail.hasRole(await auditTrail.EXAM_CONTROLLER_ROLE(), student.address)).to.equal(false);

      // The admin manages roles but does not hold the module roles itself.
      expect(await auditTrail.hasRole(await auditTrail.INSTRUCTOR_ROLE(), admin.address)).to.equal(false);
    });

    it("should require exactly 2 signatures and start with an empty proposal list", async function () {
      expect(await auditTrail.REQUIRED_SIGNATURES()).to.equal(2n);
      expect(await auditTrail.proposalCount()).to.equal(0n);
    });
  });

  describe("proposeChange", function () {
    it("should let an instructor create a Pending proposal pre-signed with 1 of 2 required signatures", async function () {
      const tx = await auditTrail
        .connect(instructor)
        .proposeChange(STUDENT_ID, FIELD, OLD_VALUE, NEW_VALUE, IPFS_CID);

      await expect(tx)
        .to.emit(auditTrail, "ProposalCreated")
        .withArgs(1n, STUDENT_ID, instructor.address, FIELD, OLD_VALUE, NEW_VALUE, IPFS_CID);
      await expect(tx).to.emit(auditTrail, "ProposalSigned").withArgs(1n, instructor.address, 1n);

      const proposal = await auditTrail.getProposal(1n);
      expect(proposal.status).to.equal(STATUS_PENDING);
      expect(proposal.signatureCount).to.equal(1n);
      expect(await auditTrail.hasSigned(1n, instructor.address)).to.equal(true);
      expect(await auditTrail.proposalCount()).to.equal(1n);
    });

    it("should revert when an account without INSTRUCTOR_ROLE calls proposeChange()", async function () {
      const instructorRole = await auditTrail.INSTRUCTOR_ROLE();

      // A plain student holds no module role at all.
      await expect(
        auditTrail.connect(student).proposeChange(STUDENT_ID, FIELD, OLD_VALUE, NEW_VALUE, IPFS_CID)
      )
        .to.be.revertedWithCustomError(auditTrail, "AccessControlUnauthorizedAccount")
        .withArgs(student.address, instructorRole);

      // Holding a different module role (HOD) is not sufficient either.
      await expect(
        auditTrail.connect(hod).proposeChange(STUDENT_ID, FIELD, OLD_VALUE, NEW_VALUE, IPFS_CID)
      )
        .to.be.revertedWithCustomError(auditTrail, "AccessControlUnauthorizedAccount")
        .withArgs(hod.address, instructorRole);
    });

    it("should reject proposals with an empty field or empty IPFS evidence", async function () {
      await expect(
        auditTrail.connect(instructor).proposeChange(STUDENT_ID, "", OLD_VALUE, NEW_VALUE, IPFS_CID)
      ).to.be.revertedWithCustomError(auditTrail, "EmptyField");

      await expect(
        auditTrail.connect(instructor).proposeChange(STUDENT_ID, FIELD, OLD_VALUE, NEW_VALUE, "")
      ).to.be.revertedWithCustomError(auditTrail, "EmptyIpfsCid");
    });
  });

  describe("approve", function () {
    it("should execute the proposal and emit RecordUpdated when the HOD co-signs the instructor's proposal", async function () {
      await auditTrail
        .connect(instructor)
        .proposeChange(STUDENT_ID, FIELD, OLD_VALUE, NEW_VALUE, IPFS_CID);

      const tx = await auditTrail.connect(hod).approve(1n);
      const receipt = await tx.wait();
      const block = await ethers.provider.getBlock(receipt.blockNumber);
      const executedAt = BigInt(block.timestamp);

      // The HOD's signature is the 2nd one and flips the proposal to Executed.
      await expect(tx).to.emit(auditTrail, "ProposalSigned").withArgs(1n, hod.address, 2n);
      await expect(tx)
        .to.emit(auditTrail, "RecordUpdated")
        .withArgs(STUDENT_ID, FIELD, OLD_VALUE, NEW_VALUE, executedAt, IPFS_CID);

      const proposal = await auditTrail.getProposal(1n);
      expect(proposal.status).to.equal(STATUS_EXECUTED);
      expect(proposal.signatureCount).to.equal(2n);
      expect(proposal.executedAt).to.equal(executedAt);
    });

    it("should revert a second approval from the exam controller because the proposal already executed", async function () {
      await auditTrail
        .connect(instructor)
        .proposeChange(STUDENT_ID, FIELD, OLD_VALUE, NEW_VALUE, IPFS_CID);

      // Instructor's signature (1) + HOD approval (2) hits the threshold and
      // auto-executes the proposal.
      await auditTrail.connect(hod).approve(1n);
      expect((await auditTrail.getProposal(1n)).status).to.equal(STATUS_EXECUTED);

      // The exam controller's approval now arrives too late.
      await expect(auditTrail.connect(examController).approve(1n))
        .to.be.revertedWithCustomError(auditTrail, "ProposalNotPending")
        .withArgs(1n);
    });

    it("should revert when the same HOD calls approve() twice on the same proposal", async function () {
      await auditTrail
        .connect(instructor)
        .proposeChange(STUDENT_ID, FIELD, OLD_VALUE, NEW_VALUE, IPFS_CID);

      // The first approval reaches the threshold and executes the proposal,
      // so the duplicate call is rejected as no longer pending.
      await auditTrail.connect(hod).approve(1n);

      await expect(auditTrail.connect(hod).approve(1n))
        .to.be.revertedWithCustomError(auditTrail, "ProposalNotPending")
        .withArgs(1n);
    });

    it("should revert when an account without HOD_ROLE or EXAM_CONTROLLER_ROLE calls approve()", async function () {
      await auditTrail
        .connect(instructor)
        .proposeChange(STUDENT_ID, FIELD, OLD_VALUE, NEW_VALUE, IPFS_CID);

      // A plain student is not an approver.
      await expect(auditTrail.connect(student).approve(1n))
        .to.be.revertedWithCustomError(auditTrail, "NotAnApprover")
        .withArgs(student.address);

      // Neither is the instructor who created the proposal.
      await expect(auditTrail.connect(instructor).approve(1n))
        .to.be.revertedWithCustomError(auditTrail, "NotAnApprover")
        .withArgs(instructor.address);
    });

    it("should revert when the proposer tries to co-sign their own proposal with an approver role", async function () {
      // Edge case: one address holding both INSTRUCTOR_ROLE and HOD_ROLE must
      // not be able to supply two signatures for the same proposal, because
      // its signature was already counted when the proposal was created.
      await auditTrail.grantRole(await auditTrail.HOD_ROLE(), instructor.address);

      await auditTrail
        .connect(instructor)
        .proposeChange(STUDENT_ID, FIELD, OLD_VALUE, NEW_VALUE, IPFS_CID);

      await expect(auditTrail.connect(instructor).approve(1n))
        .to.be.revertedWithCustomError(auditTrail, "AlreadySigned")
        .withArgs(1n, instructor.address);
    });
  });

  describe("getProposal", function () {
    it("should return every proposal field with Pending status before execution", async function () {
      const tx = await auditTrail
        .connect(instructor)
        .proposeChange(STUDENT_ID, FIELD, OLD_VALUE, NEW_VALUE, IPFS_CID);
      const receipt = await tx.wait();
      const block = await ethers.provider.getBlock(receipt.blockNumber);

      const proposal = await auditTrail.getProposal(1n);
      expect(proposal.id).to.equal(1n);
      expect(proposal.studentId).to.equal(STUDENT_ID);
      expect(proposal.field).to.equal(FIELD);
      expect(proposal.oldValue).to.equal(OLD_VALUE);
      expect(proposal.newValue).to.equal(NEW_VALUE);
      expect(proposal.ipfsCid).to.equal(IPFS_CID);
      expect(proposal.proposer).to.equal(instructor.address);
      expect(proposal.status).to.equal(STATUS_PENDING);
      expect(proposal.signatureCount).to.equal(1n);
      expect(proposal.createdAt).to.equal(BigInt(block.timestamp));
      expect(proposal.executedAt).to.equal(0n);

      expect(await auditTrail.hasSigned(1n, instructor.address)).to.equal(true);
      expect(await auditTrail.hasSigned(1n, hod.address)).to.equal(false);
    });

    it("should return Executed status and the execution timestamp after approval", async function () {
      await auditTrail
        .connect(instructor)
        .proposeChange(STUDENT_ID, FIELD, OLD_VALUE, NEW_VALUE, IPFS_CID);

      const tx = await auditTrail.connect(hod).approve(1n);
      const receipt = await tx.wait();
      const block = await ethers.provider.getBlock(receipt.blockNumber);

      const proposal = await auditTrail.getProposal(1n);
      expect(proposal.status).to.equal(STATUS_EXECUTED);
      expect(proposal.signatureCount).to.equal(2n);
      expect(proposal.executedAt).to.equal(BigInt(block.timestamp));
      expect(proposal.executedAt).to.be.greaterThan(0n);

      // The original change payload stays intact after execution.
      expect(proposal.studentId).to.equal(STUDENT_ID);
      expect(proposal.field).to.equal(FIELD);
      expect(proposal.oldValue).to.equal(OLD_VALUE);
      expect(proposal.newValue).to.equal(NEW_VALUE);
      expect(proposal.ipfsCid).to.equal(IPFS_CID);

      expect(await auditTrail.hasSigned(1n, hod.address)).to.equal(true);
    });

    it("should revert when reading a proposal id that does not exist", async function () {
      await expect(auditTrail.getProposal(999n))
        .to.be.revertedWithCustomError(auditTrail, "ProposalNotFound")
        .withArgs(999n);
    });
  });
});
