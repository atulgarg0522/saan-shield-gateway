import pytest
from decimal import Decimal
from app.services.cost_svc import calculate_cost
from app.services.crypto_svc import encrypt_api_key, decrypt_api_key


@pytest.mark.asyncio
async def test_cryptographic_envelope_encryption():
    """
    Asserts that AES-256 data envelope encryption hides plaintext keys
    and restores them accurately upon decryption.
    """
    raw_key = "sk-proj-super-secret-provider-key-12345"
    
    # Encrypt raw key
    encrypted = encrypt_api_key(raw_key)
    assert encrypted != raw_key
    assert len(encrypted) > len(raw_key)

    # Decrypt key back
    decrypted = decrypt_api_key(encrypted)
    assert decrypted == raw_key


@pytest.mark.asyncio
async def test_high_precision_cost_calculation():
    """
    Asserts that the cost calculation yields exact decimal balances
    and matches database Numeric scale properties.
    """
    # 1. Test gpt-4o:
    # Input rate: $0.005 / 1k, Output rate: $0.015 / 1k
    # Request: 2,000 prompt tokens, 1,000 completion tokens
    # Math: (2000 * 0.005 / 1000) + (1000 * 0.015 / 1000) = $0.010000 + $0.015000 = $0.025000
    cost_gpt4o = await calculate_cost("gpt-4o", 2000, 1000)
    assert cost_gpt4o == Decimal("0.025000")

    # 2. Test gpt-4o-mini:
    # Input rate: $0.000150 / 1k, Output rate: $0.000600 / 1k
    # Request: 10,000 prompt tokens, 5,000 completion tokens
    # Math: (10000 * 0.000150 / 1000) + (5000 * 0.000600 / 1000) = $0.001500 + $0.003000 = $0.004500
    cost_mini = await calculate_cost("gpt-4o-mini", 10000, 5000)
    assert cost_mini == Decimal("0.004500")

    # 3. Test Gemini 3.5 Flash:
    # Input rate: $0.000375 / 1k, Output rate: $0.0015 / 1k
    # Request: 4,000 prompt tokens, 8,000 completion tokens
    # Math: (4000 * 0.000375 / 1000) + (8000 * 0.0015 / 1000) = $0.001500 + $0.012000 = $0.013500
    cost_flash = await calculate_cost("gemini-3-5-flash", 4000, 8000)
    assert cost_flash == Decimal("0.013500")

    # 4. Test missing model default:
    cost_unknown = await calculate_cost("non-existent-model", 1000, 1000)
    assert cost_unknown == Decimal("0.000000")
