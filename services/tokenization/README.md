# Tokenization Service

Asset onboarding, AI-driven evaluation, and native Liquid asset issuance through Elements.

## Responsibility

- Accept asset metadata, documents, and valuation inputs
- AI/ML model assessment of risk, projected ROI, market timing
- Interface with Elements RPC to issue Liquid assets on-chain
- Split a single asset token into N fractional units

## Flow

```
User submits asset → Ingestor validates → AI Evaluator scores →
  If approved → Token Issuer issues on Liquid →
    Fractionalization Engine creates tradable units
```

## Technology

Python 3.11+ / FastAPI

## Port

`:8002`
