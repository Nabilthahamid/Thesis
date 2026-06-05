const hre = require("hardhat");
const {
  getManifestInfo,
  getManifestPath,
  getVersionArg,
  requireContractAddress,
  requireManifestCid
} = require("./lib/manifest");

async function main() {
  const address = requireContractAddress();
  const manifestCid = requireManifestCid();
  const info = getManifestInfo(getManifestPath());
  const ledger = await hre.ethers.getContractAt("ThreatLedger", address);

  const requestedVersion = getVersionArg();
  const version = requestedVersion || (await ledger.latestVersion()) + 1n;

  console.log(`Registering manifest version: ${version}`);
  console.log(`Manifest CID: ${manifestCid}`);
  console.log(`Manifest SHA-256: ${info.sha256Bytes32}`);
  console.log(`Row count: ${info.rowCount}`);

  const tx = await ledger.registerManifest(
    version,
    manifestCid,
    info.sha256Bytes32,
    info.rowCount
  );
  console.log(`Transaction sent: ${tx.hash}`);
  await tx.wait();
  console.log("Manifest registered.");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

