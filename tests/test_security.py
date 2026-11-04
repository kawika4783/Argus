from apps.api.app.security import hash_password, verify_password

def test_argon2_password_hash_round_trip():
    encoded = hash_password("synthetic strong password")
    assert encoded.startswith("$argon2")
    assert verify_password("synthetic strong password", encoded)
    assert not verify_password("wrong password", encoded)
