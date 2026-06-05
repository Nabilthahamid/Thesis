const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("ThreatLedger", function () {
  const cidV1 = "bafkreieu23hdvbqt5pqhkkgfvhr2rzmwsuxn3v6sjrks4ftvzzwfpmg6fq";
  const cidV2 = "bafkreiaaacy3rgbqcvmwklv3jcompsqyrd74bulfmlvg6lhyaqkc44nr5m";
  const shaV1 = "0x" + "11".repeat(32);
  const shaV2 = "0x" + "22".repeat(32);

  async function deployFixture() {
    const [owner, approver, attacker] = await ethers.getSigners();
    const ledger = await ethers.deployContract("ThreatLedger", [approver.address]);
    await ledger.waitForDeployment();
    return { ledger, owner, approver, attacker };
  }

  it("allows the owner to register a manifest", async function () {
    const { ledger, owner } = await deployFixture();

    await expect(ledger.registerManifest(1, cidV1, shaV1, 280))
      .to.emit(ledger, "ManifestRegistered")
      .withArgs(1, cidV1, shaV1, 280, owner.address);

    expect(await ledger.latestVersion()).to.equal(1);

    const manifest = await ledger.getManifest(1);
    expect(manifest[0]).to.equal(1);
    expect(manifest[1]).to.equal(cidV1);
    expect(manifest[2]).to.equal(shaV1);
    expect(manifest[3]).to.equal(280);
    expect(manifest[5]).to.equal(owner.address);
    expect(manifest[6]).to.equal(false);
  });

  it("allows the approver to confirm a manifest", async function () {
    const { ledger, approver } = await deployFixture();

    await ledger.registerManifest(1, cidV1, shaV1, 280);

    await expect(ledger.connect(approver).confirmManifest(1))
      .to.emit(ledger, "ManifestConfirmed")
      .withArgs(1, cidV1, shaV1, 280);

    expect(await ledger.latestConfirmedVersion()).to.equal(1);
    const manifest = await ledger.getLatestConfirmedManifest();
    expect(manifest[6]).to.equal(true);
  });

  it("rejects confirmation from a non-approver", async function () {
    const { ledger, attacker } = await deployFixture();

    await ledger.registerManifest(1, cidV1, shaV1, 280);

    await expect(ledger.connect(attacker).confirmManifest(1))
      .to.be.revertedWith("ThreatLedger: caller is not approver");
  });

  it("rejects duplicate or skipped versions", async function () {
    const { ledger } = await deployFixture();

    await expect(ledger.registerManifest(2, cidV1, shaV1, 280))
      .to.be.revertedWith("ThreatLedger: version must be next");

    await ledger.registerManifest(1, cidV1, shaV1, 280);

    await expect(ledger.registerManifest(1, cidV2, shaV2, 50))
      .to.be.revertedWith("ThreatLedger: version must be next");
  });

  it("rejects duplicate manifest content hashes", async function () {
    const { ledger, approver } = await deployFixture();

    await ledger.registerManifest(1, cidV1, shaV1, 280);
    await ledger.connect(approver).confirmManifest(1);

    await expect(ledger.registerManifest(2, cidV2, shaV1, 280))
      .to.be.revertedWith("ThreatLedger: manifest already registered");
  });

  it("requires sequential confirmation", async function () {
    const { ledger, approver } = await deployFixture();

    await ledger.registerManifest(1, cidV1, shaV1, 280);
    await ledger.registerManifest(2, cidV2, shaV2, 50);

    await expect(ledger.connect(approver).confirmManifest(2))
      .to.be.revertedWith("ThreatLedger: confirm previous version first");
  });
});

