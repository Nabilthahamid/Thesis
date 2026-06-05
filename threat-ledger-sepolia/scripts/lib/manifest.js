const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const DEFAULT_MANIFEST = path.resolve(
  __dirname,
  "..",
  "..",
  "..",
  "JailBreakV_28K",
  "JailBreakV_28k",
  "ipfs_cid_outputs",
  "mini_JailBreakV_28K_cids.csv"
);

function getArg(name) {
  const index = process.argv.indexOf(name);
  if (index === -1 || index + 1 >= process.argv.length) {
    return undefined;
  }
  return process.argv[index + 1];
}

function getManifestPath() {
  return path.resolve(getArg("--path") || process.env.MANIFEST_CSV || DEFAULT_MANIFEST);
}

function getManifestInfo(manifestPath) {
  const bytes = fs.readFileSync(manifestPath);
  const text = bytes.toString("utf8").replace(/^\uFEFF/, "");
  const lines = text.split(/\r?\n/).filter((line) => line.trim().length > 0);

  if (lines.length === 0) {
    throw new Error(`Manifest has no header: ${manifestPath}`);
  }

  const headers = lines[0].split(",").map((value) => value.trim());
  if (!headers.includes("id") || !headers.includes("ipfs_cid")) {
    throw new Error("Manifest CSV must contain id and ipfs_cid columns");
  }

  return {
    path: manifestPath,
    sha256Hex: crypto.createHash("sha256").update(bytes).digest("hex"),
    sha256Bytes32: `0x${crypto.createHash("sha256").update(bytes).digest("hex")}`,
    rowCount: lines.length - 1
  };
}

function requireContractAddress() {
  const address = process.env.THREAT_LEDGER_ADDRESS || getArg("--address");
  if (!address) {
    throw new Error("Set THREAT_LEDGER_ADDRESS in .env or pass --address <contract>");
  }
  return address;
}

function requireManifestCid() {
  const cid = process.env.MANIFEST_CID || getArg("--cid");
  if (!cid) {
    throw new Error("Set MANIFEST_CID in .env or pass --cid <ipfs-cid>");
  }
  return cid;
}

function getVersionArg() {
  const value = getArg("--version") || process.env.MANIFEST_VERSION;
  return value ? BigInt(value) : undefined;
}

module.exports = {
  DEFAULT_MANIFEST,
  getArg,
  getManifestInfo,
  getManifestPath,
  getVersionArg,
  requireContractAddress,
  requireManifestCid
};

