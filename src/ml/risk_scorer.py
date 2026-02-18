"""
XGBoost-based Risk Scoring Model

Classifies DeFi protocols into risk tiers: Low (0-30), Medium (31-70), High (71-100)
"""

import xgboost as xgb
import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit, cross_val_score
from sklearn.metrics import classification_report, confusion_matrix, roc_auc_score
from typing import Tuple, Dict, List
import shap
import joblib


class RiskScorer:
    """
    XGBoost-based protocol risk classification system.
    
    Features 45 dimensions across:
    - Smart Contract Security (15)
    - Protocol Maturity (10)
    - Economic Risks (12)
    - Operational Risks (8)
    
    Risk Tiers:
    - 0 (Low): 0-30 score
    - 1 (Medium): 31-70 score
    - 2 (High): 71-100 score
    """
    
    def __init__(self, config: Dict = None):
        """
        Initialize risk scorer.
        
        Args:
            config: Model configuration dict
        """
        self.config = config or self._default_config()
        self.model = None
        self.feature_names = self._get_feature_names()
        self.explainer = None
        
    def _default_config(self) -> Dict:
        """Default XGBoost configuration."""
        return {
            'objective': 'multi:softmax',
            'num_class': 3,
            'max_depth': 8,
            'learning_rate': 0.05,
            'n_estimators': 500,
            'subsample': 0.8,
            'colsample_bytree': 0.8,
            'eval_metric': 'mlogloss',
            'random_state': 42
        }
    
    def _get_feature_names(self) -> List[str]:
        """Define all 45 feature names."""
        return [
            # Smart Contract Security (15)
            'audit_score', 'num_audits', 'time_since_last_audit',
            'bug_bounty_exists', 'max_bounty_payout', 'code_complexity',
            'external_dependencies', 'upgradeability_score', 'admin_key_count',
            'timelock_duration', 'historical_exploits', 'funds_lost_usd',
            'immunefi_score', 'code_coverage', 'formal_verification',
            
            # Protocol Maturity (10)
            'days_since_deployment', 'cumulative_tvl_days', 'total_transactions',
            'unique_users', 'governance_decentralization', 'dao_maturity',
            'team_doxxed', 'venture_backing_usd', 'insurance_coverage',
            'regulatory_compliance',
            
            # Economic Risks (12)
            'impermanent_loss_max', 'liquidation_risk', 'oracle_reliability',
            'stablecoin_peg_stability', 'collateral_diversity', 'debt_ceiling_util',
            'bad_debt_ratio', 'reserve_ratio', 'token_inflation_rate',
            'sell_pressure_score', 'liquidity_fragmentation', 'mev_exposure',
            
            # Operational Risks (8)
            'exit_liquidity', 'withdrawal_delay', 'dependency_score',
            'bridge_risk', 'centralization_score', 'incident_response_time',
            'community_activity', 'regulatory_scrutiny'
        ]
    
    def train(self, X: pd.DataFrame, y: np.ndarray,
             validation_split: float = 0.2) -> Dict:
        """
        Train the risk scoring model.
        
        Args:
            X: Feature matrix (n_samples, 45 features)
            y: Target labels (0=Low, 1=Medium, 2=High)
            validation_split: Fraction for validation
            
        Returns:
            Training metrics dict
        """
        # Time-series split (no data leakage)
        tscv = TimeSeriesSplit(n_splits=5)
        
        # Initialize model
        self.model = xgb.XGBClassifier(**self.config)
        
        # Split data
        split_idx = int(len(X) * (1 - validation_split))
        X_train, X_val = X[:split_idx], X[split_idx:]
        y_train, y_val = y[:split_idx], y[split_idx:]
        
        # Train with early stopping
        self.model.fit(
            X_train, y_train,
            eval_set=[(X_val, y_val)],
            verbose=False
        )
        
        # Cross-validation score
        cv_scores = cross_val_score(
            self.model, X_train, y_train,
            cv=tscv, scoring='accuracy'
        )
        
        # Validation predictions
        y_pred = self.model.predict(X_val)
        y_pred_proba = self.model.predict_proba(X_val)
        
        # Calculate metrics
        metrics = {
            'accuracy': np.mean(y_pred == y_val),
            'cv_accuracy_mean': np.mean(cv_scores),
            'cv_accuracy_std': np.std(cv_scores),
            'classification_report': classification_report(y_val, y_pred),
            'confusion_matrix': confusion_matrix(y_val, y_pred),
            'roc_auc': self._calculate_multiclass_auc(y_val, y_pred_proba)
        }
        
        # Create SHAP explainer
        self.explainer = shap.TreeExplainer(self.model)
        
        return metrics
    
    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """
        Predict risk tier for protocols.
        
        Args:
            X: Feature matrix
            
        Returns:
            Risk tier predictions (0, 1, or 2)
        """
        if self.model is None:
            raise ValueError("Model not trained. Call train() first.")
        
        return self.model.predict(X)
    
    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        """
        Predict risk tier probabilities.
        
        Args:
            X: Feature matrix
            
        Returns:
            Probability matrix (n_samples, 3)
        """
        if self.model is None:
            raise ValueError("Model not trained. Call train() first.")
        
        return self.model.predict_proba(X)
    
    def calculate_risk_score(self, X: pd.DataFrame) -> np.ndarray:
        """
        Calculate continuous risk scores (0-100).
        
        Args:
            X: Feature matrix
            
        Returns:
            Risk scores (0-100)
        """
        proba = self.predict_proba(X)
        
        # Weighted score: Low=15, Medium=50, High=85
        scores = (proba[:, 0] * 15 +
                 proba[:, 1] * 50 +
                 proba[:, 2] * 85)
        
        return scores
    
    def explain_prediction(self, X: pd.DataFrame, idx: int = 0) -> Dict:
        """
        Explain a single prediction using SHAP values.
        
        Args:
            X: Feature matrix
            idx: Index of sample to explain
            
        Returns:
            Explanation dict
        """
        if self.explainer is None:
            raise ValueError("Model not trained. Call train() first.")
        
        # Calculate SHAP values
        shap_values = self.explainer.shap_values(X.iloc[idx:idx+1])
        
        # Get prediction
        pred_class = self.predict(X.iloc[idx:idx+1])[0]
        pred_proba = self.predict_proba(X.iloc[idx:idx+1])[0]
        risk_score = self.calculate_risk_score(X.iloc[idx:idx+1])[0]
        
        # Top contributing features
        feature_importance = pd.DataFrame({
            'feature': self.feature_names,
            'value': X.iloc[idx].values,
            'shap_value': shap_values[pred_class][0]
        }).sort_values('shap_value', ascending=False, key=abs)
        
        return {
            'risk_tier': ['Low', 'Medium', 'High'][pred_class],
            'risk_score': risk_score,
            'probabilities': {
                'low': pred_proba[0],
                'medium': pred_proba[1],
                'high': pred_proba[2]
            },
            'top_risk_factors': feature_importance.head(10).to_dict('records'),
            'top_safety_factors': feature_importance.tail(10).to_dict('records')
        }
    
    def get_feature_importance(self) -> pd.DataFrame:
        """
        Get global feature importance.
        
        Returns:
            DataFrame with feature importances
        """
        if self.model is None:
            raise ValueError("Model not trained. Call train() first.")
        
        importance = pd.DataFrame({
            'feature': self.feature_names,
            'importance': self.model.feature_importances_
        }).sort_values('importance', ascending=False)
        
        return importance
    
    def save(self, path: str):
        """Save trained model."""
        if self.model is None:
            raise ValueError("No model to save. Train first.")
        
        joblib.dump({
            'model': self.model,
            'config': self.config,
            'feature_names': self.feature_names
        }, path)
        
        print(f"Model saved to: {path}")
    
    def load(self, path: str):
        """Load trained model."""
        data = joblib.load(path)
        self.model = data['model']
        self.config = data['config']
        self.feature_names = data['feature_names']
        self.explainer = shap.TreeExplainer(self.model)
        
        print(f"Model loaded from: {path}")
    
    def _calculate_multiclass_auc(self, y_true: np.ndarray,
                                   y_pred_proba: np.ndarray) -> float:
        """Calculate multiclass AUC (one-vs-rest)."""
        try:
            return roc_auc_score(y_true, y_pred_proba, multi_class='ovr')
        except:
            return 0.0


def create_risk_features(protocol_data: Dict) -> pd.Series:
    """
    Create 45-dimensional feature vector from protocol data.
    
    Args:
        protocol_data: Dict with protocol information
        
    Returns:
        Feature series
    """
    features = {
        # Smart Contract Security
        'audit_score': protocol_data.get('audit_score', 50),
        'num_audits': protocol_data.get('num_audits', 0),
        'time_since_last_audit': protocol_data.get('days_since_audit', 365),
        'bug_bounty_exists': int(protocol_data.get('has_bug_bounty', False)),
        'max_bounty_payout': protocol_data.get('max_bounty_usd', 0),
        'code_complexity': protocol_data.get('sloc', 10000),
        'external_dependencies': protocol_data.get('num_dependencies', 5),
        'upgradeability_score': protocol_data.get('upgradeability', 50),
        'admin_key_count': protocol_data.get('admin_keys', 1),
        'timelock_duration': protocol_data.get('timelock_hours', 0),
        'historical_exploits': protocol_data.get('num_exploits', 0),
        'funds_lost_usd': protocol_data.get('funds_lost', 0),
        'immunefi_score': protocol_data.get('immunefi', 50),
        'code_coverage': protocol_data.get('coverage', 0),
        'formal_verification': int(protocol_data.get('verified', False)),
        
        # Protocol Maturity
        'days_since_deployment': protocol_data.get('age_days', 0),
        'cumulative_tvl_days': protocol_data.get('tvl_days', 0),
        'total_transactions': protocol_data.get('total_txs', 0),
        'unique_users': protocol_data.get('users', 0),
        'governance_decentralization': protocol_data.get('decentralization', 50),
        'dao_maturity': protocol_data.get('dao_age_days', 0),
        'team_doxxed': int(protocol_data.get('team_doxxed', False)),
        'venture_backing_usd': protocol_data.get('funding', 0),
        'insurance_coverage': protocol_data.get('insurance', 0),
        'regulatory_compliance': protocol_data.get('compliance', 50),
        
        # Economic Risks
        'impermanent_loss_max': protocol_data.get('max_il', 0),
        'liquidation_risk': protocol_data.get('liq_risk', 50),
        'oracle_reliability': protocol_data.get('oracle_score', 50),
        'stablecoin_peg_stability': protocol_data.get('peg_stability', 0.001),
        'collateral_diversity': protocol_data.get('collateral_herfindahl', 0.5),
        'debt_ceiling_util': protocol_data.get('debt_util', 0),
        'bad_debt_ratio': protocol_data.get('bad_debt', 0),
        'reserve_ratio': protocol_data.get('reserves', 0),
        'token_inflation_rate': protocol_data.get('inflation', 0),
        'sell_pressure_score': protocol_data.get('sell_pressure', 50),
        'liquidity_fragmentation': protocol_data.get('fragmentation', 50),
        'mev_exposure': protocol_data.get('mev', 50),
        
        # Operational Risks
        'exit_liquidity': protocol_data.get('exit_liquidity', 50),
        'withdrawal_delay': protocol_data.get('withdraw_delay_hours', 0),
        'dependency_score': protocol_data.get('dependencies', 50),
        'bridge_risk': protocol_data.get('bridge_risk', 0),
        'centralization_score': protocol_data.get('centralization', 50),
        'incident_response_time': protocol_data.get('response_hours', 24),
        'community_activity': protocol_data.get('github_commits', 0),
        'regulatory_scrutiny': protocol_data.get('scrutiny', 50)
    }
    
    return pd.Series(features)


if __name__ == '__main__':
    # Example usage
    print("XGBoost Risk Scorer - Example")
    print("=" * 50)
    
    # Generate dummy data for demonstration
    np.random.seed(42)
    n_samples = 500
    
    # Create synthetic features
    X = pd.DataFrame(
        np.random.randn(n_samples, 45),
        columns=RiskScorer()._get_feature_names()
    )
    
    # Create synthetic labels (0=Low, 1=Medium, 2=High)
    y = np.random.choice([0, 1, 2], size=n_samples, p=[0.4, 0.4, 0.2])
    
    # Train model
    scorer = RiskScorer()
    metrics = scorer.train(X, y)
    
    print("\nTraining Metrics:")
    print(f"Accuracy: {metrics['accuracy']:.3f}")
    print(f"CV Accuracy: {metrics['cv_accuracy_mean']:.3f} ± {metrics['cv_accuracy_std']:.3f}")
    print(f"ROC-AUC: {metrics['roc_auc']:.3f}")
    
    print("\nClassification Report:")
    print(metrics['classification_report'])
    
    # Feature importance
    print("\nTop 10 Features:")
    print(scorer.get_feature_importance().head(10))
    
    # Save model
    scorer.save('models/xgboost_risk_scorer_v1.pkl')
    
    print("\nModel training complete!")
