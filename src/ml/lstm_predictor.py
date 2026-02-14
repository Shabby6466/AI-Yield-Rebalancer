"""
LSTM Yield Prediction Model
Predicts 7-day future APY using 32-dimensional feature vectors
Architecture: 2-layer LSTM with attention mechanism
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
import pytorch_lightning as pl
from typing import Dict, Tuple, Optional
import numpy as np


class AttentionLayer(nn.Module):
    """Attention mechanism for LSTM outputs"""
    
    def __init__(self, hidden_size: int):
        super().__init__()
        self.attention = nn.Linear(hidden_size, 1)
    
    def forward(self, lstm_output):
        # lstm_output shape: (batch, seq_len, hidden_size)
        attention_weights = F.softmax(self.attention(lstm_output), dim=1)
        # attention_weights shape: (batch, seq_len, 1)
        
        # Weighted sum
        context = torch.sum(attention_weights * lstm_output, dim=1)
        # context shape: (batch, hidden_size)
        
        return context, attention_weights


class YieldPredictorLSTM(pl.LightningModule):
    """
    LSTM-based yield predictor with attention
    
    Input: Sequence of feature vectors (batch, seq_len, 32)
    Output: Predicted average APY for next 7 days
    """
    
    def evaluate_model(self, X_test: np.ndarray, y_test: np.ndarray) -> Dict:
        """
        Evaluate model performance on test set
        Detects 'lag' and accuracy metrics.
        """
        self.eval()
        with torch.no_grad():
            inputs = torch.FloatTensor(X_test).to(self.device)
            # YieldPredictorLSTM returns (predictions, attention_weights)
            predictions, _ = self(inputs)
            preds = predictions.cpu().numpy()
            
        mae = np.mean(np.abs(y_test - preds))
        mape = np.mean(np.abs((y_test - preds) / (y_test + 1e-8))) * 100
        correlation = np.corrcoef(y_test, preds)[0, 1]
        
        # Check for 'lag' (is correlation with shifted signal higher?)
        # If corr(y_test, preds) < corr(y_test_shifted, preds), model is lagging
        
        logger.info(f"Evaluation: MAE={mae:.4f}, MAPE={mape:.2f}%, Corr={correlation:.4f}")
        return {
            "mae": float(mae),
            "mape": float(mape),
            "correlation": float(correlation)
        }

    def __init__(
        self,
        input_size: int = 32,
        hidden_size: int = 128,
        num_layers: int = 2,
        dropout: float = 0.3,
        learning_rate: float = 0.001,
    ):
        super().__init__()
        self.save_hyperparameters()
        
        self.input_size = input_size
        self.hidden_size = hidden_size
        self.num_layers = num_layers
        self.learning_rate = learning_rate
        
        # Input projection
        self.input_projection = nn.Sequential(
            nn.Linear(input_size, hidden_size),
            nn.LayerNorm(hidden_size),
            nn.ReLU(),
            nn.Dropout(dropout)
        )
        
        # LSTM layers
        self.lstm = nn.LSTM(
            input_size=hidden_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0,
            bidirectional=False
        )
        
        # Attention mechanism
        self.attention = AttentionLayer(hidden_size)
        
        # Output layers (match target shape with squeeze)
        self.output_layers = nn.Sequential(
            nn.Linear(hidden_size, 64),
            nn.LayerNorm(64),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(64, 32),
            nn.ReLU(),
            nn.Dropout(dropout * 0.5),
            nn.Linear(32, 1)  # Single output: predicted APY
        )
        
        # Loss function
        self.criterion = nn.MSELoss()
    
    def forward(self, x):
        """
        Forward pass
        
        Args:
            x: (batch, seq_len, input_size)
            
        Returns:
            predictions: (batch, 1) predicted APY
            attention_weights: (batch, seq_len, 1)
        """
        batch_size, seq_len, _ = x.shape
        
        # Project input features
        x = self.input_projection(x)  # (batch, seq_len, hidden_size)
        
        # LSTM forward pass
        lstm_out, (h_n, c_n) = self.lstm(x)
        # lstm_out: (batch, seq_len, hidden_size)
        
        # Apply attention
        context, attention_weights = self.attention(lstm_out)
        # context: (batch, hidden_size)
        
        # Output prediction
        predictions = self.output_layers(context).squeeze(-1)
        # predictions: (batch,)
        
        return predictions, attention_weights
    
    def training_step(self, batch, batch_idx):
        """Training step"""
        x, y = batch  # x: features, y: target APY
        
        predictions, _ = self(x)
        loss = self.criterion(predictions.squeeze(), y)
        
        # Calculate MAPE (Mean Absolute Percentage Error)
        with torch.no_grad():
            mape = torch.mean(torch.abs((y - predictions.squeeze()) / (y + 1e-8))) * 100
        
        # Log metrics
        self.log('train_loss', loss, prog_bar=True)
        self.log('train_mape', mape, prog_bar=True)
        
        return loss
    
    def validation_step(self, batch, batch_idx):
        """Validation step"""
        x, y = batch
        
        predictions, attention_weights = self(x)
        loss = self.criterion(predictions.squeeze(), y)
        
        # Calculate metrics
        mape = torch.mean(torch.abs((y - predictions.squeeze()) / (y + 1e-8))) * 100
        mae = torch.mean(torch.abs(y - predictions.squeeze()))
        
        # Log metrics
        self.log('val_loss', loss, prog_bar=True)
        self.log('val_mape', mape, prog_bar=True)
        self.log('val_mae', mae, prog_bar=True)
        
        return {
            'val_loss': loss,
            'val_mape': mape,
            'predictions': predictions,
            'targets': y,
            'attention_weights': attention_weights
        }
    
    def test_step(self, batch, batch_idx):
        """Test step"""
        x, y = batch
        
        predictions, attention_weights = self(x)
        loss = self.criterion(predictions.squeeze(), y)
        
        # Calculate metrics
        mape = torch.mean(torch.abs((y - predictions.squeeze()) / (y + 1e-8))) * 100
        mae = torch.mean(torch.abs(y - predictions.squeeze()))
        rmse = torch.sqrt(torch.mean((y - predictions.squeeze()) ** 2))
        
        # Log metrics
        self.log('test_loss', loss)
        self.log('test_mape', mape)
        self.log('test_mae', mae)
        self.log('test_rmse', rmse)
        
        return {
            'test_loss': loss,
            'test_mape': mape,
            'predictions': predictions,
            'targets': y
        }
    
    def configure_optimizers(self):
        """Configure optimizer and learning rate scheduler"""
        optimizer = torch.optim.Adam(
            self.parameters(),
            lr=self.learning_rate,
            weight_decay=1e-5
        )
        
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            optimizer,
            mode='min',
            factor=0.5,
            patience=5
        )
        
        return {
            'optimizer': optimizer,
            'lr_scheduler': {
                'scheduler': scheduler,
                'monitor': 'train_loss',  # Will be overridden by training script if val available
                'interval': 'epoch',
                'frequency': 1
            }
        }
    
    def predict_apy(
        self,
        features: np.ndarray,
        return_attention: bool = False
    ) -> Tuple[float, Optional[np.ndarray]]:
        """
        Predict APY from feature sequence
        
        Args:
            features: (seq_len, input_size) numpy array
            return_attention: Whether to return attention weights
            
        Returns:
            predicted_apy: Predicted APY value
            attention_weights: Optional attention weights
        """
        self.eval()
        
        with torch.no_grad():
            # Convert to tensor and add batch dimension
            x = torch.FloatTensor(features).unsqueeze(0)  # (1, seq_len, input_size)
            
            # Get prediction
            predictions, attention_weights = self(x)
            
            predicted_apy = predictions.squeeze().item()
            
            if return_attention:
                attn = attention_weights.squeeze().cpu().numpy()
                return predicted_apy, attn
            else:
                return predicted_apy, None


class YieldDataset(torch.utils.data.Dataset):
    """
    Dataset for yield prediction
    Creates sequences of historical features to predict future APY
    """
    
    def __init__(
        self,
        features: np.ndarray,
        targets: np.ndarray,
        sequence_length: int = 14,
    ):
        """
        Args:
            features: (num_samples, input_size) array of features
            targets: (num_samples,) array of target APYs
            sequence_length: Number of time steps to use as input
        """
        self.features = features
        self.targets = targets
        self.sequence_length = sequence_length
        
        # Create sequences
        self.sequences = []
        self.sequence_targets = []
        
        for i in range(len(features) - sequence_length):
            seq = features[i:i + sequence_length]
            target = targets[i + sequence_length]
            
            self.sequences.append(seq)
            self.sequence_targets.append(target)
        
        self.sequences = np.array(self.sequences)
        self.sequence_targets = np.array(self.sequence_targets)
    
    def __len__(self):
        return len(self.sequences)
    
    def __getitem__(self, idx):
        return (
            torch.FloatTensor(self.sequences[idx]),
            torch.FloatTensor([self.sequence_targets[idx]])
        )


def create_dataloaders(
    train_features: np.ndarray,
    train_targets: np.ndarray,
    val_features: np.ndarray,
    val_targets: np.ndarray,
    sequence_length: int = 14,
    batch_size: int = 32,
) -> Tuple[torch.utils.data.DataLoader, torch.utils.data.DataLoader]:
    """
    Create train and validation dataloaders
    
    Args:
        train_features: Training features
        train_targets: Training targets
        val_features: Validation features
        val_targets: Validation targets
        sequence_length: Sequence length for LSTM
        batch_size: Batch size
        
    Returns:
        train_loader, val_loader
    """
    train_dataset = YieldDataset(train_features, train_targets, sequence_length)
    val_dataset = YieldDataset(val_features, val_targets, sequence_length)
    
    train_loader = torch.utils.data.DataLoader(
        train_dataset,
        batch_size=batch_size,
        shuffle=True,
        num_workers=0,
        pin_memory=True
    )
    
    val_loader = torch.utils.data.DataLoader(
        val_dataset,
        batch_size=batch_size,
        shuffle=False,
        num_workers=0,
        pin_memory=True
    )
    
    return train_loader, val_loader
