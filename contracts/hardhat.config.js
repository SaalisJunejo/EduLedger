require("@nomicfoundation/hardhat-ethers");
// Adds `expect(...).to.emit(...)` / `revertedWithCustomError` support to Chai
// for the Mocha suite in test/.
require("@nomicfoundation/hardhat-chai-matchers");

/** @type import('hardhat/config').HardhatUserConfig */
module.exports = {
  // 0.8.24 is the minimum compiler version supported by OpenZeppelin v5.
  solidity: {
    version: "0.8.24",
    settings: {
      optimizer: {
        enabled: true,
        runs: 200,
      },
    },
  },
  networks: {
    // In-process network used by `npx hardhat test` and one-off scripts.
    hardhat: {
      chainId: 31337,
    },
    // Persistent local chain started with `npm run node` (see README).
    localhost: {
      url: "http://127.0.0.1:8545",
      chainId: 31337,
    },
  },
};
