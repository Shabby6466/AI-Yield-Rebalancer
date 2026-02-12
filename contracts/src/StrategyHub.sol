// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";

// ========== External Protocol Interfaces ==========

/// @notice Aave V3 Pool interface (supply/withdraw)
interface IAavePool {
    function supply(address asset, uint256 amount, address onBehalfOf, uint16 referralCode) external;
    function withdraw(address asset, uint256 amount, address to) external returns (uint256);
}

/// @notice Compound V3 Comet interface (supply/withdraw)
interface IComet {
    function supply(address asset, uint256 amount) external;
    function withdraw(address asset, uint256 amount) external;
    function balanceOf(address account) external view returns (uint256);
}

/// @notice Aave aToken interface (for balance check)
interface IAToken {
    function balanceOf(address account) external view returns (uint256);
}

/**
 * @title StrategyHub
 * @notice Routes USDC between Aave V3 and Compound V3 based on AI signals.
 *         This is the execution layer — AI decides, StrategyHub executes.
 * 
 * Flow:
 *   1. Vault deposits USDC into StrategyHub
 *   2. AI Brain computes optimal allocation (e.g., 60% Aave, 40% Compound)
 *   3. Keeper calls rebalance() with the AI's recommended allocation
 *   4. StrategyHub moves funds between protocols
 */
contract StrategyHub is AccessControl, ReentrancyGuard, Pausable {
    using SafeERC20 for IERC20;

    // ============ Roles ============
    bytes32 public constant KEEPER_ROLE = keccak256("KEEPER_ROLE");
    bytes32 public constant VAULT_ROLE = keccak256("VAULT_ROLE");

    // ============ Protocol Addresses (Ethereum Mainnet) ============
    IERC20 public immutable usdc;
    IAavePool public immutable aavePool;
    IAToken public immutable aUsdc;     // Aave's aUSDC (interest-bearing)
    IComet public immutable compoundComet; // Compound V3 USDC market

    // ============ State ============
    uint256 public totalDeposited;
    uint256 public aaveAllocationBps;    // Current Aave allocation in BPS (0-10000)
    uint256 public compoundAllocationBps; // Current Compound allocation in BPS
    uint256 public lastRebalanceTime;
    uint256 public minRebalanceInterval;  // Seconds between rebalances

    // Safety limits
    uint256 public maxSingleMoveBps;     // Max % to move in one rebalance (e.g., 5000 = 50%)
    uint256 public constant MAX_BPS = 10000;

    // ============ Events ============
    event Deposited(address indexed from, uint256 amount);
    event Withdrawn(address indexed to, uint256 amount);
    event Rebalanced(
        uint256 aaveAllocationBps,
        uint256 compoundAllocationBps,
        uint256 aaveBalance,
        uint256 compoundBalance,
        uint256 timestamp
    );
    event EmergencyWithdraw(address indexed token, uint256 amount);

    // ============ Errors ============
    error InvalidAllocation();
    error RebalanceTooSoon();
    error MoveTooLarge();
    error InsufficientBalance();

    constructor(
        address _usdc,
        address _aavePool,
        address _aUsdc,
        address _compoundComet
    ) {
        usdc = IERC20(_usdc);
        aavePool = IAavePool(_aavePool);
        aUsdc = IAToken(_aUsdc);
        compoundComet = IComet(_compoundComet);

        minRebalanceInterval = 6 hours;
        maxSingleMoveBps = 5000; // Max 50% move per rebalance
        aaveAllocationBps = 5000; // Start 50/50
        compoundAllocationBps = 5000;

        _grantRole(DEFAULT_ADMIN_ROLE, msg.sender);
        _grantRole(KEEPER_ROLE, msg.sender);
    }

    // ============ Deposit/Withdraw (Vault → Hub) ============

    /**
     * @notice Vault deposits USDC into the hub
     * @param amount Amount of USDC to deposit
     */
    function deposit(uint256 amount) external nonReentrant whenNotPaused {
        usdc.safeTransferFrom(msg.sender, address(this), amount);
        totalDeposited += amount;

        // Auto-deploy to protocols based on current allocation
        _deployCapital(amount);

        emit Deposited(msg.sender, amount);
    }

    /**
     * @notice Withdraw USDC back to caller (Vault)
     * @param amount Amount to withdraw (use type(uint256).max for all)
     */
    function withdraw(uint256 amount) external nonReentrant {
        uint256 totalVal = totalValue();
        if (amount > totalVal && amount != type(uint256).max) revert InsufficientBalance();
        
        uint256 toWithdraw = (amount == type(uint256).max) ? totalVal : amount;
        uint256 remainingToWithdraw = toWithdraw;

        // 1. Use idle USDC first
        uint256 idle = usdc.balanceOf(address(this));
        if (idle > 0) {
            uint256 takenFromIdle = (remainingToWithdraw > idle) ? idle : remainingToWithdraw;
            remainingToWithdraw -= takenFromIdle;
        }

        // 2. Use funds from protocols if still needed
        if (remainingToWithdraw > 0) {
            uint256 aaveBal = aUsdc.balanceOf(address(this));
            uint256 compBal = compoundComet.balanceOf(address(this));
            uint256 valInProtocols = aaveBal + compBal;

            if (valInProtocols > 0) {
                // Withdraw proportionally from what's available
                uint256 fromAave = (remainingToWithdraw * aaveBal) / valInProtocols;
                uint256 fromCompound = remainingToWithdraw - fromAave;

                if (fromAave > 0) {
                    uint256 actualFromAave = fromAave > aaveBal ? aaveBal : fromAave;
                    aavePool.withdraw(address(usdc), actualFromAave, address(this));
                }
                if (fromCompound > 0) {
                    uint256 actualFromComp = fromCompound > compBal ? compBal : fromCompound;
                    compoundComet.withdraw(address(usdc), actualFromComp);
                }
            }
        }

        // Update state (with underflow protection)
        totalDeposited = (toWithdraw >= totalDeposited) ? 0 : totalDeposited - toWithdraw;
        
        // Final transfer
        usdc.safeTransfer(msg.sender, toWithdraw);

        emit Withdrawn(msg.sender, toWithdraw);
    }

    // ============ Core: Rebalance ============

    /**
     * @notice Rebalance funds between Aave and Compound.
     *         Called by the Keeper with AI Brain's recommended allocation.
     * 
     * @param newAaveBps Target Aave allocation (0-10000)
     * @param newCompoundBps Target Compound allocation (0-10000)
     * 
     * Example: rebalance(7000, 3000) = 70% Aave, 30% Compound
     */
    function rebalance(
        uint256 newAaveBps,
        uint256 newCompoundBps
    ) external onlyRole(KEEPER_ROLE) nonReentrant whenNotPaused {
        // Validate allocation
        if (newAaveBps + newCompoundBps != MAX_BPS) revert InvalidAllocation();
        if (block.timestamp < lastRebalanceTime + minRebalanceInterval) {
            revert RebalanceTooSoon();
        }

        // Calculate how much to move
        uint256 currentAaveBalance = aUsdc.balanceOf(address(this));
        uint256 currentCompoundBalance = compoundComet.balanceOf(address(this));
        uint256 totalVal = currentAaveBalance + currentCompoundBalance;

        if (totalVal == 0) return; // Nothing to rebalance

        uint256 targetAave = (totalVal * newAaveBps) / MAX_BPS;
        uint256 targetCompound = totalVal - targetAave;

        // Move from Aave → Compound if Aave is overweight
        if (currentAaveBalance > targetAave) {
            uint256 moveAmount = currentAaveBalance - targetAave;

            // Safety: cap single move
            uint256 maxMove = (totalVal * maxSingleMoveBps) / MAX_BPS;
            if (moveAmount > maxMove) revert MoveTooLarge();

            // Withdraw from Aave
            aavePool.withdraw(address(usdc), moveAmount, address(this));
            // Supply to Compound
            usdc.approve(address(compoundComet), moveAmount);
            compoundComet.supply(address(usdc), moveAmount);
        }
        // Move from Compound → Aave if Compound is overweight
        else if (currentCompoundBalance > targetCompound) {
            uint256 moveAmount = currentCompoundBalance - targetCompound;

            uint256 maxMove = (totalVal * maxSingleMoveBps) / MAX_BPS;
            if (moveAmount > maxMove) revert MoveTooLarge();

            // Withdraw from Compound
            compoundComet.withdraw(address(usdc), moveAmount);
            // Supply to Aave
            usdc.approve(address(aavePool), moveAmount);
            aavePool.supply(address(usdc), moveAmount, address(this), 0);
        }

        // Update state
        aaveAllocationBps = newAaveBps;
        compoundAllocationBps = newCompoundBps;
        lastRebalanceTime = block.timestamp;

        emit Rebalanced(
            newAaveBps,
            newCompoundBps,
            aUsdc.balanceOf(address(this)),
            compoundComet.balanceOf(address(this)),
            block.timestamp
        );
    }

    // ============ Internal ============

    /**
     * @notice Deploy capital to protocols based on current allocation
     */
    function _deployCapital(uint256 amount) internal {
        uint256 toAave = (amount * aaveAllocationBps) / MAX_BPS;
        uint256 toCompound = amount - toAave;

        if (toAave > 0) {
            usdc.approve(address(aavePool), toAave);
            aavePool.supply(address(usdc), toAave, address(this), 0);
        }

        if (toCompound > 0) {
            usdc.approve(address(compoundComet), toCompound);
            compoundComet.supply(address(usdc), toCompound);
        }
    }

    // ============ View Functions ============

    /**
     * @notice Total value across all protocols (USDC)
     */
    function totalValue() public view returns (uint256) {
        return aUsdc.balanceOf(address(this)) 
             + compoundComet.balanceOf(address(this))
             + usdc.balanceOf(address(this));
    }

    /**
     * @notice Get current balances in each protocol
     */
    function getBalances() external view returns (
        uint256 aaveBalance,
        uint256 compoundBalance,
        uint256 idleBalance,
        uint256 total
    ) {
        aaveBalance = aUsdc.balanceOf(address(this));
        compoundBalance = compoundComet.balanceOf(address(this));
        idleBalance = usdc.balanceOf(address(this));
        total = aaveBalance + compoundBalance + idleBalance;
    }

    // ============ Admin ============

    function setMinRebalanceInterval(uint256 _interval) external onlyRole(DEFAULT_ADMIN_ROLE) {
        minRebalanceInterval = _interval;
    }

    function setMaxSingleMove(uint256 _maxBps) external onlyRole(DEFAULT_ADMIN_ROLE) {
        maxSingleMoveBps = _maxBps;
    }

    function pause() external onlyRole(DEFAULT_ADMIN_ROLE) {
        _pause();
    }

    function unpause() external onlyRole(DEFAULT_ADMIN_ROLE) {
        _unpause();
    }

    /**
     * @notice Emergency: pull all funds back to this contract
     */
    function emergencyWithdrawAll() external onlyRole(DEFAULT_ADMIN_ROLE) {
        _pause();
        
        uint256 aaveBal = aUsdc.balanceOf(address(this));
        if (aaveBal > 0) {
            aavePool.withdraw(address(usdc), type(uint256).max, address(this));
        }
        
        uint256 compBal = compoundComet.balanceOf(address(this));
        if (compBal > 0) {
            compoundComet.withdraw(address(usdc), compBal);
        }

        emit EmergencyWithdraw(address(usdc), usdc.balanceOf(address(this)));
    }
}
