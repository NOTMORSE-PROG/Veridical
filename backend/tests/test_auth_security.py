from app.auth.security import hash_password, verify_password


def test_hash_is_not_the_plaintext_password():
    assert hash_password("correct horse battery staple") != "correct horse battery staple"


def test_verify_accepts_the_correct_password():
    assert verify_password(
        "correct horse battery staple", hash_password("correct horse battery staple")
    )


def test_verify_rejects_a_wrong_password():
    assert not verify_password("wrong password", hash_password("correct horse battery staple"))


def test_verify_rejects_a_corrupted_hash_without_crashing():
    assert not verify_password("anything", "not-an-argon2-hash-at-all")


def test_two_hashes_of_the_same_password_differ():
    """Argon2 salts every hash — this also proves salting is happening."""
    assert hash_password("same password") != hash_password("same password")
