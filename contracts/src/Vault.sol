// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ERC4626} from "@openzeppelin/contracts/token/ERC20/extensions/ERC4626.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";

/**
 * @title YieldVault
 * @notice ERC4626-compliant vault for DeFi yield optimization
 * @dev Users deposit USDC, receive vault shares, AI optimizes yield across protocols
 */
contract YieldVault is ERC4626, AccessControl, ReentrancyGuard, Pausable {
    using SafeERC20 for IERC20;

    // ============ Roles ============
    bytes32 public constant KEEPER_ROLE = keccak256("KEEPER_ROLE");
    bytes32 public constant STRATEGIST_ROLE = keccak256("STRATEGIST_ROLE");
    bytes32 public constant EMERGENCY_ROLE = keccak256("EMERGENCY_ROLE");

    // ============ State Variables ============
    address public strategyHub;
    uint256 public totalAllocated;
    uint256 public lastHarvestTimestamp;
    uint256 public performanceFee; // basis points (e.g., 1000 = 10%)
    address public feeRecipient;
    
    // Limits
    uint256 public depositLimit;
    uint256 public minDeposit;

    // ============ Events ============
    event StrategyHubUpdated(address indexed oldHub, address indexed newHub);
    event PerformanceFeeUpdated(uint256 oldFee, uint256 newFee);
    event FeeRecipientUpdated(address indexed oldRecipient, address indexed newRecipient);
    event DepositLimitUpdated(uint256 oldLimit, uint256 newLimit);
    event Harvested(uint256 profit, uint256 fee);
    event EmergencyShutdown(address indexed caller);

    // ============ Errors ============
    error DepositLimitExceeded();
    error DepositTooSmall();
    error InvalidStrategyHub();
    error InvalidFeeRecipient();
    error InvalidPerformanceFee();

    /**
     * @notice Constructor
     * @param asset_ Underlying asset (USDC)
     * @param name_ Vault token name
     * @param symbol_ Vault token symbol
     * @param strategyHub_ Address of strategy hub
     */
    constructor(
        IERC20 asset_,
        string memory name_,
        string memory symbol_,
        address strategyHub_
    ) ERC4626(asset_) ERC20(name_, symbol_) {
        if (strategyHub_ == address(0)) revert InvalidStrategyHub();
        
        strategyHub = strategyHub_;
        performanceFee = 1000; // 10%
        feeRecipient = msg.sender;
        depositLimit = 10_000_000e6; // 10M USDC initial limit
        minDeposit = 1000e6; // 1K USDC minimum
        
        _grantRole(DEFAULT_ADMIN_ROLE, msg.sender);
        _grantRole(EMERGENCY_ROLE, msg.sender);
    }

    // ============ Deposit/Withdraw Functions ============

    /**
     * @notice Deposit assets and receive vault shares
     * @param assets Amount of assets to deposit
     * @param receiver Address to receive shares
     */
    function deposit(uint256 assets, address receiver)
        public
        override
        nonReentrant
        whenNotPaused
        returns (uint256)
    {
        if (assets < minDeposit) revert DepositTooSmall();
        if (totalAssets() + assets > depositLimit) revert DepositLimitExceeded();
        
        return super.deposit(assets, receiver);
    }

    /**
     * @notice Mint shares by depositing assets
     * @param shares Amount of shares to mint
     * @param receiver Address to receive shares
     */
    function mint(uint256 shares, address receiver)
        public
        override
        nonReentrant
        whenNotPaused
        returns (uint256)
    {
        uint256 assets = previewMint(shares);
        if (assets < minDeposit) revert DepositTooSmall();
        if (totalAssets() + assets > depositLimit) revert DepositLimitExceeded();
        
        return super.mint(shares, receiver);
    }

    /**
     * @notice Withdraw assets by burning shares
     * @param assets Amount of assets to withdraw
     * @param receiver Address to receive assets
     * @param owner Address that owns the shares
     */
    function withdraw(uint256 assets, address receiver, address owner)
        public
        override
        nonReentrant
        returns (uint256)
    {
        return super.withdraw(assets, receiver, owner);
    }

    /**
     * @notice Redeem shares for assets
     * @param shares Amount of shares to redeem
     * @param receiver Address to receive assets
     * @param owner Address that owns the shares
     */
    function redeem(uint256 shares, address receiver, address owner)
        public
        override
        nonReentrant
        returns (uint256)
    {
        return super.redeem(shares, receiver, owner);
    }

    // ============ Strategy Functions ============

    /**
     * @notice Total assets under management (vault + allocated)
     */
    function totalAssets() public view override returns (uint256) {
        return IERC20(asset()).balanceOf(address(this)) + totalAllocated;
    }

    /**
     * @notice Allocate funds to strategy hub for yield generation
     * @param amount Amount to allocate
     */
    function allocateToStrategy(uint256 amount) external onlyRole(KEEPER_ROLE) {
        IERC20(asset()).safeTransfer(strategyHub, amount);
        totalAllocated += amount;
    }

    /**
     * @notice Deallocate funds from strategy hub
     * @param amount Amount to deallocate
     */
    function deallocateFromStrategy(uint256 amount) external onlyRole(KEEPER_ROLE) {
        // Strategy hub will transfer back
        totalAllocated -= amount;
    }

    /**
     * @notice Harvest profits from strategies
     */
    function harvest() external onlyRole(KEEPER_ROLE) {
        uint256 balanceBefore = IERC20(asset()).balanceOf(address(this));
        
        // Strategy hub sends profits back
        // (Implementation depends on strategy hub interface)
        
        uint256 balanceAfter = IERC20(asset()).balanceOf(address(this));
        uint256 profit = balanceAfter - balanceBefore;
        
        if (profit > 0) {
            // Take performance fee
            uint256 fee = (profit * performanceFee) / 10000;
            if (fee > 0) {
                IERC20(asset()).safeTransfer(feeRecipient, fee);
            }
            
            emit Harvested(profit, fee);
        }
        
        lastHarvestTimestamp = block.timestamp;
    }

    // ============ Admin Functions ============

    /**
     * @notice Update strategy hub address
     * @param newStrategyHub New strategy hub address
     */
    function setStrategyHub(address newStrategyHub) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (newStrategyHub == address(0)) revert InvalidStrategyHub();
        
        address oldHub = strategyHub;
        strategyHub = newStrategyHub;
        
        emit StrategyHubUpdated(oldHub, newStrategyHub);
    }

    /**
     * @notice Update performance fee
     * @param newFee New fee in basis points
     */
    function setPerformanceFee(uint256 newFee) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (newFee > 3000) revert InvalidPerformanceFee(); // Max 30%
        
        uint256 oldFee = performanceFee;
        performanceFee = newFee;
        
        emit PerformanceFeeUpdated(oldFee, newFee);
    }

    /**
     * @notice Update fee recipient
     * @param newRecipient New recipient address
     */
    function setFeeRecipient(address newRecipient) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (newRecipient == address(0)) revert InvalidFeeRecipient();
        
        address oldRecipient = feeRecipient;
        feeRecipient = newRecipient;
        
        emit FeeRecipientUpdated(oldRecipient, newRecipient);
    }

    /**
     * @notice Update deposit limit
     * @param newLimit New deposit limit
     */
    function setDepositLimit(uint256 newLimit) external onlyRole(DEFAULT_ADMIN_ROLE) {
        uint256 oldLimit = depositLimit;
        depositLimit = newLimit;
        
        emit DepositLimitUpdated(oldLimit, newLimit);
    }

    /**
     * @notice Pause deposits
     */
    function pause() external onlyRole(EMERGENCY_ROLE) {
        _pause();
    }

    /**
     * @notice Unpause deposits
     */
    function unpause() external onlyRole(EMERGENCY_ROLE) {
        _unpause();
    }

    /**
     * @notice Emergency shutdown - pause and recall all funds
     */
    function emergencyShutdown() external onlyRole(EMERGENCY_ROLE) {
        _pause();
        
        // Recall all funds from strategy
        // (Implementation depends on strategy hub interface)
        
        emit EmergencyShutdown(msg.sender);
    }

    // ============ View Functions ============

    /**
     * @notice Get vault utilization ratio
     * @return Utilization ratio in basis points
     */
    function getUtilization() external view returns (uint256) {
        uint256 total = totalAssets();
        if (total == 0) return 0;
        return (totalAllocated * 10000) / total;
    }

    /**
     * @notice Get idle assets (not allocated)
     */
    function getIdleAssets() external view returns (uint256) {
        return IERC20(asset()).balanceOf(address(this));
    }
}
