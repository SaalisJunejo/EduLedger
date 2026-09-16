/**
 * Deploys RecordAuditTrail (Module 2 - multi-sig anti-tamper audit trail)
 * to the local Hardhat network, grants the three signer roles to the
 * corresponding addresses from deployed/accounts.json, and exports the
 * deployed address + ABI to deployed/RecordAuditTrail.json for the backend.
 *
 * Prerequisites (from the /contracts folder):
 *   npm run node           # terminal A - local chain at 127.0.0.1:8545
 *   npm run seed:accounts  # writes deployed/accounts.json (once)
 *
 * Usage (terminal B):
 *   npm run deploy:audit-trail
 *   # or: npx hardhat run scripts/deploy.js --network localhost
 */
const fs = require("fs");
const path = require("path");
const hre = require("hardhat");

const CONTRACT_NAME = "RecordAuditTrail";
const DEPLOYED_DIR = path.join(__dirname, "..", "deployed");
const ACCOUNTS_FILE = path.join(DEPLOYED_DIR, "accounts.json");
const OUTPUT_FILE = path.join(DEPLOYED_DIR, `${CONTRACT_NAME}.json`);

// accounts.json entry -> on-chain role to grant (the deployer stays admin)
const ROLE_GRANTS = [
  { role: "INSTRUCTOR_ROLE", account: "instructor", label: "Instructor" },
  { role: "HOD_ROLE", account: "hod", label: "HOD" },
  { role: "EXAM_CONTROLLER_ROLE", account: "examController", label: "Exam Controller" },
];

function readAccounts() {
  if (!fs.existsSync(ACCOUNTS_FILE)) {
    throw new Error(
      `${path.relative(process.cwd(), ACCOUNTS_FILE)} not found. Run \`npm run seed:accounts\` first.`
    );
  }
  return JSON.parse(fs.readFileSync(ACCOUNTS_FILE, "utf8")).accounts;
}

async function main() {
  const accounts = readAccounts();

  const [deployer] = await hre.ethers.getSigners();
  const chainId = Number((await hre.ethers.provider.getNetwork()).chainId);

  console.log(`Deploying ${CONTRACT_NAME}`);
  console.log(`  network : ${hre.network.name} (chain id ${chainId})`);
  console.log(`  deployer: ${deployer.address}\n`);

  const auditTrail = await hre.ethers.deployContract(CONTRACT_NAME, [deployer.address]);
  await auditTrail.waitForDeployment();
  const address = await auditTrail.getAddress();
  console.log(`  ${CONTRACT_NAME}  ->  ${address}`);

  console.log("\nGranting roles:");
  const roles = {};
  for (const { role, account, label } of ROLE_GRANTS) {
    const target = accounts[account]?.address;
    if (!target) {
      throw new Error(`accounts.json has no "${account}" entry - run \`npm run seed:accounts\`.`);
    }

    const roleId = await auditTrail[role]();
    const tx = await auditTrail.grantRole(roleId, target);
    await tx.wait();

    const verified = await auditTrail.hasRole(roleId, target);
    console.log(
      `  ${role.padEnd(20)} -> ${label.padEnd(16)} ${target}  ${verified ? "(verified)" : "(FAILED)"}`
    );

    roles[role] = { label, address: target };
  }

  const artifact = await hre.artifacts.readArtifact(CONTRACT_NAME);

  const exported = {
    contractName: CONTRACT_NAME,
    network: hre.network.name,
    chainId,
    address,
    deployer: deployer.address,
    deployedAt: new Date().toISOString(),
    roles,
    abi: artifact.abi,
  };

  fs.mkdirSync(DEPLOYED_DIR, { recursive: true });
  fs.writeFileSync(OUTPUT_FILE, `${JSON.stringify(exported, null, 2)}\n`);

  console.log(`\nAddress + ABI written to ${path.relative(process.cwd(), OUTPUT_FILE)}`);
  console.log("");
}

main().catch((error) => {
  console.error("\nDeployment failed:");
  console.error(error);
  process.exitCode = 1;
});
