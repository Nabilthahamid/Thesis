// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

contract ThreatLedger {
    struct Manifest {
        uint256 version;
        string manifestCid;
        bytes32 manifestSha256;
        uint256 rowCount;
        uint256 timestamp;
        address proposer;
        bool confirmed;
    }

    address public owner;
    address public approver;
    uint256 public latestVersion;
    uint256 public latestConfirmedVersion;

    mapping(uint256 => Manifest) private manifests;
    mapping(bytes32 => bool) public manifestHashUsed;

    event ManifestRegistered(
        uint256 indexed version,
        string manifestCid,
        bytes32 manifestSha256,
        uint256 rowCount,
        address indexed proposer
    );

    event ManifestConfirmed(
        uint256 indexed version,
        string manifestCid,
        bytes32 manifestSha256,
        uint256 rowCount
    );

    event ApproverChanged(address indexed oldApprover, address indexed newApprover);
    event OwnershipTransferred(address indexed oldOwner, address indexed newOwner);

    modifier onlyOwner() {
        require(msg.sender == owner, "ThreatLedger: caller is not owner");
        _;
    }

    modifier onlyApprover() {
        require(msg.sender == approver, "ThreatLedger: caller is not approver");
        _;
    }

    constructor(address initialApprover) {
        require(initialApprover != address(0), "ThreatLedger: approver is zero");
        owner = msg.sender;
        approver = initialApprover;
        emit OwnershipTransferred(address(0), msg.sender);
        emit ApproverChanged(address(0), initialApprover);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "ThreatLedger: owner is zero");
        address oldOwner = owner;
        owner = newOwner;
        emit OwnershipTransferred(oldOwner, newOwner);
    }

    function setApprover(address newApprover) external onlyOwner {
        require(newApprover != address(0), "ThreatLedger: approver is zero");
        address oldApprover = approver;
        approver = newApprover;
        emit ApproverChanged(oldApprover, newApprover);
    }

    function registerManifest(
        uint256 version,
        string calldata manifestCid,
        bytes32 manifestSha256,
        uint256 rowCount
    ) external onlyOwner {
        require(version == latestVersion + 1, "ThreatLedger: version must be next");
        require(bytes(manifestCid).length > 0, "ThreatLedger: CID is empty");
        require(manifestSha256 != bytes32(0), "ThreatLedger: SHA is empty");
        require(rowCount > 0, "ThreatLedger: row count is zero");
        require(!manifestHashUsed[manifestSha256], "ThreatLedger: manifest already registered");

        manifests[version] = Manifest({
            version: version,
            manifestCid: manifestCid,
            manifestSha256: manifestSha256,
            rowCount: rowCount,
            timestamp: block.timestamp,
            proposer: msg.sender,
            confirmed: false
        });

        manifestHashUsed[manifestSha256] = true;
        latestVersion = version;

        emit ManifestRegistered(version, manifestCid, manifestSha256, rowCount, msg.sender);
    }

    function confirmManifest(uint256 version) external onlyApprover {
        Manifest storage manifest = manifests[version];
        require(manifest.version != 0, "ThreatLedger: manifest not found");
        require(!manifest.confirmed, "ThreatLedger: already confirmed");
        require(version == latestConfirmedVersion + 1, "ThreatLedger: confirm previous version first");

        manifest.confirmed = true;
        latestConfirmedVersion = version;

        emit ManifestConfirmed(version, manifest.manifestCid, manifest.manifestSha256, manifest.rowCount);
    }

    function getManifest(uint256 version) external view returns (
        uint256,
        string memory,
        bytes32,
        uint256,
        uint256,
        address,
        bool
    ) {
        Manifest storage manifest = manifests[version];
        require(manifest.version != 0, "ThreatLedger: manifest not found");
        return (
            manifest.version,
            manifest.manifestCid,
            manifest.manifestSha256,
            manifest.rowCount,
            manifest.timestamp,
            manifest.proposer,
            manifest.confirmed
        );
    }

    function getLatestConfirmedManifest() external view returns (
        uint256,
        string memory,
        bytes32,
        uint256,
        uint256,
        address,
        bool
    ) {
        require(latestConfirmedVersion != 0, "ThreatLedger: no confirmed manifest");
        Manifest storage manifest = manifests[latestConfirmedVersion];
        return (
            manifest.version,
            manifest.manifestCid,
            manifest.manifestSha256,
            manifest.rowCount,
            manifest.timestamp,
            manifest.proposer,
            manifest.confirmed
        );
    }
}

