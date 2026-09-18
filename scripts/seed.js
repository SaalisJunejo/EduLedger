/**
 * Seeds demo actors on the local chain: binds Hardhat's pre-funded test
 * accounts to EduLedger roles so the MVP can be demoed without live
 * onboarding (see docs/PRD.md, section 6).
 *
 *   account 0      Admin            (role admin, also the deployer)
 *   account 1      Instructor       (proposal initiator / session owner)
 *   account 2      HOD              (record-change approver)
 *   account 3      Exam Controller  (third signer, 2-of-3 threshold)
 *   accounts 4-6   Students         (device-bound attendance)
 *
 * Run after scripts/deploy.js against the same running Hardhat node:
 *   cd contracts && npm run seed:local
 */
const fs = require("fs");
const path = require("path");
const { createRequire } = require("module");

// `hardhat run` preloads the Hardhat runtime environment, but Node resolves
// `require("hardhat")` from this file's location (repo-root /scripts), so we
// pull the module from the contracts project's node_modules explicitly.
const requireFromContracts = createRequire(path.join(__dirname, "..", "contracts", "package.json"));
const hre = requireFromContracts("hardhat");

const DEMO_ACTORS = [
  { index: 1, label: "Instructor", role: "INSTRUCTOR_ROLE" },
  { index: 2, label: "HOD", role: "HOD_ROLE" },
  { index: 3, label: "Exam Controller", role: "EXAM_CONTROLLER_ROLE" },
];

async function main() {
  const network = hre.network.name;
  const deploymentFile = path.join(__dirname, "deployments", `${network}.json`);

  if (!fs.existsSync(deploymentFile)) {
    throw new Error(
      `No deployment found for network "${network}" (${deploymentFile}). Run the deploy step first.`
    );
  }

  const { contracts } = JSON.parse(fs.readFileSync(deploymentFile, "utf8"));
  const roles = await hre.ethers.getContractAt("EduLedgerRoles", contracts.roleRegistry);
  const signers = await hre.ethers.getSigners();

  console.log(`Seeding demo roles via ${contracts.roleRegistry}\n`);

  for (const actor of DEMO_ACTORS) {
    const signer = signers[actor.index];
    const roleId = await roles[actor.role]();
    const registered = await roles.hasRole(roleId, signer.address);

    if (registered) {
      console.log(`  = ${actor.label.padEnd(16)} ${signer.address}  (already ${actor.role})`);
      continue;
    }

    const tx = await roles.registerRole(signer.address, roleId);
    await tx.wait();
    console.log(`  + ${actor.label.padEnd(16)} ${signer.address}  ${actor.role}`);
  }

  console.log("\nDemo roles seeded. Account 0 (deployer) holds the admin role.");
  console.log("");
}

main().catch((error) => {
  console.error("\nSeeding failed:");
  console.error(error);
  process.exitCode = 1;
});
