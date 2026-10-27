/**
 * Deploys GovernanceRegistry (Phase 0.5 - system admin bootstrap anchor)
 * to the local Hardhat network and exports the deployed address + ABI to
 * deployed/GovernanceRegistry.json for the backend.
 *
 * This script deploys ONLY GovernanceRegistry - the other four contracts
 * keep their existing addresses. Because Hardhat addresses are derived from
 * the deployer's nonce, always run this one LAST on a fresh chain
 * (deploy:audit-trail, deploy:attendance, deploy:local, deploy:workload,
 * then deploy:governance), so the addresses already recorded in backend/.env
 * never shift.
 *
 * The registry is deployed UNLOCKED: the root admin address is written once,
 * by the backend's POST /api/v1/auth/bootstrap/admin endpoint, which signs
 * lockRoot() with the deployer key. The lock is permanent for the lifetime
 * of the chain - restarting the Hardhat node is the only way to clear it.
 *
 * Prerequisites (from the /contracts folder):
 *   npm run node    # terminal A - local chain at 127.0.0.1:8545
 *
 * Usage (terminal B):
 *   npm run deploy:governance
 *   # or: npx hardhat run scripts/deploy-governance.js --network localhost
 */
const fs = require("fs");
const path = require("path");
const hre = require("hardhat");

const CONTRACT_NAME = "GovernanceRegistry";
const DEPLOYED_DIR = path.join(__dirname, "..", "deployed");
const OUTPUT_FILE = path.join(DEPLOYED_DIR, `${CONTRACT_NAME}.json`);

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  const chainId = Number((await hre.ethers.provider.getNetwork()).chainId);

  console.log(`Deploying ${CONTRACT_NAME}`);
  console.log(`  network : ${hre.network.name} (chain id ${chainId})`);
  console.log(`  deployer: ${deployer.address}\n`);

  const registry = await hre.ethers.deployContract(CONTRACT_NAME);
  await registry.waitForDeployment();
  const address = await registry.getAddress();
  console.log(`  ${CONTRACT_NAME}  ->  ${address}`);

  // The deployer is the only account that may place the one-time root lock,
  // so the backend's bootstrap signer must be this same key.
  const locked = await registry.isLocked();
  const rootAdmin = await registry.rootAdmin();
  console.log("\nRoot state:");
  console.log(`  ${"isLocked".padEnd(12)} -> ${locked}`);
  console.log(`  ${"rootAdmin".padEnd(12)} -> ${rootAdmin}`);
  console.log(`  ${"lockRoot by".padEnd(12)} -> ${deployer.address} (deployer only, once)`);

  const artifact = await hre.artifacts.readArtifact(CONTRACT_NAME);

  const exported = {
    contractName: CONTRACT_NAME,
    network: hre.network.name,
    chainId,
    address,
    deployer: deployer.address,
    deployedAt: new Date().toISOString(),
    roles: {
      DEPLOYER: {
        label: "Deployer (the only account allowed to call lockRoot once)",
        address: deployer.address,
      },
    },
    abi: artifact.abi,
  };

  fs.mkdirSync(DEPLOYED_DIR, { recursive: true });
  fs.writeFileSync(OUTPUT_FILE, `${JSON.stringify(exported, null, 2)}\n`);

  console.log(`\nAddress + ABI written to ${path.relative(process.cwd(), OUTPUT_FILE)}`);
  console.log(`Set GOVERNANCE_CONTRACT_ADDRESS=${address} in backend/.env`);
  console.log("");
}

main().catch((error) => {
  console.error("\nDeployment failed:");
  console.error(error);
  process.exitCode = 1;
});
