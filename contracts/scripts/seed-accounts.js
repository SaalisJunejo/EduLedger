/**
 * Exports the first 5 default Hardhat test accounts for local development.
 *
 *   #0  Deployer        (contract deployer / platform admin)
 *   #1  Instructor
 *   #2  HOD
 *   #3  ExamController
 *   #4  Student1
 *
 * Prints each account's address and private key to the console (LOCAL TEST
 * ONLY) and writes the labeled addresses - never the private keys - to
 * contracts/deployed/accounts.json for the backend to read later.
 *
 * Usage (from the /contracts folder):
 *   npm run seed:accounts
 *   # or: npx hardhat run scripts/seed-accounts.js [--network localhost]
 *
 * No running chain is required: the accounts are derived from the Hardhat
 * network's account configuration (the default test mnemonic), the same way
 * `npm run node` generates them.
 */
const fs = require("fs");
const path = require("path");
const hre = require("hardhat");

const DEFAULT_MNEMONIC = "test test test test test test test test test test test junk";
const DEFAULT_HD_PATH = "m/44'/60'/0'/0";

const ACCOUNT_DEFINITIONS = [
  { key: "deployer", label: "Deployer" },
  { key: "instructor", label: "Instructor" },
  { key: "hod", label: "HOD" },
  { key: "examController", label: "ExamController" },
  { key: "student1", label: "Student1" },
];

const OUTPUT_FILE = path.join(__dirname, "..", "deployed", "accounts.json");

/**
 * Derive the first `count` accounts of the local Hardhat network.
 * Handles both account config shapes:
 *   - HD accounts: { mnemonic, path, initialIndex, ... } (default)
 *   - explicit list: [{ privateKey, balance }, ...]
 */
function deriveAccounts(count) {
  const accountsConfig = hre.config.networks.hardhat.accounts;

  if (Array.isArray(accountsConfig)) {
    return accountsConfig
      .slice(0, count)
      .map((account) => new hre.ethers.Wallet(account.privateKey));
  }

  const mnemonic = accountsConfig?.mnemonic ?? DEFAULT_MNEMONIC;
  const basePath = accountsConfig?.path ?? DEFAULT_HD_PATH;
  const initialIndex = accountsConfig?.initialIndex ?? 0;

  return Array.from({ length: count }, (_, i) =>
    hre.ethers.HDNodeWallet.fromPhrase(mnemonic, undefined, `${basePath}/${initialIndex + i}`)
  );
}

async function main() {
  const wallets = deriveAccounts(ACCOUNT_DEFINITIONS.length);

  if (wallets.length < ACCOUNT_DEFINITIONS.length) {
    console.warn(
      `Warning: only ${wallets.length} Hardhat accounts are available, expected ${ACCOUNT_DEFINITIONS.length}.`
    );
  }

  console.log("");
  console.log("======================================================================");
  console.log("  EduLedger - local test accounts (first 5 default Hardhat accounts)");
  console.log("  LOCAL TEST ONLY — NEVER use in production.");
  console.log("  These private keys are publicly known. Any funds sent to these");
  console.log("  accounts on a real network WILL BE LOST.");
  console.log("======================================================================");

  const accounts = {};

  wallets.forEach((wallet, index) => {
    const definition = ACCOUNT_DEFINITIONS[index];

    accounts[definition.key] = {
      label: definition.label,
      index,
      address: wallet.address,
    };

    console.log("");
    console.log(`  #${index}  ${definition.label}`);
    console.log(`      address:     ${wallet.address}`);
    console.log(`      private key: ${wallet.privateKey}`);
  });

  const output = {
    network: hre.network.name,
    chainId: hre.network.config.chainId ?? 31337,
    generatedAt: new Date().toISOString(),
    source: "scripts/seed-accounts.js",
    note: "Local test accounts only. Private keys are intentionally not stored here; they are printed to the console by the script.",
    accounts,
  };

  fs.mkdirSync(path.dirname(OUTPUT_FILE), { recursive: true });
  fs.writeFileSync(OUTPUT_FILE, `${JSON.stringify(output, null, 2)}\n`);

  console.log("");
  console.log("----------------------------------------------------------------------");
  console.log(`Labeled addresses (no private keys) written to:`);
  console.log(`  ${path.relative(process.cwd(), OUTPUT_FILE)}`);
  console.log("");
}

main().catch((error) => {
  console.error("\nSeeding accounts failed:");
  console.error(error);
  process.exitCode = 1;
});
