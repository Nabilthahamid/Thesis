const hre = require("hardhat");

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  const approver = process.env.APPROVER_ADDRESS || deployer.address;

  console.log(`Deploying ThreatLedger from: ${deployer.address}`);
  console.log(`Initial approver: ${approver}`);

  const ledger = await hre.ethers.deployContract("ThreatLedger", [approver]);
  await ledger.waitForDeployment();

  const address = await ledger.getAddress();
  console.log(`ThreatLedger deployed to: ${address}`);
  console.log("");
  console.log("Add this to .env:");
  console.log(`THREAT_LEDGER_ADDRESS=${address}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

