const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("AttendanceLedger", function () {
  let attendanceLedger;
  let owner;
  let backendSigner;
  let unauthorizedAccount;

  const STUDENT_ID = 101;
  const SESSION_ID = 5001;

  beforeEach(async function () {
    // 1. Get signers from Hardhat
    [owner, backendSigner, unauthorizedAccount] = await ethers.getSigners();

    // 2. Deploy contract passing owner.address as the 'admin' constructor argument
    const AttendanceLedger = await ethers.getContractFactory("AttendanceLedger");
    attendanceLedger = await AttendanceLedger.deploy(owner.address);
    await attendanceLedger.waitForDeployment();

    // 3. Grant BACKEND_ROLE to backendSigner using the admin account
    const BACKEND_ROLE = await attendanceLedger.BACKEND_ROLE();
    await attendanceLedger.grantRole(BACKEND_ROLE, backendSigner.address);
  });

  it("should successfully lock attendance and emit AttendanceLocked event on first call", async function () {
    // Call lockAttendance using the authorized backend signer
    await expect(
      attendanceLedger.connect(backendSigner).lockAttendance(STUDENT_ID, SESSION_ID)
    )
      .to.emit(attendanceLedger, "AttendanceLocked")
      .withArgs(STUDENT_ID, SESSION_ID, (timestamp) => timestamp > 0);

    // Verify status is locked
    const locked = await attendanceLedger.isLocked(STUDENT_ID, SESSION_ID);
    expect(locked).to.equal(true);
  });

  it("should revert with AttendanceAlreadyLocked when called a second time for the same pair", async function () {
    // First call locks attendance
    await attendanceLedger.connect(backendSigner).lockAttendance(STUDENT_ID, SESSION_ID);

    // Second call for the same student + session pair must revert with custom error
    await expect(
      attendanceLedger.connect(backendSigner).lockAttendance(STUDENT_ID, SESSION_ID)
    )
      .to.be.revertedWithCustomError(attendanceLedger, "AttendanceAlreadyLocked")
      .withArgs(STUDENT_ID, SESSION_ID);
  });

  it("should revert if an unauthorized account attempts to lock attendance", async function () {
    await expect(
      attendanceLedger.connect(unauthorizedAccount).lockAttendance(STUDENT_ID, SESSION_ID)
    ).to.be.revertedWithCustomError(attendanceLedger, "AccessControlUnauthorizedAccount");
  });
});