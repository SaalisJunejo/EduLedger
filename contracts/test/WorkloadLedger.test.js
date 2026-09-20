const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("WorkloadLedger", function () {
  let workloadLedger;
  let owner;
  let backendSigner;
  let unauthorizedAccount;

  const CLASS_ID = 301;
  const PROFESSOR_ID = 77;

  beforeEach(async function () {
    // 1. Get signers from Hardhat
    [owner, backendSigner, unauthorizedAccount] = await ethers.getSigners();

    // 2. Deploy contract passing owner.address as the 'admin' constructor argument
    const WorkloadLedger = await ethers.getContractFactory("WorkloadLedger");
    workloadLedger = await WorkloadLedger.deploy(owner.address);
    await workloadLedger.waitForDeployment();

    // 3. Grant BACKEND_ROLE to backendSigner using the admin account
    const BACKEND_ROLE = await workloadLedger.BACKEND_ROLE();
    await workloadLedger.grantRole(BACKEND_ROLE, backendSigner.address);
  });

  it("should successfully log workload and emit WorkloadLogged event on first call", async function () {
    // Before any log, the view must indicate that no entry exists yet.
    const [loggedBefore] = await workloadLedger.getWorkloadLog(CLASS_ID);
    expect(loggedBefore).to.equal(false);

    // Call logWorkload using the authorized backend signer (status 0 = CONDUCTED)
    await expect(
      workloadLedger.connect(backendSigner).logWorkload(CLASS_ID, PROFESSOR_ID, 0)
    )
      .to.emit(workloadLedger, "WorkloadLogged")
      .withArgs(CLASS_ID, PROFESSOR_ID, 0, (timestamp) => timestamp > 0);

    // Verify the anchored entry reads back correctly
    const [logged, status, professorId, timestamp] = await workloadLedger.getWorkloadLog(CLASS_ID);
    expect(logged).to.equal(true);
    expect(status).to.equal(0); // CONDUCTED
    expect(professorId).to.equal(PROFESSOR_ID);
    expect(timestamp).to.be.gt(0);
  });

  it("should revert with WorkloadAlreadyLogged when called a second time for the same class", async function () {
    // First call logs the class (status 1 = CONDUCTED_ZERO_STUDENTS)
    await workloadLedger.connect(backendSigner).logWorkload(CLASS_ID, PROFESSOR_ID, 1);

    // Second call for the same class must revert with the custom error,
    // even when it carries a different status or professor
    await expect(
      workloadLedger.connect(backendSigner).logWorkload(CLASS_ID, PROFESSOR_ID + 1, 2)
    )
      .to.be.revertedWithCustomError(workloadLedger, "WorkloadAlreadyLogged")
      .withArgs(CLASS_ID);
  });

  it("should revert if an unauthorized account attempts to log workload", async function () {
    await expect(
      workloadLedger.connect(unauthorizedAccount).logWorkload(CLASS_ID, PROFESSOR_ID, 0)
    ).to.be.revertedWithCustomError(workloadLedger, "AccessControlUnauthorizedAccount");
  });
});
