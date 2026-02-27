#!/usr/bin/env node

/**
 * Generates a new EVM-compatible wallet using ethers.js.
 * Outputs JSON: { address, privateKey, mnemonic }
 *
 * Usage: node generate_wallet.js
 * Requires: ethers@6 (installed locally or via npx)
 */

async function main() {
  let ethers;
  try {
    ethers = require("ethers");
  } catch {
    console.error("ethers not found. Install with: npm install ethers@6");
    process.exit(1);
  }

  const w = ethers.Wallet.createRandom();
  console.log(
    JSON.stringify({
      address: w.address,
      privateKey: w.privateKey,
      mnemonic: w.mnemonic.phrase,
    })
  );
}

main();
