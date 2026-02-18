// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {StdCheats} from "forge-std/StdCheats.sol";
import {StrategyHub} from "../src/StrategyHub.sol";
import {YieldVault} from "../src/Vault.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

contract Deploy is Script, StdCheats {
    function run() external {
        uint256 deployerPrivateKey = vm.envUint("PRIVATE_KEY");
        address keeper = vm.envAddress("KEEPER_ADDRESS");
        
        address usdc = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48;
        address aavePool = 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2;
        address aUsdc = 0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c;
        address compoundComet = 0xc3d688B66703497DAA19211EEdff47f25384cdc3;

        vm.startBroadcast(deployerPrivateKey);

        // 2. Deployments
        StrategyHub hub = new StrategyHub(usdc, aavePool, aUsdc, compoundComet);
        console2.log("StrategyHub deployed to:", address(hub));

        YieldVault vault = new YieldVault(
            IERC20(usdc),
            "AI Yield Vault",
            "aiUSDC",
            address(hub)
        );
        console2.log("YieldVault deployed to:", address(vault));

        // 3. Setup Roles
        bytes32 KEEPER_ROLE = hub.KEEPER_ROLE();
        bytes32 VAULT_ROLE = hub.VAULT_ROLE();

        hub.grantRole(VAULT_ROLE, address(vault));
        hub.grantRole(KEEPER_ROLE, keeper);
        vault.grantRole(KEEPER_ROLE, keeper);

        vm.stopBroadcast();
    }
}
