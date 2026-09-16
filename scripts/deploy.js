/**
 * Deploys the EduLedger contract suite to a local Hardhat network and writes
 * the resulting addresses to scripts/deployments/<network>.json.
 *
 * Prerequisites (from the /contracts folder):
 *   npm install
 *   npm run node           # terminal A - keeps the local chain alive
 *   npm run deploy:local   # terminal B - runs this script via Hardhat
 *
 * Module contracts (attendance engine, multi-sig audit trail, faculty
 * workload ledger) will be added to the `contracts` map below as they are
 * built; the backend reads the same keys from backend/.env.
 */
const fs = require("fs");
const path = require("path");
const { createRequire } = require("module");

// `hardhat run` preloads the Hardhat runtime environment, but Node resolves
// `require("hardhat")` from this file's location (repo-root /scripts), so we
// pull the module from the contracts project's node_modules explicitly.
const requireFromContracts = createRequire(path.join(__dirname, "..", "contracts", "package.json"));
const hre = requireFromContracts("hardhat");

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  const network = hre.network.name;
  const chainId = Number((await hre.ethers.provider.getNetwork()).chainId);

  console.log("Deploying EduLedger contracts");
  console.log(`  network : ${network} (chain id ${chainId})`);
  console.log(`  deployer: ${deployer.address}\n`);

  const roleRegistry = await hre.ethers.deployContract("EduLedgerRoles", [deployer.address]);
  await roleRegistry.waitForDeployment();
  const roleRegistryAddress = await roleRegistry.getAddress();
  console.log(`  EduLedgerRoles  ->  ${roleRegistryAddress}`);

  const deployment = {
    network,
    chainId,
    deployer: deployer.address,
    deployedAt: new Date().toISOString(),
    contracts: {
      roleRegistry: roleRegistryAddress,
    },
  };

  const deploymentsDir = path.join(__dirname, "deployments");
  fs.mkdirSync(deploymentsDir, { recursive: true });
  const deploymentFile = path.join(deploymentsDir, `${network}.json`);
  fs.writeFileSync(deploymentFile, `${JSON.stringify(deployment, null, 2)}\n`);

  console.log(`\nSaved to ${deploymentFile}`);
  console.log("\nAdd these values to backend/.env (created from backend/.env.example):\n");
  console.log("HARDHAT_RPC_URL=http://127.0.0.1:8545");
  console.log(`CHAIN_ID=${chainId}`);
  console.log(`ROLE_REGISTRY_CONTRACT_ADDRESS=${roleRegistryAddress}`);
  console.log("");
}

main().catch((error) => {
  console.error("\nDeployment failed:");
  console.error(error);
  process.exitCode = 1;
});
