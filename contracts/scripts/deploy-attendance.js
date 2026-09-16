/**
 * Deploys AttendanceLedger (Module 1 - Zero-Proxy Attendance Engine anchor)
 * to the local Hardhat network, grants BACKEND_ROLE to the deployer account
 * (which acts as the trusted backend signer during local development), and
 * exports the deployed address + ABI to deployed/AttendanceLedger.json for
 * the backend.
 *
 * Prerequisites (from the /contracts folder):
 *   npm run node    # terminal A - local chain at 127.0.0.1:8545
 *
 * Usage (terminal B):
 *   npm run deploy:attendance
 *   # or: npx hardhat run scripts/deploy-attendance.js --network localhost
 */
const fs = require("fs");
const path = require("path");
const hre = require("hardhat");

const CONTRACT_NAME = "AttendanceLedger";
const DEPLOYED_DIR = path.join(__dirname, "..", "deployed");
const OUTPUT_FILE = path.join(DEPLOYED_DIR, `${CONTRACT_NAME}.json`);

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  const chainId = Number((await hre.ethers.provider.getNetwork()).chainId);

  console.log(`Deploying ${CONTRACT_NAME}`);
  console.log(`  network : ${hre.network.name} (chain id ${chainId})`);
  console.log(`  deployer: ${deployer.address}\n`);

  const ledger = await hre.ethers.deployContract(CONTRACT_NAME, [deployer.address]);
  await ledger.waitForDeployment();
  const address = await ledger.getAddress();
  console.log(`  ${CONTRACT_NAME}  ->  ${address}`);

  // For now the deployer doubles as the trusted backend signer; later the
  // admin can grant BACKEND_ROLE to the real backend's signing address.
  console.log("\nGranting roles:");
  const backendRole = await ledger.BACKEND_ROLE();
  const tx = await ledger.grantRole(backendRole, deployer.address);
  await tx.wait();

  const verified = await ledger.hasRole(backendRole, deployer.address);
  console.log(
    `  ${"BACKEND_ROLE".padEnd(20)} -> ${"Backend/Deployer".padEnd(16)} ${deployer.address}  ${
      verified ? "(verified)" : "(FAILED)"
    }`
  );

  const artifact = await hre.artifacts.readArtifact(CONTRACT_NAME);

  const exported = {
    contractName: CONTRACT_NAME,
    network: hre.network.name,
    chainId,
    address,
    deployer: deployer.address,
    deployedAt: new Date().toISOString(),
    roles: {
      BACKEND_ROLE: {
        label: "Backend (deployer acts as the trusted backend signer)",
        address: deployer.address,
      },
    },
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
