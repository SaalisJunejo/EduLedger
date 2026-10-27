const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("GovernanceRegistry", function () {
  let registry;
  let deployer;
  let rootAdmin;
  let outsider;

  beforeEach(async function () {
    [deployer, rootAdmin, outsider] = await ethers.getSigners();

    const GovernanceRegistry = await ethers.getContractFactory("GovernanceRegistry");
    registry = await GovernanceRegistry.deploy();
    await registry.waitForDeployment();
  });

  it("starts unlocked with a zero root admin", async function () {
    expect(await registry.isLocked()).to.equal(false);
    expect(await registry.rootAdmin()).to.equal(ethers.ZeroAddress);
    expect(await registry.deployer()).to.equal(deployer.address);
  });

  it("locks the root admin once and emits RootLocked", async function () {
    await expect(registry.lockRoot(rootAdmin.address))
      .to.emit(registry, "RootLocked")
      .withArgs(rootAdmin.address);

    expect(await registry.isLocked()).to.equal(true);
    expect(await registry.rootAdmin()).to.equal(rootAdmin.address);
  });

  it("reverts when the root admin is already locked", async function () {
    await registry.lockRoot(rootAdmin.address);

    // Even the deployer, even with the very same address: the lock is permanent.
    await expect(registry.lockRoot(rootAdmin.address))
      .to.be.revertedWithCustomError(registry, "RootAlreadyLocked")
      .withArgs(rootAdmin.address);

    await expect(registry.lockRoot(outsider.address))
      .to.be.revertedWithCustomError(registry, "RootAlreadyLocked")
      .withArgs(rootAdmin.address);
  });

  it("reverts when the proposed root admin is the zero address", async function () {
    await expect(
      registry.lockRoot(ethers.ZeroAddress)
    ).to.be.revertedWithCustomError(registry, "ZeroRootAddress");

    // A rejected attempt must leave the registry open for the real root.
    expect(await registry.isLocked()).to.equal(false);
  });

  it("reverts when anyone other than the deployer tries to lock the root", async function () {
    await expect(registry.connect(outsider).lockRoot(outsider.address))
      .to.be.revertedWithCustomError(registry, "NotDeployer")
      .withArgs(outsider.address);

    expect(await registry.isLocked()).to.equal(false);
    expect(await registry.rootAdmin()).to.equal(ethers.ZeroAddress);
  });
});
