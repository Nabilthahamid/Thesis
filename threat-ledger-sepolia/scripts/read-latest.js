const hre = require("hardhat");
const { requireContractAddress } = require("./lib/manifest");

function normalizeManifest(result) {
  return {
    version: result[0].toString(),
    manifestCid: result[1],
    manifestSha256: result[2],
    rowCount: result[3].toString(),
    timestamp: result[4].toString(),
    proposer: result[5],
    confirmed: result[6]
  };
}

async function main() {
  const address = requireContractAddress();
  const ledger = await hre.ethers.getContractAt("ThreatLedger", address);
  const latestVersion = await ledger.latestVersion();
  const latestConfirmedVersion = await ledger.latestConfirmedVersion();

  console.log(`Latest registered version: ${latestVersion}`);
  console.log(`Latest confirmed version: ${latestConfirmedVersion}`);

  if (latestConfirmedVersion > 0n) {
    const manifest = await ledger.getLatestConfirmedManifest();
    console.log(JSON.stringify(normalizeManifest(manifest), null, 2));
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

