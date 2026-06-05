const hre = require("hardhat");
const { getVersionArg, requireContractAddress } = require("./lib/manifest");

async function main() {
  const address = requireContractAddress();
  const ledger = await hre.ethers.getContractAt("ThreatLedger", address);
  const version = getVersionArg() || await ledger.latestVersion();

  if (version === 0n) {
    throw new Error("No manifest has been registered yet");
  }

  console.log(`Confirming manifest version: ${version}`);
  const tx = await ledger.confirmManifest(version);
  console.log(`Transaction sent: ${tx.hash}`);
  await tx.wait();
  console.log("Manifest confirmed.");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

