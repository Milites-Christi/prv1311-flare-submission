// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @dev Minimal registry interface -- same address on every Flare network,
/// confirmed empirically against Coston2 before this contract was written
/// (see flare/README.md). Resolves the FtsoV2 and FeeCalculator addresses
/// by name rather than hardcoding either, since only the registry address
/// itself is stable across networks and protocol upgrades.
interface IFtsoRegistry {
    function getContractAddressByName(string calldata _name) external view returns (address);
}

/// @dev getFeedById is `external payable`, not `view` -- a fee may be
/// required (currently zero on both Coston2 and mainnet, per FeeCalculator,
/// but this contract never assumes that stays true).
interface IFtsoV2 {
    function getFeedById(bytes21 _feedId)
        external
        payable
        returns (uint256 _value, int8 _decimals, uint64 _timestamp);
}

interface IFeeCalculator {
    function calculateFeeByIds(bytes21[] calldata _feedIds) external view returns (uint256 _fee);
}

/// @title DivergenceAnchor
/// @notice Anchors FTSOv2-vs-centralized-venue price divergence on-chain,
/// with the oracle side read live from FtsoV2 at call time -- never accepted
/// as a function argument. Accepting the oracle value as an argument would
/// make this contract a database of our own claims rather than independent
/// on-chain evidence; that distinction is the entire point of the design.
///
/// No owner, no proxy, no upgradability, no held funds. The one operational
/// risk that comes with "no upgradability" -- the registry re-pointing
/// FtsoV2/FeeCalculator to new addresses after a protocol upgrade -- is
/// covered by refreshAddresses() below instead of a privileged admin role.
contract DivergenceAnchor {
    address public constant REGISTRY = 0xaD67FE66660Fb8dFE9d6b1b4240d8650e30F6019;

    address public ftsoV2;
    address public feeCalculator;

    struct Record {
        uint256 oracleValue;
        int8 oracleDecimals;
        uint64 oracleTimestamp;
        uint256 venueValue;
        uint8 venueDecimals;
        int256 divergenceBps;
        bytes32 decisionHash;
        uint256 recordedAt;
    }

    /// @dev Latest record per feed. History lives in the DivergenceRecorded
    /// event log, not in storage -- events are cheap and fully queryable;
    /// an on-chain array of every reading never was.
    mapping(bytes21 => Record) public latestByFeed;

    event DivergenceRecorded(
        bytes21 indexed feedId,
        uint256 oracleValue,
        uint64 oracleTimestamp,
        uint256 venueValue,
        int256 divergenceBps,
        bytes32 decisionHash,
        uint256 blockTimestamp
    );

    event AddressesRefreshed(address ftsoV2, address feeCalculator);

    constructor() {
        _refreshAddresses();
    }

    /// @notice Re-resolves FtsoV2 and FeeCalculator from the registry.
    /// Permissionless by design -- anyone can call this, at any time. This
    /// is the fix for registry-staleness risk that does not require an
    /// owner, a proxy, or any privileged role: if Flare ever re-registers
    /// either contract, anyone (not just us) can bring this anchor current.
    function refreshAddresses() external {
        _refreshAddresses();
    }

    function _refreshAddresses() internal {
        ftsoV2 = IFtsoRegistry(REGISTRY).getContractAddressByName("FtsoV2");
        // FeeCalculator is not guaranteed to exist on every network. Its
        // absence must not break this contract's actual purpose, which is
        // reading FtsoV2 -- so a failed resolution just means "treat the
        // fee as zero," not "this contract is broken."
        try IFtsoRegistry(REGISTRY).getContractAddressByName("FeeCalculator") returns (address addr) {
            feeCalculator = addr;
        } catch {
            feeCalculator = address(0);
        }
        emit AddressesRefreshed(ftsoV2, feeCalculator);
    }

    /// @notice Records the divergence between FTSOv2's live value for
    /// `feedId` and an off-chain venue price. Resolves and pays the current
    /// FtsoV2 fee itself, at call time -- callers should send generous
    /// msg.value; any excess over the actual fee is refunded.
    /// @param feedId Flare 21-byte feed id (category byte + ticker, zero-padded).
    /// @param venueValue Venue price scaled by 10**venueDecimals.
    /// @param venueDecimals Decimal scale of venueValue.
    /// @param decisionHash keccak256 commitment to the off-chain decision
    /// context this call anchors (e.g. the canonical JSON of the
    /// corresponding rider_decisions row -- symbol, every gate verdict,
    /// block reason, prices used). Committing this hash on-chain BEFORE the
    /// outcome exists is what makes the reasoning provable later: anyone
    /// who is later given the row can recompute this hash and confirm
    /// nothing was revised after the fact.
    function recordDivergence(
        bytes21 feedId,
        uint256 venueValue,
        uint8 venueDecimals,
        bytes32 decisionHash
    ) external payable {
        uint256 fee = 0;
        if (feeCalculator != address(0)) {
            bytes21[] memory ids = new bytes21[](1);
            ids[0] = feedId;
            fee = IFeeCalculator(feeCalculator).calculateFeeByIds(ids);
        }
        require(msg.value >= fee, "insufficient fee");

        (uint256 oracleValue, int8 oracleDecimals, uint64 oracleTimestamp) =
            IFtsoV2(ftsoV2).getFeedById{value: fee}(feedId);

        int256 divergenceBps = _divergenceBps(oracleValue, oracleDecimals, venueValue, venueDecimals);

        // --- effects: write state and emit before any external interaction ---
        latestByFeed[feedId] = Record({
            oracleValue: oracleValue,
            oracleDecimals: oracleDecimals,
            oracleTimestamp: oracleTimestamp,
            venueValue: venueValue,
            venueDecimals: venueDecimals,
            divergenceBps: divergenceBps,
            decisionHash: decisionHash,
            recordedAt: block.timestamp
        });

        emit DivergenceRecorded(
            feedId, oracleValue, oracleTimestamp, venueValue, divergenceBps, decisionHash, block.timestamp
        );

        // --- interaction, LAST: checks-effects-interactions. Fee is 0 today
        // (confirmed empirically on Coston2) so this refund path likely
        // never fires -- but if FeeCalculator ever starts charging and a
        // caller overpays, state is already written and the event already
        // emitted before this external call happens. ---
        uint256 refund = msg.value - fee;
        if (refund > 0) {
            (bool ok, ) = msg.sender.call{value: refund}("");
            require(ok, "refund failed");
        }
    }

    function _scaleTo18(uint256 value, int8 decimals) internal pure returns (uint256) {
        require(decimals >= 0 && decimals <= 18, "decimals out of range");
        return value * (10 ** uint256(18 - uint8(decimals)));
    }

    function _divergenceBps(
        uint256 oracleValue,
        int8 oracleDecimals,
        uint256 venueValue,
        uint8 venueDecimals
    ) internal pure returns (int256) {
        uint256 oracleScaled = _scaleTo18(oracleValue, oracleDecimals);
        uint256 venueScaled = _scaleTo18(venueValue, int8(venueDecimals));
        require(venueScaled > 0, "venue value zero");
        return (int256(oracleScaled) - int256(venueScaled)) * 10000 / int256(venueScaled);
    }
}
