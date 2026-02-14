"""
LSTM-based Yield Forecasting Model

Predicts 7-day ahead APY for DeFi protocols using historical time-series data.
"""

import torch
import torch.nn as nn
import pytorch_lightning as pl
from torch.utils.data import Dataset, DataLoader
import numpy as np
from typing import Tuple, Dict


class YieldDataset(Dataset):
    """
    Time-series dataset for yield prediction.
    
    Args:
        data: Historical protocol data
        lookback_window: Number of days to look back (default: 30)
        prediction_horizon: Number of days to predict ahead (default: 7)
    """
    
    def __init__(self, data: np.ndarray, lookback_window: int = 30, 
                 prediction_horizon: int = 7):
        self.data = data
        self.lookback = lookback_window
        self.horizon = prediction_horizon
        
    def __len__(self) -> int:
        return len(self.data) - self.lookback - self.horizon + 1
    
    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, torch.Tensor]:
        # Input: last lookback_window days of features
        x = self.data[idx:idx + self.lookback]
        
        # Target: Average APY over the prediction_horizon (more stable than single point)
        # Assuming APY is the first feature (index 0)
        y = np.mean(self.data[idx + self.lookback : idx + self.lookback + self.horizon, 0])
        
        return torch.FloatTensor(x), torch.FloatTensor([y])


class YieldForecaster(nn.Module):
    """
    LSTM-based neural network for yield forecasting.
    
    Architecture:
        - 2-layer LSTM (128 hidden units)
        - 1-layer LSTM (64 hidden units)
        - Multi-head attention (4 heads)
        - Fully connected output layer
    
    Args:
        input_size: Number of input features (default: 32)
        hidden_size_1: First LSTM hidden size (default: 128)
        hidden_size_2: Second LSTM hidden size (default: 64)
        num_layers: Number of LSTM layers in first block (default: 2)
        dropout: Dropout rate (default: 0.2)
        num_heads: Attention heads (default: 4)
    """
    
    def __init__(self, input_size: int = 32, hidden_size_1: int = 128,
                 hidden_size_2: int = 64, num_layers: int = 2,
                 dropout: float = 0.2, num_heads: int = 4):
        super().__init__()
        
        self.input_size = input_size
        
        # First LSTM block (deeper)
        self.lstm1 = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size_1,
            num_layers=num_layers,
            dropout=dropout if num_layers > 1 else 0,
            batch_first=True
        )
        
        # Second LSTM block (compressor)
        self.lstm2 = nn.LSTM(
            input_size=hidden_size_1,
            hidden_size=hidden_size_2,
            num_layers=1,
            batch_first=True
        )
        
        # Multi-head attention for temporal importance
        self.attention = nn.MultiheadAttention(
            embed_dim=hidden_size_2,
            num_heads=num_heads,
            batch_first=True
        )
        
        # Output layer
        self.fc = nn.Linear(hidden_size_2, 1)
        
        # Activation
        self.relu = nn.ReLU()
        
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Forward pass.
        
        Args:
            x: Input tensor of shape (batch, sequence_length, features)
            
        Returns:
            Predicted APY of shape (batch, 1)
        """
        # First LSTM: (batch, seq, features) -> (batch, seq, hidden1)
        lstm_out, _ = self.lstm1(x)
        
        # Second LSTM: (batch, seq, hidden1) -> (batch, seq, hidden2)
        lstm_out, _ = self.lstm2(lstm_out)
        
        # Attention: Focus on important time steps
        attn_out, _ = self.attention(lstm_out, lstm_out, lstm_out)
        
        # Take last time step
        last_hidden = attn_out[:, -1, :]  # (batch, hidden2)
        
        # Output prediction
        output = self.fc(last_hidden)  # (batch, 1)
        
        return output


class YieldForecastingModule(pl.LightningModule):
    """
    PyTorch Lightning module for training yield forecaster.
    
    Args:
        model: YieldForecaster instance
        learning_rate: Learning rate for optimizer
        weight_decay: L2 regularization weight
    """
    
    def __init__(self, model: YieldForecaster, learning_rate: float = 1e-3,
                 weight_decay: float = 1e-5):
        super().__init__()
        self.model = model
        self.lr = learning_rate
        self.weight_decay = weight_decay
        
        # Loss functions
        self.mse_loss = nn.MSELoss()
        
        # Save hyperparameters
        self.save_hyperparameters(ignore=['model'])
        
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.model(x)
    
    def training_step(self, batch: Tuple[torch.Tensor, torch.Tensor], 
                     batch_idx: int) -> torch.Tensor:
        x, y = batch
        y_hat = self(x)
        
        # MSE loss
        mse = self.mse_loss(y_hat, y)
        
        # MAPE loss (custom)
        mape = torch.mean(torch.abs((y - y_hat) / (y + 1e-8))) * 100
        
        # Combined loss (70% MSE, 30% directional)
        direction_loss = self._directional_loss(y_hat, y)
        loss = 0.7 * mse + 0.3 * direction_loss
        
        # Logging
        self.log('train_loss', loss, prog_bar=True)
        self.log('train_mse', mse)
        self.log('train_mape', mape)
        
        return loss
    
    def validation_step(self, batch: Tuple[torch.Tensor, torch.Tensor],
                       batch_idx: int) -> torch.Tensor:
        x, y = batch
        y_hat = self(x)
        
        mse = self.mse_loss(y_hat, y)
        mape = torch.mean(torch.abs((y - y_hat) / (y + 1e-8))) * 100
        
        # R² score
        ss_res = torch.sum((y - y_hat) ** 2)
        ss_tot = torch.sum((y - torch.mean(y)) ** 2)
        r2 = 1 - ss_res / (ss_tot + 1e-8)
        
        # Directional accuracy
        dir_acc = self._directional_accuracy(y_hat, y)
        
        self.log('val_loss', mse, prog_bar=True)
        self.log('val_mape', mape, prog_bar=True)
        self.log('val_r2', r2)
        self.log('val_dir_acc', dir_acc)
        
        return mse
    
    def configure_optimizers(self):
        optimizer = torch.optim.AdamW(
            self.parameters(),
            lr=self.lr,
            weight_decay=self.weight_decay
        )
        
        # Learning rate scheduler (reduce on plateau)
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            optimizer,
            mode='min',
            factor=0.5,
            patience=5,
            verbose=True
        )
        
        return {
            'optimizer': optimizer,
            'lr_scheduler': {
                'scheduler': scheduler,
                'monitor': 'val_loss'
            }
        }
    
    def _directional_loss(self, y_pred: torch.Tensor, y_true: torch.Tensor) -> torch.Tensor:
        """
        Penalize incorrect directional predictions.
        
        If we predict APY going up but it actually goes down (or vice versa),
        this is worse than just being off in magnitude.
        """
        # Get direction (1 for up, -1 for down, 0 for flat)
        true_dir = torch.sign(y_true - 0.05)  # Assume baseline 5% APY
        pred_dir = torch.sign(y_pred - 0.05)
        
        # Penalty when directions don't match
        direction_penalty = torch.mean((true_dir - pred_dir) ** 2)
        
        return direction_penalty
    
    def _directional_accuracy(self, y_pred: torch.Tensor, y_true: torch.Tensor) -> torch.Tensor:
        """Calculate percentage of correct directional predictions."""
        true_dir = torch.sign(y_true - 0.05)
        pred_dir = torch.sign(y_pred - 0.05)
        
        correct = (true_dir == pred_dir).float()
        accuracy = torch.mean(correct) * 100
        
        return accuracy


def create_dataloaders(train_data: np.ndarray, val_data: np.ndarray,
                       batch_size: int = 64, lookback: int = 30,
                       horizon: int = 7) -> Tuple[DataLoader, DataLoader]:
    """
    Create training and validation data loaders.
    
    Args:
        train_data: Training dataset (numpy array)
        val_data: Validation dataset (numpy array)
        batch_size: Batch size for training
        lookback: Lookback window in days
        horizon: Prediction horizon in days
        
    Returns:
        Tuple of (train_loader, val_loader)
    """
    train_dataset = YieldDataset(train_data, lookback, horizon)
    val_dataset = YieldDataset(val_data, lookback, horizon)
    
    train_loader = DataLoader(
        train_dataset,
        batch_size=batch_size,
        shuffle=True,
        num_workers=4,
        pin_memory=True
    )
    
    val_loader = DataLoader(
        val_dataset,
        batch_size=batch_size,
        shuffle=False,
        num_workers=4,
        pin_memory=True
    )
    
    return train_loader, val_loader


def train_yield_forecaster(train_data: np.ndarray, val_data: np.ndarray,
                           config: Dict = None) -> YieldForecastingModule:
    """
    Train the yield forecasting model.
    
    Args:
        train_data: Training data (numpy array)
        val_data: Validation data (numpy array)
        config: Training configuration dict
        
    Returns:
        Trained PyTorch Lightning module
    """
    # Default configuration
    if config is None:
        config = {
            'input_size': 32,
            'hidden_size_1': 128,
            'hidden_size_2': 64,
            'num_layers': 2,
            'dropout': 0.2,
            'num_heads': 4,
            'learning_rate': 1e-3,
            'batch_size': 64,
            'max_epochs': 100,
            'lookback_window': 30,
            'prediction_horizon': 7
        }
    
    # Create model
    model = YieldForecaster(
        input_size=config['input_size'],
        hidden_size_1=config['hidden_size_1'],
        hidden_size_2=config['hidden_size_2'],
        num_layers=config['num_layers'],
        dropout=config['dropout'],
        num_heads=config['num_heads']
    )
    
    # Wrap in Lightning module
    pl_module = YieldForecastingModule(
        model=model,
        learning_rate=config['learning_rate']
    )
    
    # Create data loaders
    train_loader, val_loader = create_dataloaders(
        train_data, val_data,
        batch_size=config['batch_size'],
        lookback=config['lookback_window'],
        horizon=config['prediction_horizon']
    )
    
    # Callbacks
    checkpoint_callback = pl.callbacks.ModelCheckpoint(
        dirpath='models/checkpoints',
        filename='lstm_yield_{epoch:02d}_{val_loss:.4f}',
        monitor='val_loss',
        save_top_k=3,
        mode='min'
    )
    
    early_stop_callback = pl.callbacks.EarlyStopping(
        monitor='val_loss',
        patience=10,
        mode='min',
        verbose=True
    )
    
    # Trainer
    trainer = pl.Trainer(
        max_epochs=config['max_epochs'],
        accelerator='auto',  # Use GPU if available
        devices=1,
        callbacks=[checkpoint_callback, early_stop_callback],
        log_every_n_steps=10,
        enable_progress_bar=True
    )
    
    # Train
    trainer.fit(pl_module, train_loader, val_loader)
    
    return pl_module


if __name__ == '__main__':
    # Example usage
    print("LSTM Yield Forecaster - Example")
    print("=" * 50)
    
    # Generate dummy data for demonstration
    np.random.seed(42)
    train_data = np.random.randn(1000, 32)  # 1000 days, 32 features
    val_data = np.random.randn(200, 32)
    
    # Train model
    trained_model = train_yield_forecaster(train_data, val_data)
    
    print("\nModel training complete!")
    print("Saved to: models/checkpoints/")
