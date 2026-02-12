// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import "../src/StrategyHub.sol";

/**
 * @title ForkTest
 * @notice Tests the full rebalancing flow on a forked Ethereum mainnet.
 *         Uses REAL Aave V3 and Compound V3 contracts with real USDC.
 *
 * Run with:
 *   forge test --fork-url https://eth-mainnet.g.alchemy.com/v2/YOUR_KEY -vvv
 */
contract ForkTest is Test {
    // ========== Mainnet Addresses ==========
    address constant USDC = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48;
    address constant AAVE_POOL = 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2;
    address constant AUSDC = 0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c;     // Aave V3 aUSDC
    address constant COMPOUND_COMET = 0xc3d688B66703497DAA19211EEdff47f25384cdc3; // Compound V3 USDC

    // Large USDC holder to impersonate (Circle's hot wallet)
    address constant USDC_WHALE = 0x37305B1cD40574E4C5Ce33f8e8306Be057fD7341;

    StrategyHub public hub;
    address public keeper;
    uint256 constant DEPOSIT_AMOUNT = 100_000 * 1e6; // 100K USDC

    function setUp() public {
        keeper = address(this);

        // Deploy StrategyHub
        hub = new StrategyHub(
            USDC,
            AAVE_POOL,
            AUSDC,
            COMPOUND_COMET
        );

        // Set rebalance interval to 0 for testing
        hub.setMinRebalanceInterval(0);
        // Allow 100% moves for testing
        hub.setMaxSingleMove(10000);

        // Impersonate a USDC whale and send us USDC
        vm.startPrank(USDC_WHALE);
        IERC20(USDC).transfer(address(this), DEPOSIT_AMOUNT);
        vm.stopPrank();

        // Approve hub to spend our USDC
        IERC20(USDC).approve(address(hub), type(uint256).max);
    }

    // ========== Test: Deposit & Deploy ==========

    function test_deposit() public {
        uint256 balBefore = IERC20(USDC).balanceOf(address(this));

        hub.deposit(DEPOSIT_AMOUNT);

        // USDC should have left our wallet
        assertEq(IERC20(USDC).balanceOf(address(this)), balBefore - DEPOSIT_AMOUNT);

        // Hub should have deployed to Aave + Compound
        (uint256 aaveBal, uint256 compBal, uint256 idle, uint256 total) = hub.getBalances();

        console.log("=== After Deposit ===");
        console.log("Aave Balance:     ", aaveBal / 1e6, "USDC");
        console.log("Compound Balance: ", compBal / 1e6, "USDC");
        console.log("Idle Balance:     ", idle / 1e6, "USDC");
        console.log("Total Value:      ", total / 1e6, "USDC");

        // Total should be approximately what we deposited (minus dust)
        assertApproxEqAbs(total, DEPOSIT_AMOUNT, 100); // Within 100 wei
        assertTrue(aaveBal > 0, "Aave should have funds");
        assertTrue(compBal > 0, "Compound should have funds");
    }

    // ========== Test: Rebalance (Aave-heavy) ==========

    function test_rebalance_to_aave() public {
        hub.deposit(DEPOSIT_AMOUNT);

        // AI says: move 80% to Aave, 20% to Compound
        hub.rebalance(8000, 2000);

        (uint256 aaveBal, uint256 compBal, , uint256 total) = hub.getBalances();

        console.log("=== After Rebalance (80/20 Aave) ===");
        console.log("Aave Balance:     ", aaveBal / 1e6, "USDC");
        console.log("Compound Balance: ", compBal / 1e6, "USDC");
        console.log("Total Value:      ", total / 1e6, "USDC");

        // Aave should have ~80% of total
        uint256 aavePct = (aaveBal * 10000) / total;
        console.log("Aave %:           ", aavePct);
        assertApproxEqAbs(aavePct, 8000, 200); // Within 2% tolerance

        // Allocation state updated
        assertEq(hub.aaveAllocationBps(), 8000);
        assertEq(hub.compoundAllocationBps(), 2000);
    }

    // ========== Test: Rebalance (Compound-heavy) ==========

    function test_rebalance_to_compound() public {
        hub.deposit(DEPOSIT_AMOUNT);

        // AI says: move 30% to Aave, 70% to Compound
        hub.rebalance(3000, 7000);

        (uint256 aaveBal, uint256 compBal, , uint256 total) = hub.getBalances();

        console.log("=== After Rebalance (30/70 Compound) ===");
        console.log("Aave Balance:     ", aaveBal / 1e6, "USDC");
        console.log("Compound Balance: ", compBal / 1e6, "USDC");

        uint256 compPct = (compBal * 10000) / total;
        console.log("Compound %:       ", compPct);
        assertApproxEqAbs(compPct, 7000, 200);
    }

    // ========== Test: Full Cycle (Deposit -> Rebalance -> Withdraw) ==========

    function test_full_cycle() public {
        uint256 startBalance = IERC20(USDC).balanceOf(address(this));
        console.log("=== Full Cycle Test ===");
        console.log("Starting USDC:    ", startBalance / 1e6);

        // 1. Deposit
        hub.deposit(DEPOSIT_AMOUNT);
        console.log("Deposited:        ", DEPOSIT_AMOUNT / 1e6, "USDC");

        // 2. Rebalance to 90/10 Aave
        hub.rebalance(9000, 1000);
        (uint256 a1, uint256 c1, , ) = hub.getBalances();
        console.log("After rebalance -> Aave:", a1 / 1e6, "Compound:", c1 / 1e6);

        // 3. Simulate time passing (yield accrues)
        vm.warp(block.timestamp + 30 days);
        vm.roll(block.number + 216000); // ~30 days of blocks

        (uint256 a2, uint256 c2, , uint256 totalAfter) = hub.getBalances();
        console.log("After 30 days -> Aave:", a2 / 1e6, "Compound:", c2 / 1e6);
        console.log("Total value:      ", totalAfter / 1e6, "USDC");

        // Value should have grown (yield earned)
        // Note: On a static fork, balances won't magically update since
        // we aren't simulating the Aave/Compound interest accrual.
        // In a real scenario, aToken balances increase automatically.

        // 4. Withdraw everything
        hub.withdraw(hub.totalValue());

        uint256 endBalance = IERC20(USDC).balanceOf(address(this));
        console.log("Final USDC:       ", endBalance / 1e6);

        // Should get back at least what we put in (plus yield)
        assertTrue(endBalance >= startBalance, "Should not lose money");
        console.log("Yield earned:     ", (endBalance - startBalance) / 1e6, "USDC");
    }

    // ========== Test: Emergency Withdrawal ==========

    function test_emergency_withdraw() public {
        hub.deposit(DEPOSIT_AMOUNT);

        // Emergency: pull everything out
        hub.emergencyWithdrawAll();

        assertTrue(hub.paused(), "Should be paused after emergency");

        uint256 hubBalance = IERC20(USDC).balanceOf(address(hub));
        console.log("Hub USDC after emergency: ", hubBalance / 1e6);

        // Most funds should be back in the hub (idle)
        assertApproxEqAbs(hubBalance, DEPOSIT_AMOUNT, 1000);
    }

    // ========== Test: Invalid Allocation Reverts ==========

    function test_revert_invalid_allocation() public {
        hub.deposit(DEPOSIT_AMOUNT);

        // 60% + 50% = 110% -> should revert
        vm.expectRevert(StrategyHub.InvalidAllocation.selector);
        hub.rebalance(6000, 5000);
    }

    // ========== Test: Multiple Rebalances ==========

    function test_multiple_rebalances() public {
        hub.deposit(DEPOSIT_AMOUNT);

        console.log("=== Multiple Rebalances ===");

        // Round 1: 80/20
        hub.rebalance(8000, 2000);
        (uint256 a1, uint256 c1, , ) = hub.getBalances();
        console.log("Round 1 (80/20): Aave=", a1 / 1e6, "Comp=", c1 / 1e6);

        // Round 2: 20/80
        hub.rebalance(2000, 8000);
        (uint256 a2, uint256 c2, , ) = hub.getBalances();
        console.log("Round 2 (20/80): Aave=", a2 / 1e6, "Comp=", c2 / 1e6);

        // Round 3: 50/50
        hub.rebalance(5000, 5000);
        (uint256 a3, uint256 c3, , uint256 total) = hub.getBalances();
        console.log("Round 3 (50/50): Aave=", a3 / 1e6, "Comp=", c3 / 1e6);
        console.log("Total preserved: ", total / 1e6, "USDC");

        // Value should be preserved through all rebalances
        assertApproxEqAbs(total, DEPOSIT_AMOUNT, 1000);
    }
}
