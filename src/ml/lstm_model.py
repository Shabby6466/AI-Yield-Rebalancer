"""
LSTM Model for Yield Prediction
Predicts 7-day ahead APY using 30-day historical sequences
"""

import torch
import torch.nn as nn
import numpy as np
from torch.utils.data import Dataset, DataLoader
from typing import Dict, Tuple
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class YieldDataset(Dataset):
    """PyTorch dataset for yield sequences"""
    
    def __init__(self, X: np.ndarray, y: np.ndarray):
        self.X = torch.FloatTensor(X)
        self.y = torch.FloatTensor(y).unsqueeze(1)
        
    def __len__(self):
        return len(self.X)
    
    def __getitem__(self, idx):
        return self.X[idx], self.y[idx]


class YieldLSTM(nn.Module):
    """
    LSTM model for yield forecasting
    
    Architecture:
        - LSTM Layer 1: 128 hidden units, bidirectional
        - LSTM Layer 2: 64 hidden units
        - Dropout: 0.3
        - FC layers with BatchNorm
    """
    
    def __init__(self, input_dim: int, hidden_dim: int = 128, num_layers: int = 2, dropout: float = 0.3):
        super().__init__()
        
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.num_layers = num_layers
        
        # LSTM layers
        self.lstm1 = nn.LSTM(
            input_size=input_dim,
            hidden_size=hidden_dim,
            num_layers=1,
            batch_first=True,
            bidirectional=True,
            dropout=0
        )
        
        self.lstm2 = nn.LSTM(
            input_size=hidden_dim * 2,  # *2 because bidirectional
            hidden_size=hidden_dim // 2,
            num_layers=1,
            batch_first=True,
            dropout=0
        )
        
        self.dropout = nn.Dropout(dropout)
        self.batch_norm = nn.BatchNorm1d(hidden_dim // 2)
        
        # Fully connected layers
        self.fc1 = nn.Linear(hidden_dim // 2, 32)
        self.fc2 = nn.Linear(32, 1)
        
        self.relu = nn.ReLU()
        
    def forward(self, x):
        # x shape: (batch, sequence_length, features)
        
        # LSTM layers
        lstm_out1, _ = self.lstm1(x)
        lstm_out1 = self.dropout(lstm_out1)
        
        lstm_out2, _ = self.lstm2(lstm_out1)
        
        # Take last timestep
        last_hidden = lstm_out2[:, -1, :]
        
        # Batch norm
        last_hidden = self.batch_norm(last_hidden)
        
        # FC layers
        x = self.relu(self.fc1(last_hidden))
        x = self.dropout(x)
        output = self.fc2(x)
        
        return output


class YieldPredictor:
    """Trainer for LSTM model"""
    
    def __init__(self, input_dim: int, device: str = 'cuda' if torch.cuda.is_available() else 'cpu'):
        self.device = device
        self.model = YieldLSTM(input_dim).to(device)
        self.criterion = nn.MSELoss()
        
        logger.info(f"✓ Model initialized on {device}")
        logger.info(f"  Parameters: {sum(p.numel() for p in self.model.parameters()):,}")
        
    def train(self, train_loader: DataLoader, val_loader: DataLoader,
              epochs: int = 50, lr: float = 0.001, patience: int = 10) -> Dict:
        """
        Train the model with early stopping
        """
        
        optimizer = torch.optim.Adam(self.model.parameters(), lr=lr)
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            optimizer, mode='min', factor=0.5, patience=5
        )
        
        best_val_loss = float('inf')
        patience_counter = 0
        history = {'train_loss': [], 'val_loss': []}
        
        logger.info(f"\n{'='*60}")
        logger.info(f"Training LSTM Model")
        logger.info(f"{'='*60}")
        
        for epoch in range(epochs):
            # Training
            self.model.train()
            train_loss = 0
            for X_batch, y_batch in train_loader:
                X_batch = X_batch.to(self.device)
                y_batch = y_batch.to(self.device)
                
                optimizer.zero_grad()
                y_pred = self.model(X_batch)
                loss = self.criterion(y_pred, y_batch)
                loss.backward()
                optimizer.step()
                
                train_loss += loss.item()
            
            train_loss /= len(train_loader)
            
            # Validation
            self.model.eval()
            val_loss = 0
            with torch.no_grad():
                for X_batch, y_batch in val_loader:
                    X_batch = X_batch.to(self.device)
                    y_batch = y_batch.to(self.device)
                    
                    y_pred = self.model(X_batch)
                    loss = self.criterion(y_pred, y_batch)
                    val_loss += loss.item()
            
            val_loss /= len(val_loader)
            
            history['train_loss'].append(train_loss)
            history['val_loss'].append(val_loss)
            
            # Learning rate scheduling
            scheduler.step(val_loss)
            
            # Early stopping
            if val_loss < best_val_loss:
                best_val_loss = val_loss
                patience_counter = 0
                # Save best model
                torch.save(self.model.state_dict(), 'models/lstm_best.pth')
            else:
                patience_counter += 1
            
            if (epoch + 1) % 5 == 0:
                logger.info(f"Epoch {epoch+1:3d}/{epochs} | Train Loss: {train_loss:.4f} | Val Loss: {val_loss:.4f} | Best: {best_val_loss:.4f}")
            
            if patience_counter >= patience:
                logger.info(f"Early stopping at epoch {epoch+1}")
                break
        
        # Load best model
        self.model.load_state_dict(torch.load('models/lstm_best.pth'))
        logger.info(f"\n✓ Training complete. Best val loss: {best_val_loss:.4f}")
        
        return history
    
    def evaluate(self, test_loader: DataLoader) -> Tuple[float, np.ndarray, np.ndarray]:
        """
        Evaluate model on test set
        """
        
        self.model.eval()
        predictions = []
        actuals = []
        
        with torch.no_grad():
            for X_batch, y_batch in test_loader:
                X_batch = X_batch.to(self.device)
                y_pred = self.model(X_batch)
                
                predictions.extend(y_pred.cpu().numpy())
                actuals.extend(y_batch.numpy())
        
        predictions = np.array(predictions).flatten()
        actuals = np.array(actuals).flatten()
        
        # Calculate metrics
        mse = np.mean((predictions - actuals) ** 2)
        rmse = np.sqrt(mse)
        mae = np.mean(np.abs(predictions - actuals))
        mape = np.mean(np.abs((actuals - predictions) / (actuals + 1e-10))) * 100
        
        logger.info(f"\n{'='*60}")
        logger.info(f"Test Set Evaluation")
        logger.info(f"{'='*60}")
        logger.info(f"RMSE: {rmse:.4f}")
        logger.info(f"MAE:  {mae:.4f}")
        logger.info(f"MAPE: {mape:.2f}%")
        
        return rmse, predictions, actuals
    
    def predict(self, X: np.ndarray) -> np.ndarray:
        """
        Make predictions on new data
        """
        
        self.model.eval()
        X_tensor = torch.FloatTensor(X).to(self.device)
        
        with torch.no_grad():
            predictions = self.model(X_tensor)
        
        return predictions.cpu().numpy().flatten()
    
    def save(self, path: str = 'models/lstm_yield_predictor.pth'):
        """Save model"""
        torch.save({
            'model_state_dict': self.model.state_dict(),
            'input_dim': self.model.input_dim,
            'hidden_dim': self.model.hidden_dim,
            'num_layers': self.model.num_layers
        }, path)
        logger.info(f"✓ Model saved to {path}")
    
    def load(self, path: str = 'models/lstm_yield_predictor.pth'):
        """Load model"""
        checkpoint = torch.load(path, map_location=self.device)
        self.model.load_state_dict(checkpoint['model_state_dict'])
        logger.info(f"✓ Model loaded from {path}")


if __name__ == "__main__":
    # Test model architecture
    input_dim = 20  # Number of features
    batch_size = 32
    sequence_length = 30
    
    model = YieldLSTM(input_dim)
    
    # Dummy input
    x = torch.randn(batch_size, sequence_length, input_dim)
    y = model(x)
    
    print(f"\nModel Test:")
    print(f"Input shape: {x.shape}")
    print(f"Output shape: {y.shape}")
    print(f"Parameters: {sum(p.numel() for p in model.parameters()):,}")
