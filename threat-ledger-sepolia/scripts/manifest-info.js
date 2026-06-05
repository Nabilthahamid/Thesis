const { getManifestInfo, getManifestPath } = require("./lib/manifest");

async function main() {
  const info = getManifestInfo(getManifestPath());
  console.log(JSON.stringify(info, null, 2));
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

